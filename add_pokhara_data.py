#!/usr/bin/env python3
"""
Adds Pokhara as a searchable location, alongside the existing Banepa-area
seed data (does NOT clear or touch what's already there). Run once:

    python add_pokhara_data.py

Safe to re-run: skips creating Pokhara again if it already exists.
"""
import os
import sys
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'travelease.settings')

try:
    import django
    django.setup()
except Exception as e:
    print(f"Error setting up Django: {e}")
    sys.exit(1)

from web_interface.models import Location, Destination, PlaceToEat

POKHARA_COORDS = {"lat": 28.2096, "lon": 83.9856}

DESTINATIONS = [
    {"name": "Phewa Lake", "desc": "Pokhara's centerpiece lake, popular for boating with views of the Annapurna range reflected on the water."},
    {"name": "Sarangkot", "desc": "Hilltop viewpoint above the city, famous for sunrise views over the Annapurna and Machapuchare (Fishtail) peaks."},
    {"name": "World Peace Pagoda", "desc": "A hilltop Buddhist stupa overlooking Phewa Lake and the Pokhara valley, reachable by a short hike or boat plus walk."},
    {"name": "Davis Falls", "desc": "A dramatic waterfall (locally Patale Chhango) that disappears into an underground tunnel."},
    {"name": "International Mountain Museum", "desc": "Museum covering the history, culture, and mountaineering heritage of Nepal's Himalayan ranges."},
    {"name": "Old Pokhara Bazaar", "desc": "The city's older market street, with traditional Newari-style buildings and local shops."},
]

RESTAURANTS = [
    {"name": "Lakeside Kitchen", "cuisine": "Nepali", "cost": 400, "rating": 4.3},
    {"name": "Pokhara Newari Ghar", "cuisine": "Newari", "cost": 450, "rating": 4.4},
    {"name": "Fishtail View Cafe", "cuisine": "Continental", "cost": 500, "rating": 4.2},
    {"name": "Annapurna Momo House", "cuisine": "Nepali", "cost": 250, "rating": 4.1},
    {"name": "Lakeside Bakery Cafe", "cuisine": "Cafe", "cost": 300, "rating": 4.0},
]


def main():
    location, created = Location.objects.get_or_create(
        name__iexact="Pokhara",
        defaults={
            "name": "Pokhara",
            "latitude": Decimal(str(POKHARA_COORDS["lat"])),
            "longitude": Decimal(str(POKHARA_COORDS["lon"])),
            "property_count": 0,
        },
    )
    if created:
        print("Created location: Pokhara")
    else:
        print("Location 'Pokhara' already exists, reusing it")

    dest_count = 0
    for d in DESTINATIONS:
        _, created = Destination.objects.get_or_create(
            location=location, name=d["name"], defaults={"description": d["desc"]}
        )
        if created:
            dest_count += 1
            print(f"   Created destination: {d['name']}")

    rest_count = 0
    for r in RESTAURANTS:
        cost = r["cost"]
        price_range = 1 if cost < 200 else 2 if cost < 400 else 3 if cost < 600 else 4
        _, created = PlaceToEat.objects.get_or_create(
            location=location,
            name=r["name"],
            defaults={
                "cuisine": r["cuisine"],
                "average_cost": Decimal(str(cost)),
                "rating": Decimal(str(r["rating"])),
                "seed_average_cost": Decimal(str(cost)),
                "seed_rating": Decimal(str(r["rating"])),
                "price_range": price_range,
                "business_hours": "09:00-21:00",
            },
        )
        if created:
            rest_count += 1
            print(f"   Created restaurant: {r['name']}")

    location.property_count = Destination.objects.filter(location=location).count() + \
        PlaceToEat.objects.filter(location=location).count()
    location.save()

    print(f"\nDone. Added {dest_count} destinations and {rest_count} restaurants for Pokhara.")
    print(f"Total Pokhara listings: {location.property_count}")


if __name__ == "__main__":
    main()
