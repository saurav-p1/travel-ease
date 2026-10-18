"""
Convert an OpenStreetMap Overpass API export into the CSV files
web_interface's `import_data` management command expects.

WHY OPENSTREETMAP

It's the recommended free data source for this project:
  - Completely free, no API key, no billing, no credit card, no signup.
    (Unlike Google Places, which now requires billing to be enabled even
    to stay within its free tier, and which changed its free-tier terms
    more than once in the last couple of years.)
  - Full, current coverage of Nepal, including remote areas.
  - Licensed under the Open Database License (ODbL) -- free to use
    commercially, the only requirement is crediting "(c) OpenStreetMap
    contributors" somewhere in the app (e.g. the footer).

HOW TO GET THE RAW DATA (you do this part yourself, in a browser)

  1. Go to https://overpass-turbo.eu/ (free, no signup).
  2. Paste a query. Two ready-to-use examples -- edit the area name for
     whichever city/region you want data for:

     Tourist attractions in Pokhara:
         [out:json][timeout:60];
         area["name"="Pokhara"]->.searchArea;
         (
           node["tourism"~"attraction|museum|viewpoint|artwork|gallery|zoo"](area.searchArea);
           way["tourism"~"attraction|museum|viewpoint|artwork|gallery|zoo"](area.searchArea);
         );
         out center tags;

     Restaurants in Pokhara:
         [out:json][timeout:60];
         area["name"="Pokhara"]->.searchArea;
         (
           node["amenity"="restaurant"](area.searchArea);
           way["amenity"="restaurant"](area.searchArea);
         );
         out center tags;

  3. Click "Run", then "Export" -> "download as GeoJSON" (or copy the raw
     JSON from the "Data" tab -- this script accepts either format).
  4. Repeat per city/region you care about, saving each export as its own
     file (e.g. pokhara_attractions.json, pokhara_restaurants.json).

USAGE

    python scripts/osm_to_csv.py \
        --location "Pokhara" \
        --attractions pokhara_attractions.json \
        --restaurants pokhara_restaurants.json \
        --output data_templates/

Run it once per city/region with --output pointing at the same folder --
it APPENDS to existing locations/destinations/restaurants CSVs there
(creating them if they don't exist yet) rather than overwriting, so you
can build up a nationwide dataset one region at a time.

Then import as usual:
    python manage.py import_data --dry-run --locations data_templates/locations.csv \
        --destinations data_templates/destinations.csv --restaurants data_templates/restaurants.csv

ROBUSTNESS

Real OSM data is crowd-sourced and messy: missing names, missing
coordinates, weird opening_hours syntax, duplicate entries mapped as both
a point and an outline. This script skips anything it can't confidently
convert (printing why) rather than guessing or crashing -- exactly the
same philosophy as import_data.py itself.
"""
import argparse
import csv
import json
import re
import statistics
from pathlib import Path

# A handful of common OSM tourism values, translated to readable English
# for the auto-generated description when OSM has no free-text description.
TOURISM_LABELS = {
    'attraction': 'tourist attraction', 'museum': 'museum', 'viewpoint': 'scenic viewpoint',
    'artwork': 'public artwork', 'gallery': 'art gallery', 'zoo': 'zoo',
    'theme_park': 'theme park', 'picnic_site': 'picnic site', 'camp_site': 'campsite',
}

TIME_RANGE_RE = re.compile(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})')


def load_elements(path):
    """Load an Overpass export, auto-detecting raw JSON vs GeoJSON.
    Returns a list of (lat, lon, tags) tuples. Never raises -- bad/missing
    files or malformed entries are reported and skipped."""
    p = Path(path)
    if not p.exists():
        print(f'  File not found, skipping: {path}')
        return []

    try:
        with p.open(encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f'  Could not parse {path} as JSON: {e}')
        return []

    results = []
    skipped = 0

    if isinstance(data, dict) and 'elements' in data:
        # Raw Overpass JSON: {"elements": [{"type": "node", "lat":.., "lon":.., "tags": {...}}, ...]}
        for el in data['elements']:
            tags = el.get('tags') or {}
            lat = el.get('lat')
            lon = el.get('lon')
            if lat is None or lon is None:
                # ways/relations queried with "out center" have their
                # representative point under "center" instead
                center = el.get('center') or {}
                lat, lon = center.get('lat'), center.get('lon')
            if lat is None or lon is None or not tags:
                skipped += 1
                continue
            results.append((lat, lon, tags))

    elif isinstance(data, dict) and data.get('type') == 'FeatureCollection':
        # GeoJSON: {"type": "FeatureCollection", "features": [{"geometry": {"coordinates": [lon, lat]}, "properties": {...}}]}
        for feature in data.get('features', []):
            geom = feature.get('geometry') or {}
            coords = geom.get('coordinates')
            tags = feature.get('properties') or {}
            if not coords or len(coords) < 2 or not tags:
                skipped += 1
                continue
            lon, lat = coords[0], coords[1]
            results.append((lat, lon, tags))
    else:
        print(f'  Unrecognized JSON structure in {path} (expected Overpass JSON or GeoJSON)')
        return []

    if skipped:
        print(f'  ({skipped} entries in {path} had no name/coordinates and were skipped)')
    return results


