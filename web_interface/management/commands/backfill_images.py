"""
Fills in `Destination.image` from Wikipedia/Wikimedia -- but only when the
article can be *proved* to be about that specific place.

WHAT WAS WRONG WITH THE PREVIOUS VERSION

It called the API once per destination with
`generator=search&gsrsearch="<name> <town>"&gsrlimit=1`, and saved the
thumbnail of whatever came back first.

Two consequences, both visible in the last run:

1. Full-text search always returns *something*. "No confident match" was
   therefore almost unreachable, and the command happily attached an
   Ncell logo to "Banepa Market", Nepal's national emblem to "Banepa
   Heritage Walk" and an old map to "Banepa Durbar Square". A wrong photo
   is worse than a blank card, because nobody knows to go and fix it.

2. One HTTP request per destination, with a User-Agent containing no URL
   and no email. Wikimedia's 2026 rate limits sort non-compliant clients
   into a restrictive anonymous tier, so the run collapsed into "HTTP 429
   from Wikipedia" from destination 11 onwards -- and every one of those
   was then logged as "left blank", which reads like "Wikipedia has no
   photo of Boudhanath Stupa". It does. The run was just throttled.

WHAT THIS VERSION DOES INSTEAD

* Identifies itself properly (TRAVELEASE_CONTACT) and batches 50 titles
  per request, so 75 destinations cost 2 requests instead of 75.
* Backs off on 429/503, honouring Retry-After, and reports throttling as
  throttling instead of as "no image".
* Never trusts a search result. An image is accepted only via:
    pass 1  exact article title == destination name, AND the article's own
            coordinates land within --radius-km of the destination's
            coordinate;
    pass 2  Wikipedia geosearch around the destination's own coordinate,
            keeping a nearby article only when its title also matches the
            destination name closely.
  An article with no coordinates is rejected outright -- that single rule
  throws out all three of the bad images above.
* Records nothing it cannot justify: anything unproven stays blank, and
  the reason is printed.

USAGE

    python manage.py backfill_images --dry-run       # show decisions, save nothing
    python manage.py backfill_images
    python manage.py backfill_images --clear-unverified
        # blank out images saved by the OLD command (upload.wikimedia.org
        # URLs on rows nobody has verified), so the site shows an honest
        # placeholder instead of a confidently wrong photo. Prompts first.

NOTE ON SCOPE
Restaurants are still never run through this. A small local business has
no Wikipedia article, so any name match would be a false one. Restaurant
photos come only from a photo tagged on the venue itself in
OpenStreetMap, or from the owner/user upload flow -- see
build_open_dataset.
"""
import difflib
import os

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from scripts.open_data import (
    WIKIPEDIA_API, HttpClient, RateLimited, contact_looks_real,
    fetch_wikipedia_pages, normalise_name, verified_wikipedia_image,
)
from web_interface.models import Destination

# How similar a geosearch article's title must be to the destination name
# before the nearby article is accepted as the same place. 0.72 keeps
# "Boudhanath" / "Boudhanath Stupa" and rejects "Ncell" / "Banepa Market".
TITLE_SIMILARITY = 0.72

# Radius for the geosearch fallback, in metres. A landmark's article
# coordinate and its OSM node are usually within a couple of hundred
# metres; 1.5km is generous without reaching the next landmark.
GEOSEARCH_RADIUS_M = 1500


def title_similarity(a, b):
    return difflib.SequenceMatcher(None, normalise_name(a), normalise_name(b)).ratio()


