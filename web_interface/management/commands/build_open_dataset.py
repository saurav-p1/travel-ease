"""
Builds a verified destination/restaurant dataset from open data, into
staging CSVs. **This command never touches the database.**

It replaces the two commands that were producing bad data:

    backfill_coordinates   -- free-text geocoding of names that mostly
                              don't name real mapped objects, so ~75% came
                              back "no match"
    backfill_images        -- first-Wikipedia-search-hit, which attached an
                              Ncell logo to "Banepa Market" and an old map
                              to "Banepa Durbar Square", and 429'd on the
                              rest

The replacement runs the other way round. Instead of starting from a list
of invented names and trying to find them in the world, it starts from
what is actually mapped near each of your Locations and brings back the
real ones, each already carrying its own exact coordinate. There is no
geocoding step, so there is nothing for a geocoder to get wrong.

WHAT COMES OUT

    data_templates/open_dataset/locations.csv      (unchanged passthrough)
    data_templates/open_dataset/destinations.csv
    data_templates/open_dataset/restaurants.csv
    data_templates/open_dataset/audit.csv          every candidate + why
    data_templates/open_dataset/summary.txt        per-town coverage report

The three CSVs use exactly the columns `import_data` already reads, so the
import step is your existing, already-tested command:

    python manage.py import_data \
        --locations    data_templates/open_dataset/locations.csv \
        --destinations data_templates/open_dataset/destinations.csv \
        --restaurants  data_templates/open_dataset/restaurants.csv \
        --dry-run

Note: **never** pass --clear. import_data's upsert is keyed on
(name__iexact, location), so re-importing preserves every row's id,
ratings, saved items, submitted_by and URLs. --clear would delete user
submissions.

USAGE

    python manage.py build_open_dataset --dry-run          # 1 town, no files written
    python manage.py build_open_dataset                    # all towns
    python manage.py build_open_dataset --areas Pokhara Kathmandu
    python manage.py build_open_dataset --target 25 --radius-km 15

REQUIRED: a real contact string, via .env TRAVELEASE_CONTACT or --contact.
Overpass and Wikimedia both require clients to identify themselves with a
reachable URL or email. Running without one is what put the previous runs
into the anonymous-scraping rate-limit tier and produced the wall of 429s.
"""
import csv
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from scripts.open_data import (
    HttpClient, RateLimited, assign_to_nearest, build_overpass_query,
    clean_business_hours, completeness_score,
    contact_looks_real, describe_destination, direct_image_from_tags,
    element_key, element_point, fetch_overpass, fetch_wikidata_entities,
    fetch_wikipedia_pages, fits_model_fields, haversine_km, normalise_name,
    parse_wikipedia_tag, pretty_cuisine, price_range_from_tags,
    verified_wikipedia_image,
)
from web_interface.models import Destination, Location, PlaceToEat

OUT_DIR = Path(settings.BASE_DIR) / 'data_templates' / 'open_dataset'

DESTINATION_COLUMNS = ['name', 'description', 'location_name', 'image', 'latitude', 'longitude']
RESTAURANT_COLUMNS = ['name', 'cuisine', 'average_cost', 'rating', 'price_range',
                      'business_hours', 'location_name', 'image', 'latitude', 'longitude']
LOCATION_COLUMNS = ['name', 'latitude', 'longitude', 'property_count']
AUDIT_COLUMNS = ['kind', 'location_name', 'name', 'osm_ref', 'latitude', 'longitude',
                 'distance_km', 'image', 'image_source', 'score', 'decision', 'note']


