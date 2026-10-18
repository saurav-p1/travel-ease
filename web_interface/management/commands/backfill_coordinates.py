"""
Fills in `Destination.latitude`/`longitude` and `PlaceToEat.latitude`/
`longitude` for places that don't have their own coordinate yet -- i.e.
everything created before those fields existed (the static seed data from
populate_data.py / add_pokhara_data.py / add_nepal_wide_data.py, and any
older CSV import that didn't include a latitude/longitude column).

WHY THIS EXISTS

Destination and PlaceToEat only ever had a ForeignKey to Location, never a
coordinate of their own. Every destination/restaurant in the same town or
city therefore rendered on the exact same map pin and resolved to the
exact same "Get directions" target -- the parent Location's single,
shared centroid -- regardless of how far apart the actual places are.
That's what effective_latitude/effective_longitude's fallback masks (see
web_interface/models.py) so nothing crashes or shows a blank map, but the
underlying problem is that most rows simply never had their own real
position on file. This command finds one.

HOW

For each place missing its own coordinate, this searches OpenStreetMap's
free, keyless Nominatim geocoder for "<name>, <location>, Nepal" and takes
the top result -- but only if it's a plausible match: within
SANITY_RADIUS_KM of the parent Location's own centroid. A generic/common
name (e.g. "City Market") can easily match an unrelated place somewhere
else entirely; accepting a distant "top result" would replace one
inaccurate coordinate (the shared centroid) with a different, confidently
-wrong one, which is worse than leaving it as an honest, clearly-labeled
estimate. Anything that doesn't clear that bar -- no match, malformed
response, network failure -- is left alone rather than guessed at, the
same philosophy backfill_images.py uses for photos. effective_latitude/
longitude's town-centroid fallback (and the "(town-center estimate)"
label next to it in travel_detail.html) keeps working for anything this
command couldn't confidently resolve.

WHY NOMINATIM, WHY NOT GEOCODE LIVE ON EVERY PAGE VIEW

Same reasoning as sync_osm_data.py's choice not to query Overpass live:
Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
caps public API use at 1 request/second and asks for a real, identifying
User-Agent -- it's meant for occasional/batch lookups like this, not a
per-request dependency on every visitor's page load. This command is
meant to be run once after a fresh import of static/dummy data, or
periodically (e.g. chained after sync_osm_data in the nightly cron --
though a proper OSM sync populates its own per-element coordinates
directly via osm_to_csv.py's `convert()`, so this is mainly for filling
gaps: hand-entered CSV rows, or the original seed data).

USAGE

    python manage.py backfill_coordinates
    python manage.py backfill_coordinates --limit 20      # quick test
    python manage.py backfill_coordinates --overwrite     # re-check places that already have one
    python manage.py backfill_coordinates --types destination   # only destinations (or: restaurant)
    python manage.py backfill_coordinates --dry-run       # report what would happen, save nothing

Safe to run repeatedly -- only places with no coordinate of their own are
touched unless --overwrite is passed, and anything left unresolved this
run is simply tried again (or eventually hand-fixed) next time.
"""
import difflib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from django.core.management.base import BaseCommand
from django.db.models import Q

from scripts.open_data import build_user_agent, contact_looks_real, normalise_name
from web_interface.models import Destination, PlaceToEat

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'

# Nominatim's own usage policy requires a real, identifying User-Agent --
# unidentified traffic can be blocked outright. Same requirement, same
# reasoning as backfill_images.py's Wikipedia User-Agent.
# Built from TRAVELEASE_CONTACT so the User-Agent carries a reachable URL
# or email, as Nominatim's usage policy requires. The old hard-coded string
# named no contact at all, which is the kind of unidentified traffic that
# gets throttled or blocked outright.
USER_AGENT = build_user_agent(os.environ.get('TRAVELEASE_CONTACT'))

# How closely Nominatim's returned display name has to resemble the name we
# searched for before the coordinate is believed.
#
# WHY THIS EXISTS: the previous version accepted the top result as long as
# it was within SANITY_RADIUS_KM of the town. That is not an identity check
# -- it only rules out a same-named place in another district. It is why
# "Highway Restaurant" (an invented seed name) was given the coordinate of
# an unrelated roadside feature near Banepa: the point was close enough, so
# nothing questioned whether it was the same thing. Requiring the returned
# name to actually resemble the query catches that.
NAME_SIMILARITY = 0.62

# How far a geocoded result is allowed to be from the parent Location's
# own centroid before it's trusted. A real destination/restaurant is
# always going to be within its own town/city, not the far side of the
# country -- Nepal's towns are close enough together (and Nominatim's
# search is loose enough with generic names) that an unbounded "just take
# the top result" would confidently attach some places to a same-named
# spot in a completely different district. 25km comfortably covers a
# large town/city's full extent without being loose enough to accept a
# wrong match in a neighboring one.
SANITY_RADIUS_KM = 25

# 1 request/second, per Nominatim's usage policy.
REQUEST_INTERVAL_S = 1.0