def extract_business_hours(opening_hours):
    """OSM's opening_hours syntax is a small language of its own (day
    ranges, exceptions, seasons...) -- fully parsing it is out of scope.
    We only pull out a simple HH:MM-HH:MM range if one is present, since
    that's all the TravelEase business_hours field supports; anything
    more complex is left blank rather than guessed at."""
    if not opening_hours:
        return ''
    match = TIME_RANGE_RE.search(opening_hours)
    return f'{match.group(1)}-{match.group(2)}' if match else ''


def describe_tourism(tags):
    if tags.get('description'):
        return tags['description'].strip()
    kind = TOURISM_LABELS.get(tags.get('tourism', ''), 'point of interest')
    name = tags.get('name', 'This')
    return f'{name} is a {kind} in Nepal.'


def image_from_tags(tags):
    """OSM occasionally has a direct photo for a place -- either a plain
    URL in the 'image' tag, or a Wikimedia Commons filename in
    'wikimedia_commons' (converted to its real file URL). Only ever
    returns a photo actually tagged on *this* element -- never guessed
    from the name, so there's no risk of attaching the wrong place's
    photo the way a text search could."""
    image = (tags.get('image') or '').strip()
    if image.startswith('http://') or image.startswith('https://'):
        return image
    commons_file = (tags.get('wikimedia_commons') or '').strip()
    if commons_file.startswith('File:'):
        # Commons' stable redirect: goes straight to the full-size file
        # without needing to resolve a page/thumbnail via their API.
        filename = commons_file[len('File:'):].replace(' ', '_')
        return f'https://commons.wikimedia.org/wiki/Special:FilePath/{filename}'
    return ''


def convert(elements, default_location, kind, seen_names):
    """kind is 'destination' or 'restaurant'. Returns (rows, location_points).

    Each row also carries the *element's own* latitude/longitude (the
    exact OSM node/way position) under 'latitude'/'longitude' -- not just
    the aggregated location_points used to recenter the parent Location.
    Previously this per-element position was computed here and then
    thrown away, only ever folded into a shared per-town/city centroid --
    which is why every destination/restaurant in the same town used to
    render on top of each other on the map and all resolve to the exact
    same "Get directions" target, however far apart they actually are.
    Carrying it through means each place gets its own map pin/route, and
    the parent Location still gets its usual centroid from the same
    points for search/distance-ranking purposes -- both are populated
    from the one Overpass fetch, neither at the other's expense.
    """
    rows = []
    location_points = {}  # location_name -> list of (lat, lon)
    skipped_no_name = 0

    for lat, lon, tags in elements:
        name = (tags.get('name') or '').strip()
        if not name:
            skipped_no_name += 1
            continue

        location_name = (tags.get('addr:city') or default_location or '').strip()
        if not location_name:
            skipped_no_name += 1  # can't place it anywhere meaningful
            continue

        dedupe_key = (kind, name.lower(), location_name.lower())
        if dedupe_key in seen_names:
            continue
        seen_names.add(dedupe_key)

        location_points.setdefault(location_name, []).append((float(lat), float(lon)))

        if kind == 'destination':
            rows.append({
                'name': name,
                'description': describe_tourism(tags),
                'location_name': location_name,
                'image': image_from_tags(tags),
                'latitude': round(float(lat), 6),
                'longitude': round(float(lon), 6),
            })
        else:
            rows.append({
                'name': name,
                'cuisine': tags.get('cuisine', '').replace(';', ', '),
                'average_cost': '',  # OSM doesn't have pricing data
                'rating': '',        # OSM doesn't have ratings
                'price_range': '',
                'business_hours': extract_business_hours(tags.get('opening_hours', '')),
                'location_name': location_name,
                'image': image_from_tags(tags),
                'latitude': round(float(lat), 6),
                'longitude': round(float(lon), 6),
            })

    if skipped_no_name:
        print(f'  ({skipped_no_name} {kind} entries had no usable name/location and were skipped)')
    return rows, location_points


