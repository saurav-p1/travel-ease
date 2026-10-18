#!/usr/bin/env python3
"""
Adds Banepa-area locations (Banepa, Dhulikhel, Panauti, Namobuddha,
Khopasi, Kavre) as searchable destinations/restaurants, alongside
whatever's already in the database from add_pokhara_data.py /
add_nepal_wide_data.py. Run once:

    python populate_data.py

Safe to re-run: skips creating anything that already exists.

FIXED (found during audit): this script used to unconditionally call
Location.objects.all().delete() (cascading to *every* Destination and
PlaceToEat in the database, from *any* region) before creating its own
Banepa-area data -- silently wiping out everything add_pokhara_data.py
or add_nepal_wide_data.py had added, with no warning, if this script
happened to be run afterward. It also created rows unconditionally
rather than with get_or_create, so re-running it after that would have
produced duplicate destinations/restaurants for the same location. Now
scoped and idempotent to match its two sibling scripts, so all three
can be run in any order, any number of times, without stepping on each
other's data.
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

try:
    from web_interface.models import Location, Destination, PlaceToEat
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

LOCATIONS_DATA = [
    {"name": "Banepa", "lat": 27.6298, "lon": 85.5203},
    {"name": "Dhulikhel", "lat": 27.6159, "lon": 85.5391},
    {"name": "Panauti", "lat": 27.5855, "lon": 85.5146},
    {"name": "Namobuddha", "lat": 27.5849, "lon": 85.5890},
    {"name": "Khopasi", "lat": 27.6087, "lon": 85.4692},
    {"name": "Kavre", "lat": 27.6483, "lon": 85.5648},
]

DESTINATION_TEMPLATES = {
    "Banepa": [
        {"name": "Banepa Durbar Square", "desc": "Historic palace square in the heart of Banepa"},
        {"name": "Chandeshwori Temple", "desc": "Ancient Hindu temple dedicated to Goddess Chandeshwori"},
        {"name": "Banepa Market", "desc": "Traditional local market with authentic goods"},
        {"name": "Old Banepa Town", "desc": "Historic town center with traditional architecture"},
        {"name": "Banepa Heritage Walk", "desc": "Walking trail through historic sites"},
    ],
    "Dhulikhel": [
        {"name": "Dhulikhel Viewpoint", "desc": "Stunning mountain views of the Himalayas"},
        {"name": "Kali Temple Dhulikhel", "desc": "Sacred temple with panoramic valley views"},
        {"name": "Dhulikhel Old Town", "desc": "Traditional Newari architecture and culture"},
        {"name": "Narayani Temple", "desc": "Historic temple in Dhulikhel"},
        {"name": "Mountain View Tower", "desc": "Best spot for sunrise and mountain photography"},
    ],
    "Panauti": [
        {"name": "Indreshwar Temple", "desc": "Ancient pagoda-style temple, UNESCO heritage site"},
        {"name": "Panauti Museum", "desc": "Local museum showcasing Panauti's rich history"},
        {"name": "Old Panauti", "desc": "Historic town with traditional Newari houses"},
        {"name": "Roshi River Bridge", "desc": "Historic bridge over Roshi River"},
        {"name": "Panauti Durbar Square", "desc": "Ancient royal palace square"},
    ],
    "Namobuddha": [
        {"name": "Namobuddha Monastery", "desc": "Sacred Buddhist monastery and pilgrimage site"},
        {"name": "Namobuddha Stupa", "desc": "Holy stupa marking Buddha's previous life sacrifice"},
        {"name": "Mountain View Point", "desc": "Spectacular Himalayan views from Namobuddha"},
        {"name": "Meditation Center", "desc": "Peaceful place for meditation and reflection"},
        {"name": "Pilgrimage Trail", "desc": "Sacred walking path for Buddhist pilgrims"},
    ],
    "Khopasi": [
        {"name": "Khopasi Market", "desc": "Local market town on the Arniko Highway"},
        {"name": "Khopasi Viewpoint", "desc": "Great views of surrounding valleys"},
        {"name": "Local Temples", "desc": "Traditional Hindu temples in Khopasi area"},
    ],
    "Kavre": [
        {"name": "Kavre District Center", "desc": "Administrative center of Kavre district"},
        {"name": "Local Heritage Sites", "desc": "Various cultural and religious sites"},
        {"name": "Traditional Villages", "desc": "Authentic rural Nepali village experience"},
    ],
}

RESTAURANT_TEMPLATES = {
    "Banepa": [
        {"name": "Banepa Khaja Ghar", "cuisine": "Nepali", "cost": 250, "rating": 4.2},
        {"name": "Newari Kitchen Banepa", "cuisine": "Newari", "cost": 350, "rating": 4.5},
        {"name": "Highway Restaurant", "cuisine": "Indian", "cost": 300, "rating": 4.0},
        {"name": "Banepa Cafe", "cuisine": "Cafe", "cost": 200, "rating": 4.1},
        {"name": "Local Bhatti", "cuisine": "Nepali", "cost": 180, "rating": 3.8},
    ],
    "Dhulikhel": [
        {"name": "Dhulikhel Mountain View", "cuisine": "Continental", "cost": 600, "rating": 4.6},
        {"name": "Himalayan Kitchen", "cuisine": "Nepali", "cost": 400, "rating": 4.4},
        {"name": "Dhulikhel Paradise", "cuisine": "Multi-cuisine", "cost": 500, "rating": 4.3},
        {"name": "View Point Restaurant", "cuisine": "Chinese", "cost": 450, "rating": 4.2},
        {"name": "Local Dine", "cuisine": "Nepali", "cost": 280, "rating": 4.0},
    ],
    "Panauti": [
        {"name": "Panauti Heritage Kitchen", "cuisine": "Newari", "cost": 320, "rating": 4.3},
        {"name": "Traditional Bhoj", "cuisine": "Nepali", "cost": 220, "rating": 4.1},
        {"name": "River Side Cafe", "cuisine": "Cafe", "cost": 250, "rating": 4.0},
        {"name": "Panauti Local Food", "cuisine": "Nepali", "cost": 180, "rating": 3.9},
    ],
    "Namobuddha": [
        {"name": "Monastery Kitchen", "cuisine": "Vegetarian", "cost": 200, "rating": 4.4},
        {"name": "Buddha View Restaurant", "cuisine": "Tibetan", "cost": 350, "rating": 4.5},
        {"name": "Pilgrims Rest", "cuisine": "Nepali", "cost": 250, "rating": 4.2},
        {"name": "Mountain Lodge Dining", "cuisine": "Continental", "cost": 500, "rating": 4.3},
    ],
    "Khopasi": [
        {"name": "Highway Dhaba", "cuisine": "Indian", "cost": 200, "rating": 3.8},
        {"name": "Khopasi Local", "cuisine": "Nepali", "cost": 150, "rating": 3.7},
        {"name": "Truck Stop Cafe", "cuisine": "Fast Food", "cost": 180, "rating": 3.6},
    ],
    "Kavre": [
        {"name": "District Kitchen", "cuisine": "Nepali", "cost": 220, "rating": 4.0},
        {"name": "Kavre Traditional", "cuisine": "Newari", "cost": 280, "rating": 4.1},
        {"name": "Local Chulo", "cuisine": "Nepali", "cost": 160, "rating": 3.8},
    ],
}


def price_range_for(cost):
    if cost < 200:
        return 1
    if cost < 400:
        return 2
    if cost < 600:
        return 3
    return 4


def main():
    print("Adding Banepa-area locations (leaves any existing data untouched)...")
    print("-" * 50)

    total_new_destinations = 0
    total_new_restaurants = 0

    for loc_data in LOCATIONS_DATA:
        location, created = Location.objects.get_or_create(
            name__iexact=loc_data["name"],
            defaults={
                "name": loc_data["name"],
                "latitude": Decimal(str(loc_data["lat"])),
                "longitude": Decimal(str(loc_data["lon"])),
                "property_count": 0,
            },
        )
        print(f"Location: {loc_data['name']} ({'created' if created else 'already existed, reusing'})")

        for template in DESTINATION_TEMPLATES.get(loc_data["name"], []):
            _, created = Destination.objects.get_or_create(
                location=location, name=template["name"], defaults={"description": template["desc"]}
            )
            if created:
                total_new_destinations += 1
                print(f"   Created destination: {template['name']}")

        for template in RESTAURANT_TEMPLATES.get(loc_data["name"], []):
            cost = template["cost"]
            _, created = PlaceToEat.objects.get_or_create(
                location=location,
                name=template["name"],
                defaults={
                    "cuisine": template["cuisine"],
                    "average_cost": Decimal(str(cost)),
                    "rating": Decimal(str(template["rating"])),
                    "seed_average_cost": Decimal(str(cost)),
                    "seed_rating": Decimal(str(template["rating"])),
                    "price_range": price_range_for(cost),
                    "business_hours": "09:00-21:00",
                },
            )
            if created:
                total_new_restaurants += 1
                print(f"   Created restaurant: {template['name']}")

        location.property_count = (
            Destination.objects.filter(location=location).count()
            + PlaceToEat.objects.filter(location=location).count()
        )
        location.save()

    print("-" * 50)
    print(f"Done. Added {total_new_destinations} new destinations and {total_new_restaurants} new restaurants.")
    print(f"Locations now in the database: {Location.objects.count()}")


if __name__ == "__main__":
    main()