def haversine_km(lat1, lon1, lat2, lon2):
    import math
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode(name, area):
    """Return (lat, lon) for the top Nominatim match of "name, area,
    Nepal", or (None, reason) if nothing came back, the response was
    unusable, or the request itself failed (network down, blocked,
    timeout). Never raises -- one place failing to geocode shouldn't stop
    the whole run, same philosophy as backfill_images.search_thumbnail.
    The reason string distinguishes a real empty search result from a
    network-level failure -- these look identical from the caller's
    perspective otherwise, which previously made a blocked/unreachable
    network indistinguishable from Nominatim genuinely having no match."""
    params = {
        'q': f'{name}, {area}, Nepal',
        'format': 'jsonv2',
        # Ask for several candidates rather than one, so the name check
        # below can pick a matching result instead of being stuck with
        # whatever Nominatim happened to rank first.
        'limit': 5,
        'countrycodes': 'np',
        'namedetails': 1,
    }
    url = f'{NOMINATIM_URL}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code} from Nominatim'
    except (urllib.error.URLError, TimeoutError) as e:
        return None, f'network error reaching Nominatim: {e}'
    except json.JSONDecodeError:
        return None, 'Nominatim returned unparseable data'

    if not data:
        return None, 'no match'

    best = None
    for hit in data:
        try:
            lat, lon = float(hit['lat']), float(hit['lon'])
        except (KeyError, ValueError, TypeError):
            continue
        candidate_names = [
            (hit.get('namedetails') or {}).get('name') or '',
            (hit.get('display_name') or '').split(',')[0],
        ]
        ratio = max(
            difflib.SequenceMatcher(None, normalise_name(name), normalise_name(c)).ratio()
            for c in candidate_names
        )
        if best is None or ratio > best[0]:
            best = (ratio, lat, lon, candidate_names[0] or candidate_names[1])

    if best is None:
        return None, 'malformed result from Nominatim'
    ratio, lat, lon, matched = best
    if ratio < NAME_SIMILARITY:
        return None, (f'best result was {matched!r} (name similarity {ratio:.2f} < '
                      f'{NAME_SIMILARITY}) -- not the same place, not trusted')
    return (lat, lon), None


class Command(BaseCommand):
    help = ("Geocode real per-place coordinates (via OpenStreetMap Nominatim) for "
            "destinations/restaurants that only have their parent Location's shared centroid.")

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None,
                             help='Only process this many places (default: all of them).')
        parser.add_argument('--overwrite', action='store_true',
                             help='Also re-check places that already have their own coordinate (default: skip them).')
        parser.add_argument('--types', nargs='+', choices=['destination', 'restaurant'],
                             default=['destination', 'restaurant'],
                             help='Which kinds of place to process (default: both).')
        parser.add_argument('--dry-run', action='store_true',
                             help="Report what would happen but don't save anything.")

    def handle(self, *args, **options):
        if not contact_looks_real(os.environ.get('TRAVELEASE_CONTACT')):
            self.stdout.write(self.style.WARNING(
                'No TRAVELEASE_CONTACT set, so this run is not identifying itself properly to '
                'Nominatim. Add TRAVELEASE_CONTACT=<your url or email> to .env.\n'))

        self.stdout.write(self.style.WARNING(
            'NOTE: this command can only find places that already exist under that exact name in\n'
            'OpenStreetMap. Most of the seed rows do not ("Local Bhatti", "Traditional Villages"),\n'
            'which is why ~75%% of a full run reports "no match". For real coverage use:\n'
            '    python manage.py build_open_dataset --dry-run\n'
            'which starts from what is actually mapped near each town instead of from a name.\n'))

        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN -- nothing will actually be saved.\n'))

        models = []
        if 'destination' in options['types']:
            models.append(('destination', Destination))
        if 'restaurant' in options['types']:
            models.append(('restaurant', PlaceToEat))

        found_total, unresolved_total, rejected_total = 0, 0, 0
        first_request = True

        for label, Model in models:
            qs = Model.objects.select_related('location').order_by('id')
            if not options['overwrite']:
                qs = qs.filter(Q(latitude__isnull=True) | Q(longitude__isnull=True))
            if options['limit']:
                qs = qs[:options['limit']]

            total = qs.count()
            if total == 0:
                self.stdout.write(f'Nothing to do for {label}s -- every one already has its own coordinate. '
                                   '(Use --overwrite to re-check them anyway.)')
                continue

            self.stdout.write(f'Geocoding {total} {label}(s) via Nominatim...')
            for i, place in enumerate(qs, start=1):
                if not first_request:
                    time.sleep(REQUEST_INTERVAL_S)
                first_request = False

                result, reason = geocode(place.name, place.location.name)
                if result is None:
                    unresolved_total += 1
                    self.stdout.write(f'  [{i}/{total}] {place.name}: {reason} -- left as-is '
                                       '(falls back to town-center estimate)')
                    continue

                lat, lon = result
                loc_lat, loc_lon = place.location.latitude, place.location.longitude
                if loc_lat is not None and loc_lon is not None:
                    distance = haversine_km(float(loc_lat), float(loc_lon), lat, lon)
                    if distance > SANITY_RADIUS_KM:
                        rejected_total += 1
                        self.stdout.write(
                            f'  [{i}/{total}] {place.name}: top match was {distance:.0f}km from '
                            f'{place.location.name} -- almost certainly a same-named place '
                            f'elsewhere, left as-is rather than trusted'
                        )
                        continue

                if not dry_run:
                    place.latitude = round(lat, 6)
                    place.longitude = round(lon, 6)
                    place.save(update_fields=['latitude', 'longitude'])
                found_total += 1
                self.stdout.write(f'  [{i}/{total}] {place.name}: {lat:.6f}, {lon:.6f}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. {found_total} place(s) got a real coordinate, {unresolved_total} left as-is '
            f'(no confident match), {rejected_total} rejected (matched somewhere implausibly far away).'
        ))
        if dry_run and found_total:
            self.stdout.write('Re-run without --dry-run to actually save these.')