def merge_location_points(all_points, existing_locations_csv):
    """Combine newly-seen location points with any locations already in
    an existing locations.csv (so re-running for a new city doesn't lose
    previously-imported ones), and compute a centroid per location.

    A location's row in an existing locations.csv stores exactly one
    (already-averaged) point plus a property_count that -- by this
    function's own convention (see the `centroids[name] = (..., len(pts))`
    line below) -- equals how many raw points originally produced that
    centroid. To fold it back in without losing that weight, it's expanded
    back into `property_count` copies of its stored centroid before
    averaging, and that's always combined with any new points this run
    found for the same name -- never one *instead of* the other. Doing
    only the latter (as a previous version of this function did) meant a
    location touched again in a later run had its entire prior
    contribution silently discarded: its property_count would shrink to
    just this run's new point count, and its centroid would recenter on
    only the newest points.
    """
    merged = {name: list(pts) for name, pts in all_points.items()}

    if existing_locations_csv and Path(existing_locations_csv).exists():
        with open(existing_locations_csv, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                name = row.get('name', '').strip()
                if not name:
                    continue
                try:
                    lat, lon = float(row['latitude']), float(row['longitude'])
                except (KeyError, ValueError, TypeError):
                    continue
                try:
                    weight = int(row.get('property_count') or 1)
                except (ValueError, TypeError):
                    weight = 1
                weight = max(weight, 1)
                merged[name] = [(lat, lon)] * weight + merged.get(name, [])

    centroids = {}
    for name, pts in merged.items():
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        centroids[name] = (round(statistics.mean(lats), 6), round(statistics.mean(lons), 6), len(pts))
    return centroids


def write_csv(path, fieldnames, rows, append):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not (append and path.exists())
    mode = 'a' if append and path.exists() else 'w'
    with path.open(mode, newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--location', required=True,
                         help='Default location name for entries without an addr:city tag (e.g. "Pokhara").')
    parser.add_argument('--attractions', help='Path to an Overpass export of tourism attractions.')
    parser.add_argument('--restaurants', help='Path to an Overpass export of restaurants.')
    parser.add_argument('--output', default='data_templates',
                         help='Output directory for locations.csv/destinations.csv/restaurants.csv (default: data_templates/)')
    parser.add_argument('--overwrite', action='store_true',
                         help='Overwrite existing CSVs in --output instead of appending to them.')
    args = parser.parse_args()

    if not args.attractions and not args.restaurants:
        parser.error('Provide at least one of --attractions or --restaurants.')

    append = not args.overwrite
    seen_names = set()
    all_points = {}
    dest_rows, rest_rows = [], []

    if args.attractions:
        print(f'Reading attractions from {args.attractions}...')
        elements = load_elements(args.attractions)
        dest_rows, points = convert(elements, args.location, 'destination', seen_names)
        for name, pts in points.items():
            all_points.setdefault(name, []).extend(pts)
        print(f'  -> {len(dest_rows)} destinations extracted')

    if args.restaurants:
        print(f'Reading restaurants from {args.restaurants}...')
        elements = load_elements(args.restaurants)
        rest_rows, points = convert(elements, args.location, 'restaurant', seen_names)
        for name, pts in points.items():
            all_points.setdefault(name, []).extend(pts)
        print(f'  -> {len(rest_rows)} restaurants extracted')

    out_dir = Path(args.output)
    locations_csv = out_dir / 'locations.csv'
    destinations_csv = out_dir / 'destinations.csv'
    restaurants_csv = out_dir / 'restaurants.csv'

    centroids = merge_location_points(all_points, locations_csv if append else None)
    location_rows = [
        {'name': name, 'latitude': lat, 'longitude': lon, 'property_count': count}
        for name, (lat, lon, count) in sorted(centroids.items())
    ]

    write_csv(locations_csv, ['name', 'latitude', 'longitude', 'property_count'], location_rows, append=False)
    if dest_rows:
        write_csv(destinations_csv, ['name', 'description', 'location_name', 'image', 'latitude', 'longitude'], dest_rows, append)
    if rest_rows:
        write_csv(restaurants_csv, ['name', 'cuisine', 'average_cost', 'rating', 'price_range',
                                     'business_hours', 'location_name', 'image', 'latitude', 'longitude'], rest_rows, append)

    print()
    print(f'Wrote {len(location_rows)} locations to {locations_csv}')
    if dest_rows:
        print(f'Wrote {len(dest_rows)} destinations to {destinations_csv}')
    if rest_rows:
        print(f'Wrote {len(rest_rows)} restaurants to {restaurants_csv}')
    print()
    print('Next step -- preview before touching the database:')
    print(f'  python manage.py import_data --dry-run --locations {locations_csv} '
          f'--destinations {destinations_csv} --restaurants {restaurants_csv}')


if __name__ == '__main__':
    main()
