"""
Bulk-import real location/destination/restaurant data from CSV files.

This exists so switching from the dummy seed data to a real data provider
(Google Places export, OpenStreetMap/Overpass export, a manually-built
spreadsheet, etc.) is a single safe command instead of hundreds of manual
admin-panel clicks -- and so a messy real-world CSV (missing fields, bad
numbers, typos) can never crash the import or corrupt the database. Bad
rows are skipped with a clear reason and everything else still imports.

USAGE

    # Preview what would happen without touching the database:
    python manage.py import_data --dry-run

    # Import for real (uses data_templates/*.csv by default):
    python manage.py import_data

    # Import from your own files:
    python manage.py import_data --locations path/to/locations.csv \\
        --destinations path/to/destinations.csv \\
        --restaurants path/to/restaurants.csv

    # Wipe all existing Location/Destination/PlaceToEat rows first, then
    # import fresh (useful when fully replacing the dummy data with real
    # data). Requires typing "yes" to confirm, or pass --yes to skip that.
    python manage.py import_data --clear --yes

CSV FORMATS (see data_templates/*.csv for filled-in examples)

    locations.csv:    name, latitude, longitude, property_count
    destinations.csv: name, description, location_name, image, latitude, longitude
    restaurants.csv:  name, cuisine, average_cost, rating, price_range,
                       business_hours, location_name, image, latitude, longitude
                       (average_cost and rating may be left blank -- free
                       real-world data sources like OpenStreetMap are map
                       data, not review data, and rarely have these)

    `latitude`/`longitude` on destinations/restaurants are optional and are
    this specific place's own coordinate (e.g. an OSM node's exact
    position) -- distinct from locations.csv's latitude/longitude, which is
    the wider town/city's centroid used for distance-ranking search. Leave
    them blank and the place falls back to displaying its parent location's
    centroid (via Destination/PlaceToEat.effective_latitude/longitude)
    rather than crashing or showing nothing -- but every place sharing that
    one fallback point is exactly the "get directions always points to the
    same/wrong spot" bug this field exists to fix, so provide a real one
    whenever the data source has it (OSM always does; see osm_to_csv.py and
    sync_osm_data.py, which populate this automatically) or run
    `python manage.py backfill_coordinates` afterward to geocode the rest.

Locations are matched/updated by name (case-insensitive) so re-running the
same import is safe and idempotent -- it won't create duplicates.
Destinations/restaurants are matched to a Location by location_name; if
that location doesn't exist yet, import locations.csv first (or make sure
it's included in the same run -- locations are always processed first).
"""
import csv
import decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from web_interface.models import Location, Destination, PlaceToEat

DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent.parent / 'data_templates'


class _DryRunRollback(Exception):
    """Used to unwind the outer transaction after a dry run, so a dry run
    still does the real inserts/updates (to accurately catch cross-file
    problems, e.g. a destination whose location is defined earlier in the
    same import) but never actually commits them."""
    pass


