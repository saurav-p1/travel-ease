"""
Read-only report on the data currently in the database. Writes nothing,
changes nothing, makes no network requests.

Run this before and after any import so you can see exactly what changed
and what is still unsafe to publish.

Each destination/restaurant is graded on two axes:

COORDINATE
    own            has its own latitude/longitude
    town-centre    falls back to the parent Location's centroid (honest,
                   but every place in the town shares one pin)
    suspect        has a coordinate that is implausibly far from its town

IMAGE
    present        has an image URL
    unverified     has an image that came from the old, name-search-based
                   backfill_images run -- i.e. a Wikipedia thumbnail that
                   was never checked against the place's own coordinates.
                   These are the ones that produced an Ncell logo on
                   "Banepa Market". Treat every one as suspect until a
                   human has looked at it.
    none           blank

NAME
    placeholder    the name reads like invented seed copy ("Local Bhatti",
                   "Traditional Villages") rather than a real business or
                   landmark. These can never be geocoded, because there is
                   nothing in any map database to geocode to.

USAGE

    python manage.py audit_places
    python manage.py audit_places --csv audit_before.csv
    python manage.py audit_places --only-problems
"""
import csv

from django.core.management.base import BaseCommand

from scripts.open_data import haversine_km, looks_like_placeholder
from web_interface.models import Destination, PlaceToEat

# Every image the OLD backfill_images saved is a `pithumbsize` thumbnail on
# upload.wikimedia.org. Every image the new pipeline saves is a
# Special:FilePath URL on commons.wikimedia.org or the article's own wiki.
# The two sets therefore live on different hosts, which is what makes
# "was this verified?" a decidable property of a stored row rather than a
# guess -- the old command recorded no provenance at all.
UNVERIFIED_IMAGE_HOSTS = ('upload.wikimedia.org',)
VERIFIED_IMAGE_MARKERS = ('/wiki/Special:FilePath/',)

# Beyond this, a coordinate is almost certainly a same-named place
# elsewhere rather than a real position within the town.
SUSPECT_RADIUS_KM = 25.0

COLUMNS = ['kind', 'id', 'location', 'name', 'name_grade', 'coord_grade',
           'latitude', 'longitude', 'distance_km', 'image_grade', 'image',
           'user_submitted', 'status']


class Command(BaseCommand):
    help = 'Read-only audit of every destination and restaurant. Writes nothing.'

    def add_arguments(self, parser):
        parser.add_argument('--csv', default=None, help='Also write the full table to this CSV path.')
        parser.add_argument('--only-problems', action='store_true',
                            help='Print only rows with a placeholder name, no own coordinate, or an unverified image.')

    def handle(self, *args, **options):
        rows = []
        for kind, model in (('destination', Destination), ('restaurant', PlaceToEat)):
            qs = model.objects.select_related('location').order_by('location__name', 'name')
            for place in qs:
                rows.append(self._grade(kind, place))

        problems = [r for r in rows
                    if r['name_grade'] == 'placeholder'
                    or r['coord_grade'] != 'own'
                    or r['image_grade'] == 'unverified']

        self._print_counts(rows)

        shown = problems if options['only_problems'] else rows
        self.stdout.write('')
        for r in shown:
            flags = []
            if r['name_grade'] == 'placeholder':
                flags.append('PLACEHOLDER-NAME')
            if r['coord_grade'] != 'own':
                flags.append(r['coord_grade'].upper())
            if r['image_grade'] == 'unverified':
                flags.append('UNVERIFIED-IMAGE')
            if r['user_submitted'] == 'yes':
                flags.append('user-submitted -- do not touch')
            self.stdout.write(f"  [{r['kind'][:4]}] {r['location']:14} {r['name'][:38]:38} "
                              f"{' '.join(flags)}")

        if options['csv']:
            with open(options['csv'], 'w', newline='', encoding='utf-8') as fh:
                writer = csv.DictWriter(fh, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            self.stdout.write(self.style.SUCCESS(f"\nFull table written to {options['csv']}."))

        self.stdout.write('')
        if any(r['image_grade'] == 'unverified' for r in rows):
            self.stdout.write(self.style.WARNING(
                'Some rows still carry an image from the old, unverified lookup. Those are the\n'
                'ones that produced wrong photos. Clear them with:\n'
                '    python manage.py backfill_images --clear-unverified --dry-run\n'))
        self.stdout.write('Nothing was modified. This command is read-only.')

    # ------------------------------------------------------------------
    @staticmethod
    def _grade(kind, place):
        loc = place.location
        distance = ''
        coord_grade = 'town-centre'
        if place.latitude is not None and place.longitude is not None:
            coord_grade = 'own'
            if loc.latitude is not None and loc.longitude is not None:
                km = haversine_km(float(loc.latitude), float(loc.longitude),
                                  float(place.latitude), float(place.longitude))
                distance = f'{km:.2f}'
                if km > SUSPECT_RADIUS_KM:
                    coord_grade = 'suspect'

        image = place.image or ''
        if not image:
            image_grade = 'none'
        elif any(marker in image for marker in VERIFIED_IMAGE_MARKERS):
            image_grade = 'verified'
        elif any(host in image for host in UNVERIFIED_IMAGE_HOSTS):
            # Saved by the old search-based backfill. Could be right, could
            # be an Ncell logo -- the old command kept no record of which,
            # which is precisely the gap the new pipeline closes.
            image_grade = 'unverified'
        else:
            # A user-submitted or OSM `image=` URL on some other host. Not
            # produced by the broken command, but not independently checked
            # either, so it is reported as its own class rather than folded
            # into either of the two above.
            image_grade = 'external'

        return {
            'kind': kind,
            'id': place.pk,
            'location': loc.name,
            'name': place.name,
            'name_grade': 'placeholder' if looks_like_placeholder(place.name) else 'plausible',
            'coord_grade': coord_grade,
            'latitude': place.latitude if place.latitude is not None else '',
            'longitude': place.longitude if place.longitude is not None else '',
            'distance_km': distance,
            'image_grade': image_grade,
            'image': image,
            'user_submitted': 'yes' if place.submitted_by_id else 'no',
            'status': place.status,
        }

    def _print_counts(self, rows):
        def tally(kind, field, value):
            return sum(1 for r in rows if r['kind'] == kind and r[field] == value)

        self.stdout.write(self.style.SUCCESS('=== Current database state ==='))
        for kind, label in (('destination', 'Destinations'), ('restaurant', 'Restaurants')):
            total = sum(1 for r in rows if r['kind'] == kind)
            self.stdout.write(
                f'{label}: {total} total | '
                f'own coordinate {tally(kind, "coord_grade", "own")}, '
                f'town-centre fallback {tally(kind, "coord_grade", "town-centre")}, '
                f'suspect {tally(kind, "coord_grade", "suspect")} | '
                f'images: verified {tally(kind, "image_grade", "verified")}, '
                f'external {tally(kind, "image_grade", "external")}, '
                f'unverified {tally(kind, "image_grade", "unverified")}, '
                f'none {tally(kind, "image_grade", "none")} | '
                f'placeholder names {tally(kind, "name_grade", "placeholder")}'
            )
        user_rows = sum(1 for r in rows if r['user_submitted'] == 'yes')
        self.stdout.write(f'User-submitted rows (never modified by any import): {user_rows}')