class Command(BaseCommand):
    help = ('Harvest verified destinations and restaurants from OpenStreetMap, Wikidata and '
            'Wikimedia Commons into staging CSVs. Never writes to the database.')

    def add_arguments(self, parser):
        parser.add_argument('--areas', nargs='+', default=None,
                            help='Only these Location names (default: every Location in the database).')
        parser.add_argument('--target', type=int, default=25,
                            help='How many of each kind to keep per town (default: 25, i.e. a margin over the 20 minimum).')
        parser.add_argument('--radius-km', type=float, default=12.0,
                            help='Catchment radius around each town centre (default: 12).')
        parser.add_argument('--expand-radius-km', type=float, default=30.0,
                            help='If a town is short of --target, retry once at this wider radius (default: 30). '
                                 'Every widened row is labelled with its real distance in audit.csv.')
        parser.add_argument('--contact', default=None,
                            help='Contact URL or email for the User-Agent. Falls back to the '
                                 'TRAVELEASE_CONTACT environment variable.')
        parser.add_argument('--out', default=str(OUT_DIR), help='Output directory.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Harvest the first town only and print what would be written; write no files.')
        parser.add_argument('--min-interval', type=float, default=1.1,
                            help='Minimum seconds between requests to the same host (default: 1.1).')

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        contact = options['contact'] or os.environ.get('TRAVELEASE_CONTACT', '')
        if not contact_looks_real(contact):
            raise CommandError(
                'A real contact URL or email is required before this command will run.\n'
                '  Why: Overpass and Wikimedia both require clients to identify themselves.\n'
                '  Since 2026 Wikimedia sorts requests with a non-compliant User-Agent into a\n'
                '  restrictive rate-limit tier -- that is what produced "HTTP 429 from Wikipedia"\n'
                '  on nearly every line of your last run.\n\n'
                '  Fix: add a line to your .env, e.g.\n'
                '      TRAVELEASE_CONTACT=https://github.com/<you>/travelease\n'
                '  or pass --contact you@example.com'
            )

        locations = list(Location.objects.order_by('name'))
        if options['areas']:
            wanted = {a.lower() for a in options['areas']}
            locations = [l for l in locations if l.name.lower() in wanted]
            missing = wanted - {l.name.lower() for l in locations}
            if missing:
                raise CommandError(f'No Location named: {", ".join(sorted(missing))}')
        if not locations:
            raise CommandError('There are no Locations in the database to harvest around.')

        dry_run = options['dry_run']
        if dry_run:
            locations = locations[:1]
            self.stdout.write(self.style.WARNING(
                f'DRY RUN -- harvesting only {locations[0].name} and writing no files.\n'))

        client = HttpClient(contact=contact, min_interval=options['min_interval'])
        self.stdout.write(f'User-Agent: {client.user_agent}')

        loc_tuples = [(l.name, float(l.latitude), float(l.longitude)) for l in locations]
        target = options['target']

        # ---- 1. Harvest ------------------------------------------------
        # candidates[kind][element_key] = (element, tags, (lat, lon))
        candidates = {'destinations': {}, 'restaurants': {}}
        widened = set()

        for kind in ('destinations', 'restaurants'):
            for name, lat, lon in loc_tuples:
                added, in_radius = self._harvest(client, kind, name, lat, lon,
                                                 options['radius_km'] * 1000, candidates)
                self.stdout.write(f'  {kind:13} {name:16} {in_radius:4d} mapped within '
                                  f'{options["radius_km"]:.0f}km ({added} new to this run)')
                # Widen on how many places exist near THIS town, not on how
                # many were new to the shared pool. Towns are harvested in
                # order and their catchments overlap, so a town processed
                # later legitimately adds almost nothing new while still
                # having plenty nearby -- deciding on `added` would widen
                # the radius for towns that do not need it.
                if in_radius < target and options['expand_radius_km'] > options['radius_km']:
                    _added2, in_radius2 = self._harvest(
                        client, kind, name, lat, lon,
                        options['expand_radius_km'] * 1000, candidates)
                    widened.add((kind, name))
                    self.stdout.write(
                        f'  {kind:13} {name:16} {in_radius2:4d} mapped within '
                        f'{options["expand_radius_km"]:.0f}km (widened -- short of target)')

        # ---- 2. Assign each element to exactly one town ----------------
        # Guarantees the "no place under two locations" requirement
        # structurally, rather than by a dedup pass that can be skipped.
        assignments = {kind: assign_to_nearest(pool, loc_tuples)
                       for kind, pool in candidates.items()}

        # ---- 3. Enrich: Wikidata (P18 photo, P625 coordinate check) ----
        qids = []
        for pool in candidates.values():
            for _el, tags, _pt in pool.values():
                if tags.get('wikidata'):
                    qids.append(tags['wikidata'].strip())
        entities = {}
        if qids:
            self.stdout.write(f'Resolving {len(set(qids))} Wikidata entities '
                              f'({(len(set(qids)) + 49) // 50} batched request(s))...')
            try:
                entities = fetch_wikidata_entities(client, qids)
            except RateLimited as e:
                self.stdout.write(self.style.WARNING(f'  Wikidata unavailable: {e}'))

        # ---- 4. Enrich: Wikipedia lead images, by exact title ----------
        titles = []
        for pool in candidates.values():
            for _el, tags, _pt in pool.values():
                parsed = parse_wikipedia_tag(tags.get('wikipedia'))
                if parsed and parsed[0] == 'en':
                    titles.append(parsed[1])
        pages = {}
        if titles:
            self.stdout.write(f'Resolving {len(set(titles))} Wikipedia articles '
                              f'({(len(set(titles)) + 49) // 50} batched request(s))...')
            try:
                pages = fetch_wikipedia_pages(client, titles)
            except RateLimited as e:
                self.stdout.write(self.style.WARNING(f'  Wikipedia unavailable: {e}'))

        # ---- 5. Build rows ---------------------------------------------
        existing_dest = self._existing_names(Destination)
        existing_rest = self._existing_names(PlaceToEat)

        dest_rows, dest_audit = self._build(
            'destinations', candidates['destinations'], assignments['destinations'],
            entities, pages, target, existing_dest)
        rest_rows, rest_audit = self._build(
            'restaurants', candidates['restaurants'], assignments['restaurants'],
            entities, pages, target, existing_rest)

        audit = dest_audit + rest_audit

        # ---- 6. Report --------------------------------------------------
        summary = self._summary(loc_tuples, dest_rows, rest_rows, target, widened,
                                existing_dest, existing_rest, client)
        self.stdout.write('')
        self.stdout.write(summary)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nDRY RUN -- no files written. Re-run without --dry-run for the full harvest.'))
            return

        out = Path(options['out'])
        out.mkdir(parents=True, exist_ok=True)
        self._write(out / 'locations.csv', LOCATION_COLUMNS, [
            {'name': l.name, 'latitude': l.latitude, 'longitude': l.longitude,
             'property_count': l.property_count} for l in locations])
        self._write(out / 'destinations.csv', DESTINATION_COLUMNS, dest_rows)
        self._write(out / 'restaurants.csv', RESTAURANT_COLUMNS, rest_rows)
        self._write(out / 'audit.csv', AUDIT_COLUMNS, audit)
        (out / 'summary.txt').write_text(summary, encoding='utf-8')

        self.stdout.write(self.style.SUCCESS(
            f'\nWrote {len(dest_rows)} destination(s) and {len(rest_rows)} restaurant(s) to {out}.\n'
            f'Nothing in the database has changed.\n\n'
            f'Next, preview the import (still no writes):\n'
            f'  python manage.py import_data --locations {out}/locations.csv '
            f'--destinations {out}/destinations.csv --restaurants {out}/restaurants.csv --dry-run\n'
        ))

    # ------------------------------------------------------------------
    def _harvest(self, client, kind, name, lat, lon, radius_m, candidates):
        query = build_overpass_query(kind, lat, lon, radius_m)
        try:
            elements = fetch_overpass(client, query)
        except RateLimited as e:
            self.stdout.write(self.style.WARNING(f'  {kind}/{name}: Overpass unavailable -- {e}'))
            return 0, 0
        added = 0
        in_radius = 0
        for element in elements:
            tags = element.get('tags') or {}
            if not (tags.get('name') or '').strip():
                continue
            point = element_point(element)
            if point is None:
                continue
            in_radius += 1
            key = element_key(element)
            if key in candidates[kind]:
                continue
            candidates[kind][key] = (element, tags, point)
            added += 1
        return added, in_radius

    @staticmethod
    def _existing_names(model):
        """{location_name_lower: {normalised_name: pk}} for rows already in
        the database. Used to report which staged rows are new versus which
        will update an existing row in place (keeping its id and ratings)."""
        out = {}
        for pk, name, loc in model.objects.values_list('pk', 'name', 'location__name'):
            out.setdefault((loc or '').lower(), {})[normalise_name(name)] = pk
        return out

    def _build(self, kind, pool, assignment, entities, pages, target, existing):
        """Score, rank, cap at `target` per town, and resolve one image per
        row from identity-linked sources only."""
        by_location = {}
        for key, (element, tags, point) in pool.items():
            if key not in assignment:
                continue
            loc_name, distance = assignment[key]
            by_location.setdefault(loc_name, []).append((key, element, tags, point, distance))

        rows, audit = [], []
        for loc_name, items in sorted(by_location.items()):
            # Best-documented first, nearest as the tie-break.
            items.sort(key=lambda t: (-completeness_score(t[2]), t[4]))
            kept_names = set()
            kept = 0
            for key, element, tags, point, distance in items:
                name = tags['name'].strip()
                norm = normalise_name(name)
                lat, lon = point

                image, image_source, note = self._resolve_image(tags, entities, pages, lat, lon)
                score = completeness_score(tags)

                cuisine = pretty_cuisine(tags) if kind == 'restaurants' else ''
                fits, why = fits_model_fields(name, cuisine, image)

                decision = 'keep'
                if not fits:
                    decision = f'skip: {why}'
                elif norm in kept_names:
                    decision = 'skip: duplicate name in this town'
                elif kept >= target:
                    decision = 'skip: over per-town target'

                if decision == 'keep':
                    kept_names.add(norm)
                    kept += 1
                    if norm in existing.get(loc_name.lower(), {}):
                        note = (note + '; ' if note else '') + 'updates an existing row in place'
                    rows.append(self._row(kind, name, tags, loc_name, lat, lon, image))

                audit.append({
                    'kind': kind, 'location_name': loc_name, 'name': name,
                    'osm_ref': f'https://www.openstreetmap.org/{key}',
                    'latitude': f'{lat:.6f}', 'longitude': f'{lon:.6f}',
                    'distance_km': f'{distance:.2f}',
                    'image': image, 'image_source': image_source,
                    'score': score, 'decision': decision, 'note': note,
                })
        return rows, audit

    @staticmethod
    def _resolve_image(tags, entities, pages, lat, lon):
        """One image per place, from identity-linked sources only, in
        descending order of directness. Every branch here is a link the OSM
        object asserts about itself -- nothing is matched by name, so the
        wrong-photo failure mode is structurally absent.

        Returns (url, source_label, note)."""
        url, source = direct_image_from_tags(tags)
        if url:
            return url, source, ''

        qid = (tags.get('wikidata') or '').strip()
        if qid and qid in entities:
            entity = entities[qid]
            if entity.get('image'):
                note = ''
                # Wikidata's own coordinate is a free cross-check on the
                # OSM position; disagreement means one of the two is
                # mistagged, so flag it rather than silently trusting it.
                if entity.get('lat') is not None:
                    gap = haversine_km(lat, lon, entity['lat'], entity['lon'])
                    if gap > 2.0:
                        note = f'wikidata coordinate disagrees by {gap:.1f}km -- check before publishing'
                return entity['image'], f'wikidata:{qid}', note

        parsed = parse_wikipedia_tag(tags.get('wikipedia'))
        if parsed and parsed[0] == 'en':
            url, reason = verified_wikipedia_image(pages.get(parsed[1]), lat, lon)
            if url:
                return url, f'wikipedia:{parsed[1]}', reason
            return '', '', f'wikipedia rejected: {reason}'

        return '', '', 'no identity-linked photo available -- left blank on purpose'

    @staticmethod
    def _row(kind, name, tags, loc_name, lat, lon, image):
        if kind == 'destinations':
            return {
                'name': name,
                'description': describe_destination(tags, loc_name),
                'location_name': loc_name,
                'image': image,
                'latitude': f'{lat:.6f}',
                'longitude': f'{lon:.6f}',
            }
        return {
            'name': name,
            'cuisine': pretty_cuisine(tags),
            # average_cost and rating are left empty on purpose: OSM is map
            # data, not review data, and import_data accepts both as
            # optional. Inventing a rating would put a fake number in front
            # of users.
            'average_cost': '',
            'rating': '',
            'price_range': price_range_from_tags(tags),
            'business_hours': clean_business_hours(tags),
            'location_name': loc_name,
            'image': image,
            'latitude': f'{lat:.6f}',
            'longitude': f'{lon:.6f}',
        }

    @staticmethod
    def _summary(loc_tuples, dest_rows, rest_rows, target, widened,
                 existing_dest, existing_rest, client):
        def count(rows, loc):
            return sum(1 for r in rows if r['location_name'] == loc)

        def with_image(rows, loc):
            return sum(1 for r in rows if r['location_name'] == loc and r['image'])

        lines = ['Per-town coverage (staged, not yet imported)', '']
        lines.append(f'{"Town":18}{"dest":>6}{"img":>6}{"rest":>7}{"img":>6}   notes')
        short = []
        for name, _lat, _lon in loc_tuples:
            d, r = count(dest_rows, name), count(rest_rows, name)
            notes = []
            if ('destinations', name) in widened or ('restaurants', name) in widened:
                notes.append('widened radius')
            if d < 20 or r < 20:
                notes.append(f'BELOW 20 (dest {d}, rest {r})')
                short.append(name)
            lines.append(f'{name:18}{d:>6}{with_image(dest_rows, name):>6}'
                         f'{r:>7}{with_image(rest_rows, name):>6}   {", ".join(notes)}')
        lines += [
            '',
            f'Totals: {len(dest_rows)} destinations, {len(rest_rows)} restaurants; '
            f'target was {target} per town.',
            f'HTTP: {client.stats["requests"]} requests, {client.stats["retries"]} retries, '
            f'{client.stats["throttled"]} throttled responses.',
        ]
        photoless = sum(1 for r in rest_rows if not r['image'])
        if rest_rows:
            lines += [
                '',
                f'Restaurant photos: {len(rest_rows) - photoless} of {len(rest_rows)} have one.',
                'This number is normally low and that is not a bug. OpenStreetMap is map data;',
                'only a small minority of venues carry an image= or wikimedia_commons= tag, and a',
                'photo is only attached here when the venue itself asserts that link. The three',
                'ways to raise it, all legitimate: tag the photo on the venue in OSM, let the',
                'owner or a user upload one through the submit-a-place form, or leave it blank.',
                'Attaching a stock or name-matched photo is what put an Ncell logo on a market.',
            ]
        if short:
            lines += [
                '',
                'Towns below the 20/20 minimum: ' + ', '.join(short),
                'These are reported, not padded. OpenStreetMap genuinely has fewer mapped',
                'venues in some of these places. Options, in order of honesty:',
                '  1. Raise --expand-radius-km so the town covers its real travel catchment.',
                '  2. Add the missing real places to OpenStreetMap yourself, then re-harvest',
                '     (they are real businesses you can see; this is the intended route).',
                '  3. Accept the lower count and let the UI show what exists.',
                'Inventing rows to reach 20 is what produced the current broken data.',
            ]
        return '\n'.join(lines)

    @staticmethod
    def _write(path, columns, rows):
        with open(path, 'w', newline='', encoding='utf-8') as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
