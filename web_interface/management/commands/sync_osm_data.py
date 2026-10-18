"""
Automates the manual steps `scripts/osm_to_csv.py`'s docstring walks
through by hand -- visit overpass-turbo.eu, paste a query, export JSON,
save the file, run osm_to_csv.py, run import_data -- into one command,
meant to be run on a schedule (cron / systemd timer) so destinations and
restaurants stay in sync with OpenStreetMap without a person re-running
that pipeline manually every time they want fresh data.

WHY NOT QUERY OVERPASS LIVE, ON EVERY PAGE VIEW INSTEAD

Overpass's own usage policy asks users not to send it a fresh query on
every request: it's a shared public service with generous but
rate-limited capacity, and hitting it once per visitor for data that
only actually changes on the order of days/weeks is wasteful and risks
getting an IP throttled. A scheduled batch sync (this command, via cron)
respects that -- and every visitor gets a fast, locally-served database
row instead of an extra network round trip, external-service dependency,
and wait on every single page load.

USAGE

    python manage.py sync_osm_data --area "Pokhara"
    python manage.py sync_osm_data --area "Pokhara" --dry-run
    python manage.py sync_osm_data --area "Kathmandu" --categories restaurants

Example crontab entry to refresh nightly at 3am for every area already
in use (repeat the line per area):

    0 3 * * * cd /path/to/travelease && /path/to/venv/bin/python manage.py sync_osm_data --area "Pokhara" >> /var/log/travelease-osm-sync.log 2>&1

This reuses the exact same CSV-conversion functions as
`scripts/osm_to_csv.py` (`convert`, `merge_location_points`, `write_csv`)
and the exact same upsert logic as `import_data` (via `call_command`,
not a reimplementation) -- so a scheduled sync behaves identically to
someone running the manual pipeline by hand, just without the manual
part.

WHY A SCRATCH DIRECTORY (data_templates/_osm_sync_cache/), NOT
data_templates/ ITSELF

`osm_to_csv.py`'s manual workflow is meant to build up one persistent,
human-curated archive (data_templates/destinations.csv etc.) region by
region over time, appending as you go -- that's the right behavior for
a person occasionally adding a new city by hand. It's the wrong
behavior for something that re-queries the *same* area on a schedule:
appending every night would duplicate every unchanged place forever,
and the obvious-looking fix (skip anything already written) is worse --
it would mean a place Overpass already returned once never gets its row
re-sent to import_data again, so real OSM changes (corrected hours, a
newly tagged photo) would silently stop propagating after the first
sync. This command writes each run's CSVs to their own directory,
completely overwritten every run with exactly that run's current
Overpass results -- no historical accumulation to manage, no dedup
logic that can go stale, and every run still fully reflects OSM's
current state. The database (via `import_data`'s `update_or_create`) is
what actually persists and accumulates across runs, not this file --
which is exactly why re-running this command is always safe.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from scripts.osm_to_csv import convert, merge_location_points, write_csv

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'

# Same category definitions as the manual queries in osm_to_csv.py's
# docstring, kept in sync with those on purpose -- this command and the
# manual fallback should always produce the same shape of data.
QUERIES = {
    'destinations': (
        'node["tourism"~"attraction|museum|viewpoint|artwork|gallery|zoo"](area.searchArea);\n'
        '          way["tourism"~"attraction|museum|viewpoint|artwork|gallery|zoo"](area.searchArea);'
    ),
    'restaurants': (
        'node["amenity"="restaurant"](area.searchArea);\n'
        '          way["amenity"="restaurant"](area.searchArea);'
    ),
}


def build_query(area_name, category):
    return (
        f'[out:json][timeout:60];\n'
        f'area["name"="{area_name}"]->.searchArea;\n'
        f'(\n          {QUERIES[category]}\n        );\n'
        f'out center tags;'
    )


def fetch_overpass(query, timeout=90):
    """POST a query to the public Overpass API and return the parsed
    JSON. Raises CommandError with a clear, actionable message on any
    failure (network error, timeout, rate limiting, bad response) rather
    than letting a traceback reach cron's log with no context."""
    # Overpass answers a raw query body with HTTP 406 (Not Acceptable). It
    # expects the query as a form-encoded `data=` parameter, with the
    # matching Content-Type. It also expects the client to identify itself;
    # unidentified traffic is the first thing a busy instance sheds.
    import os
    import urllib.parse as _urlparse

    from scripts.open_data import build_user_agent

    data = _urlparse.urlencode({'data': query}).encode('utf-8')
    req = urllib.request.Request(
        OVERPASS_URL, data=data, method='POST',
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': build_user_agent(os.environ.get('TRAVELEASE_CONTACT')),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise CommandError(
                'Overpass API rate-limited this request (HTTP 429). '
                'It has generous free capacity but does enforce limits on '
                'how often the same client can query it -- wait a while '
                'before retrying, and make sure this command is only '
                'running on a schedule (e.g. nightly via cron), not on '
                'every page view.'
            )
        raise CommandError(f'Overpass API returned HTTP {e.code}: {e.reason}')
    except urllib.error.URLError as e:
        raise CommandError(f'Could not reach Overpass API ({OVERPASS_URL}): {e.reason}')
    except TimeoutError:
        raise CommandError(
            f'Overpass API did not respond within {timeout}s. Large areas '
            'can be slow -- try a smaller/more specific area name, or '
            'rerun later.'
        )

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise CommandError(f'Overpass API response was not valid JSON: {e}')