class Command(BaseCommand):
    help = 'Bulk-import Location/Destination/PlaceToEat data from CSV files.'

    def add_arguments(self, parser):
        parser.add_argument('--locations', default=str(DEFAULT_DIR / 'locations.csv'),
                             help='Path to locations CSV.')
        parser.add_argument('--destinations', default=str(DEFAULT_DIR / 'destinations.csv'),
                             help='Path to destinations CSV.')
        parser.add_argument('--restaurants', default=str(DEFAULT_DIR / 'restaurants.csv'),
                             help='Path to restaurants CSV.')
        parser.add_argument('--dry-run', action='store_true',
                             help='Validate and report what would happen, without writing anything.')
        parser.add_argument('--clear', action='store_true',
                             help='Delete all existing Location/Destination/PlaceToEat rows before importing.')
        parser.add_argument('--yes', action='store_true',
                             help='Skip the confirmation prompt for --clear.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN -- nothing will actually be saved.\n'))

        stats = {'locations': {'created': 0, 'updated': 0, 'skipped': []},
                  'destinations': {'created': 0, 'updated': 0, 'skipped': []},
                  'restaurants': {'created': 0, 'updated': 0, 'skipped': []}}

        try:
            with transaction.atomic():
                if options['clear']:
                    self._handle_clear(options['yes'])
                self._import_locations(options['locations'], stats['locations'])
                self._import_destinations(options['destinations'], stats['destinations'])
                self._import_restaurants(options['restaurants'], stats['restaurants'])
                if dry_run:
                    raise _DryRunRollback()
        except _DryRunRollback:
            pass

        self._print_summary(stats, dry_run)

    # ------------------------------------------------------------------
    def _handle_clear(self, auto_yes):
        counts = (Location.objects.count(), Destination.objects.count(), PlaceToEat.objects.count())
        self.stdout.write(
            f'--clear will delete {counts[0]} locations, {counts[1]} destinations, '
            f'{counts[2]} restaurants (this also removes anything a user has saved or '
            f'recently viewed that points at them -- those records are kept but will '
            f'link to nothing).'
        )
        if not auto_yes:
            answer = input('Type "yes" to continue: ')
            if answer.strip().lower() != 'yes':
                raise CommandError('Aborted.')
        Destination.objects.all().delete()
        PlaceToEat.objects.all().delete()
        Location.objects.all().delete()
        self.stdout.write(self.style.SUCCESS('Cleared existing data.\n'))

    # ------------------------------------------------------------------
    def _read_csv(self, path):
        p = Path(path)
        if not p.exists():
            self.stdout.write(self.style.WARNING(f'  File not found, skipping: {path}'))
            return []
        with p.open(newline='', encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))

    def _clean_str(self, row, field, required=True):
        value = (row.get(field) or '').strip()
        if required and not value:
            raise ValueError(f"'{field}' is required")
        return value

    def _clean_decimal(self, row, field, min_value=None, max_value=None, required=True):
        raw = (row.get(field) or '').strip()
        if not raw:
            if required:
                raise ValueError(f"'{field}' is required")
            return None
        try:
            value = decimal.Decimal(raw)
        except decimal.InvalidOperation:
            raise ValueError(f"'{field}' = {raw!r} is not a valid number")
        if min_value is not None and value < min_value:
            raise ValueError(f"'{field}' = {value} is below the minimum ({min_value})")
        if max_value is not None and value > max_value:
            raise ValueError(f"'{field}' = {value} is above the maximum ({max_value})")
        return value

    def _clean_int(self, row, field, default=0, min_value=None, max_value=None):
        raw = (row.get(field) or '').strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"'{field}' = {raw!r} is not a whole number")
        if min_value is not None and value < min_value:
            raise ValueError(f"'{field}' = {value} is below the minimum ({min_value})")
        if max_value is not None and value > max_value:
            raise ValueError(f"'{field}' = {value} is above the maximum ({max_value})")
        return value

    # ------------------------------------------------------------------
    def _import_locations(self, path, stats):
        rows = self._read_csv(path)
        self.stdout.write(f'Importing locations from {path} ({len(rows)} rows)...')
        for i, row in enumerate(rows, start=2):  # row 1 is the header
            try:
                name = self._clean_str(row, 'name')
                latitude = self._clean_decimal(row, 'latitude', min_value=-90, max_value=90)
                longitude = self._clean_decimal(row, 'longitude', min_value=-180, max_value=180)
                property_count = self._clean_int(row, 'property_count', default=0, min_value=0)
            except ValueError as e:
                stats['skipped'].append(f'row {i} ({row.get("name", "?")!r}): {e}')
                continue

            try:
                with transaction.atomic():
                    obj, created = Location.objects.update_or_create(
                        name__iexact=name,
                        defaults={'name': name, 'latitude': latitude, 'longitude': longitude,
                                  'property_count': property_count},
                    )
            except Exception as e:
                stats['skipped'].append(f'row {i} ({name!r}): database rejected this row: {e}')
                continue
            stats['created' if created else 'updated'] += 1

    def _clean_url(self, row, field):
        """Optional URL column. Never raises -- a malformed or missing
        URL just means no image, not a reason to skip the whole row."""
        value = (row.get(field) or '').strip()
        if not value:
            return ''
        if not (value.startswith('http://') or value.startswith('https://')):
            return ''
        return value

    def _clean_optional_coord(self, row, field, min_value, max_value):
        """Like _clean_decimal but never raises -- an unparsable or
        out-of-range destination/restaurant-level coordinate just means
        "no coordinate of its own yet" (falls back to the parent
        Location's centroid via effective_latitude/longitude), not a
        reason to skip importing the whole place."""
        raw = (row.get(field) or '').strip()
        if not raw:
            return None
        try:
            value = decimal.Decimal(raw)
        except decimal.InvalidOperation:
            return None
        if value < min_value or value > max_value:
            return None
        return value

    def _import_destinations(self, path, stats):
        rows = self._read_csv(path)
        self.stdout.write(f'Importing destinations from {path} ({len(rows)} rows)...')
        for i, row in enumerate(rows, start=2):
            try:
                name = self._clean_str(row, 'name')
                description = self._clean_str(row, 'description', required=False)
                location_name = self._clean_str(row, 'location_name')
                image = self._clean_url(row, 'image')
                latitude = self._clean_optional_coord(row, 'latitude', -90, 90)
                longitude = self._clean_optional_coord(row, 'longitude', -180, 180)
            except ValueError as e:
                stats['skipped'].append(f'row {i} ({row.get("name", "?")!r}): {e}')
                continue

            location = Location.objects.filter(name__iexact=location_name).first()
            if location is None:
                stats['skipped'].append(
                    f'row {i} ({name!r}): no matching location named {location_name!r} '
                    f'-- import that location first'
                )
                continue

            try:
                with transaction.atomic():
                    defaults = {'name': name, 'description': description}
                    # Only touch `image` when this row actually has one --
                    # otherwise re-importing/re-syncing from a source with
                    # no image column (e.g. the static seed CSVs) would
                    # silently wipe out an image backfill_images already
                    # found and saved for this same destination.
                    if image:
                        defaults['image'] = image
                    # Same reasoning for latitude/longitude: only set them
                    # when this row actually has a value, so re-running an
                    # import from a source with no coordinate column never
                    # wipes out a real coordinate backfill_coordinates (or
                    # an earlier OSM sync) already found and saved.
                    if latitude is not None and longitude is not None:
                        defaults['latitude'] = latitude
                        defaults['longitude'] = longitude
                    obj, created = Destination.objects.update_or_create(
                        name__iexact=name, location=location,
                        defaults=defaults,
                    )
            except Exception as e:
                stats['skipped'].append(f'row {i} ({name!r}): database rejected this row: {e}')
                continue
            stats['created' if created else 'updated'] += 1

    def _import_restaurants(self, path, stats):
        rows = self._read_csv(path)
        self.stdout.write(f'Importing restaurants from {path} ({len(rows)} rows)...')
        for i, row in enumerate(rows, start=2):
            try:
                name = self._clean_str(row, 'name')
                cuisine = self._clean_str(row, 'cuisine', required=False)
                # average_cost/rating are optional -- free real-world data
                # sources (e.g. OpenStreetMap) are map data, not review
                # data, and essentially never include these commercial
                # fields. Requiring them would skip almost every real row.
                average_cost = self._clean_decimal(row, 'average_cost', min_value=0, required=False)
                rating = self._clean_decimal(row, 'rating', min_value=0, max_value=5, required=False)
                price_range = self._clean_int(row, 'price_range', default=1, min_value=1, max_value=4)
                business_hours = self._clean_str(row, 'business_hours', required=False)
                location_name = self._clean_str(row, 'location_name')
                image = self._clean_url(row, 'image')
                latitude = self._clean_optional_coord(row, 'latitude', -90, 90)
                longitude = self._clean_optional_coord(row, 'longitude', -180, 180)
            except ValueError as e:
                stats['skipped'].append(f'row {i} ({row.get("name", "?")!r}): {e}')
                continue

            location = Location.objects.filter(name__iexact=location_name).first()
            if location is None:
                stats['skipped'].append(
                    f'row {i} ({name!r}): no matching location named {location_name!r} '
                    f'-- import that location first'
                )
                continue

            try:
                with transaction.atomic():
                    defaults = {'name': name, 'cuisine': cuisine,
                                'seed_average_cost': average_cost,
                                'seed_rating': rating, 'price_range': price_range,
                                'business_hours': business_hours}
                    if image:  # see the matching comment in _import_destinations
                        defaults['image'] = image
                    if latitude is not None and longitude is not None:  # ditto
                        defaults['latitude'] = latitude
                        defaults['longitude'] = longitude
                    obj, created = PlaceToEat.objects.update_or_create(
                        name__iexact=name, location=location,
                        defaults=defaults,
                    )
                    # seed_rating/seed_average_cost just changed -- recompute
                    # the displayed rating/average_cost as the blend of this
                    # seed with any real user submissions that already exist
                    # (e.g. if this restaurant is being re-imported after
                    # users have already rated it).
                    obj.recompute_aggregates()
            except Exception as e:
                stats['skipped'].append(f'row {i} ({name!r}): database rejected this row: {e}')
                continue
            stats['created' if created else 'updated'] += 1

    # ------------------------------------------------------------------
    def _print_summary(self, stats, dry_run):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== Import summary ==='))
        for label, key in [('Locations', 'locations'), ('Destinations', 'destinations'),
                            ('Restaurants', 'restaurants')]:
            s = stats[key]
            verb = 'would be created' if dry_run else 'created'
            verb2 = 'would be updated' if dry_run else 'updated'
            self.stdout.write(f'{label}: {s["created"]} {verb}, {s["updated"]} {verb2}, '
                               f'{len(s["skipped"])} skipped')
            for reason in s['skipped']:
                self.stdout.write(self.style.WARNING(f'  - {reason}'))
        self.stdout.write('')
        if dry_run:
            self.stdout.write('Re-run without --dry-run to actually apply these changes.')
        else:
            self.stdout.write(self.style.SUCCESS('Done.'))