class Command(BaseCommand):
    help = ("Backfill Destination.image from Wikipedia, accepting a photo only when the "
            "article's own coordinates prove it is the same place.")

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None)
        parser.add_argument('--overwrite', action='store_true',
                            help='Also re-check destinations that already have an image.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print every decision but save nothing.')
        parser.add_argument('--radius-km', type=float, default=5.0,
                            help="Max distance between the article's coordinates and ours (default: 5).")
        parser.add_argument('--contact', default=None,
                            help='Contact URL or email for the User-Agent (or set TRAVELEASE_CONTACT).')
        parser.add_argument('--clear-unverified', action='store_true',
                            help='Blank out images saved by the old, unverified search-based command.')
        parser.add_argument('--yes', action='store_true', help='Skip the confirmation prompt.')

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        if options['clear_unverified']:
            return self._clear_unverified(options)

        contact = options['contact'] or os.environ.get('TRAVELEASE_CONTACT', '')
        if not contact_looks_real(contact):
            raise CommandError(
                'A real contact URL or email is required.\n'
                '  Wikimedia throttles clients whose User-Agent has no contact information --\n'
                '  that is the cause of the "HTTP 429 from Wikipedia" lines in the last run.\n'
                '  Set TRAVELEASE_CONTACT in .env, or pass --contact you@example.com'
            )

        dry_run = options['dry_run']
        radius_km = options['radius_km']

        qs = Destination.objects.select_related('location').order_by('id')
        if not options['overwrite']:
            qs = qs.filter(Q(image__isnull=True) | Q(image=''))
        if options['limit']:
            qs = qs[:options['limit']]
        places = list(qs)

        if not places:
            self.stdout.write('Nothing to do -- every destination already has an image. '
                              '(Use --overwrite to re-check them anyway.)')
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN -- nothing will be saved.\n'))

        client = HttpClient(contact=contact, min_interval=1.1)
        self.stdout.write(f'User-Agent: {client.user_agent}')
        self.stdout.write(f'Checking {len(places)} destination(s) in batches of 50...\n')

        # ---- pass 1: exact titles, batched -----------------------------
        try:
            pages = fetch_wikipedia_pages(client, [p.name for p in places])
        except RateLimited as e:
            raise CommandError(
                f'Wikipedia is still throttling after retries: {e}\n'
                'This is a rate limit, not a lack of photos. Wait a few minutes and re-run.'
            )

        results = {}
        for place in places:
            lat, lon = place.effective_latitude, place.effective_longitude
            own_coord = place.latitude is not None and place.longitude is not None
            url, reason = verified_wikipedia_image(
                pages.get(place.name), lat, lon, radius_km=radius_km)
            if url:
                results[place.pk] = (url, f'exact title match, {reason}')
            elif own_coord:
                results[place.pk] = (None, reason)  # eligible for pass 2
            else:
                # Without its own coordinate we would be verifying against
                # the town centroid, which every place in town shares --
                # that is not verification, so don't pretend it is.
                results[place.pk] = ('', f'{reason}; no own coordinate to verify against '
                                         '(run build_open_dataset first)')

        # ---- pass 2: geosearch, only for places with a real coordinate --
        pending = [p for p in places if results[p.pk][0] is None]
        if pending:
            self.stdout.write(f"{len(pending)} unresolved by exact title; trying geosearch "
                              f"around each one's own coordinate...\n")
        for place in pending:
            try:
                url, reason = self._geosearch(client, place, radius_km)
            except RateLimited as e:
                url, reason = '', f'throttled: {e}'
            results[place.pk] = (url, reason)

        # ---- apply ------------------------------------------------------
        found = blank = 0
        for i, place in enumerate(places, start=1):
            url, reason = results[place.pk]
            url = url or ''
            if url:
                found += 1
                if not dry_run:
                    place.image = url
                    place.save(update_fields=['image'])
                self.stdout.write(f'  [{i}/{len(places)}] {place.name}: ACCEPTED -- {reason}')
            else:
                blank += 1
                self.stdout.write(f'  [{i}/{len(places)}] {place.name}: left blank -- {reason}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. {found} verified image(s), {blank} left blank. '
            f'{client.stats["requests"]} HTTP request(s), '
            f'{client.stats["throttled"]} throttled response(s).'))
        if dry_run:
            self.stdout.write('Re-run without --dry-run to save these.')

    # ------------------------------------------------------------------
    def _geosearch(self, client, place, radius_km):
        """Find articles physically near this destination, then keep one
        only if its title also matches the name. Proximity alone is not
        enough -- a different temple 200m away is still the wrong photo."""
        data = client.get_json(WIKIPEDIA_API, {
            'action': 'query', 'list': 'geosearch',
            'gscoord': f'{float(place.latitude)}|{float(place.longitude)}',
            'gsradius': GEOSEARCH_RADIUS_M, 'gslimit': 10,
            'format': 'json', 'formatversion': '2',
        })
        nearby = ((data.get('query') or {}).get('geosearch') or [])
        if not nearby:
            return '', 'no Wikipedia article anywhere near this coordinate'

        scored = sorted(
            ((title_similarity(place.name, hit.get('title', '')), hit) for hit in nearby),
            key=lambda t: -t[0])
        best_ratio, best = scored[0]
        if best_ratio < TITLE_SIMILARITY:
            return '', (f'nearest article is "{best.get("title")}" '
                        f'(name similarity {best_ratio:.2f} < {TITLE_SIMILARITY}) '
                        f'-- not the same place')

        pages = fetch_wikipedia_pages(client, [best['title']])
        url, reason = verified_wikipedia_image(
            pages.get(best['title']), place.latitude, place.longitude, radius_km=radius_km)
        if url:
            return url, f'geosearch match "{best["title"]}" (similarity {best_ratio:.2f}), {reason}'
        return '', f'geosearch match "{best["title"]}" rejected: {reason}'

    # ------------------------------------------------------------------
    def _clear_unverified(self, options):
        """Remove images that the old search-based command saved. They were
        never verified, so the set contains both correct and badly wrong
        photos with no way to tell them apart from the database alone.
        Blanking them is recoverable (re-run this command to refill, this
        time with proof); leaving a wrong one live is not."""
        qs = Destination.objects.filter(image__contains='upload.wikimedia.org')
        total = qs.count()
        if not total:
            self.stdout.write('No unverified Wikipedia images found.')
            return
        self.stdout.write(f'{total} destination(s) carry an image saved by the old, '
                          f'unverified lookup:')
        for place in qs.select_related('location')[:200]:
            self.stdout.write(f'  [{place.pk}] {place.location.name} / {place.name}')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('\nDRY RUN -- nothing cleared.'))
            return
        if not options['yes']:
            answer = input(f'\nBlank the image on these {total} row(s)? Only the image field '
                           f'is touched; ids, ratings and submissions are untouched. [y/N] ')
            if answer.strip().lower() not in ('y', 'yes'):
                self.stdout.write('Cancelled. Nothing changed.')
                return
        updated = qs.update(image='')
        self.stdout.write(self.style.SUCCESS(
            f'Cleared {updated} image(s). Re-run without --clear-unverified to refill them '
            f'with verified ones.'))