def parse_elements(data):
    """Same shape as osm_to_csv.load_elements()'s raw-Overpass-JSON
    branch, since the live API always returns that format (never
    GeoJSON) -- duplicated narrowly here rather than importing a
    file-path-only function, so this command has no file I/O of its own
    until the CSV-write step."""
    results = []
    skipped = 0
    for el in data.get('elements', []):
        tags = el.get('tags') or {}
        lat, lon = el.get('lat'), el.get('lon')
        if lat is None or lon is None:
            center = el.get('center') or {}
            lat, lon = center.get('lat'), center.get('lon')
        if lat is None or lon is None or not tags:
            skipped += 1
            continue
        results.append((lat, lon, tags))
    return results, skipped


class Command(BaseCommand):
    help = ('Fetch current OpenStreetMap data for an area via the Overpass API and '
            'sync it into the database, in one step. Meant to be run on a schedule.')

    def add_arguments(self, parser):
        parser.add_argument('--area', required=True,
                             help='OSM area name to query, e.g. "Pokhara" or "Kathmandu".')
        parser.add_argument('--categories', nargs='+', choices=['destinations', 'restaurants'],
                             default=['destinations', 'restaurants'],
                             help='Which categories to sync (default: both).')
        parser.add_argument('--output', default=str(settings.BASE_DIR / 'data_templates' / '_osm_sync_cache'),
                             help='Directory for this run\'s scratch CSVs (default: '
                                  'data_templates/_osm_sync_cache/ -- separate from '
                                  'data_templates/ itself, see the module docstring for why).')
        parser.add_argument('--dry-run', action='store_true',
                             help='Fetch and convert, but pass --dry-run through to import_data '
                                  '(nothing is actually saved to the database).')

    def handle(self, *args, **options):
        area = options['area']
        categories = options['categories']
        out_dir = Path(options['output'])

        seen_names = set()
        all_points = {}
        dest_rows, rest_rows = [], []

        for i, category in enumerate(categories):
            if i > 0:
                # Be a polite Overpass citizen: don't fire two queries
                # back-to-back with no gap when syncing both categories.
                time.sleep(2)

            self.stdout.write(f'Querying Overpass for {category} in "{area}"...')
            query = build_query(area, category)
            data = fetch_overpass(query)
            elements, skipped = parse_elements(data)
            if skipped:
                self.stdout.write(f'  ({skipped} entries had no coordinates/tags and were skipped)')

            kind = 'destination' if category == 'destinations' else 'restaurant'
            rows, points = convert(elements, area, kind, seen_names)
            for name, pts in points.items():
                all_points.setdefault(name, []).extend(pts)

            if category == 'destinations':
                dest_rows = rows
            else:
                rest_rows = rows
            self.stdout.write(f'  -> {len(rows)} {category} extracted')

        locations_csv = out_dir / 'locations.csv'
        destinations_csv = out_dir / 'destinations.csv'
        restaurants_csv = out_dir / 'restaurants.csv'

        # Every write below uses append=False -- these are scratch files
        # holding only *this run's* fresh query results (see the module
        # docstring's "WHY A SCRATCH DIRECTORY" section), not an
        # accumulating archive. That matters for a command meant to run
        # nightly on the same area: with append=True here, a place
        # Overpass returns identically every night would get written
        # into the CSV again and again forever -- and deduping that by
        # skipping anything already seen would cause a worse problem,
        # silently dropping real OSM updates (corrected hours, a newly
        # tagged photo) since a "seen" place would never be re-sent to
        # import_data at all. Overwriting with exactly this run's
        # current results avoids both: no unbounded growth, and every
        # run still fully reflects OSM's current state, update or not.
        # The database (via import_data's update_or_create right below)
        # is what actually accumulates and persists -- not this file.
        centroids = merge_location_points(all_points, existing_locations_csv=None)
        location_rows = [
            {'name': name, 'latitude': lat, 'longitude': lon, 'property_count': count}
            for name, (lat, lon, count) in sorted(centroids.items())
        ]
        write_csv(locations_csv, ['name', 'latitude', 'longitude', 'property_count'], location_rows, append=False)
        if dest_rows:
            write_csv(destinations_csv, ['name', 'description', 'location_name', 'image', 'latitude', 'longitude'], dest_rows, append=False)
        if rest_rows:
            write_csv(restaurants_csv, ['name', 'cuisine', 'average_cost', 'rating', 'price_range',
                                         'business_hours', 'location_name', 'image', 'latitude', 'longitude'], rest_rows, append=False)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Fetched data for "{area}" -- handing off to import_data...'))
        self.stdout.write('')

        call_command(
            'import_data',
            locations=str(locations_csv),
            destinations=str(destinations_csv),
            restaurants=str(restaurants_csv),
            dry_run=options['dry_run'],
        )

        if 'destinations' in categories and not options['dry_run']:
            self.stdout.write('')
            self.stdout.write('Backfilling photos for any newly-synced destinations without one...')
            call_command('backfill_images')
