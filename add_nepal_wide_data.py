#!/usr/bin/env python3
"""
Adds major destinations/restaurants across ALL of Nepal (one city/town per
province, roughly), on top of whatever's already there (Kavre area,
Pokhara). Does not clear or touch existing data -- safe to re-run.

This is still hand-curated seed data, not a full OpenStreetMap import --
see README.md's "Adding real data safely" section for the real pipeline
(scripts/osm_to_csv.py + manage.py import_data) once you have OSM/Overpass
access to pull exhaustive, up-to-date coverage of every town.

Run:
    python add_nepal_wide_data.py
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

# Each entry: location name, coords, real well-known destinations, and a
# handful of restaurants in the same generic-but-plausible style as the
# rest of this project's seed data (not verified real businesses).
NEPAL_DATA = [
    {
        "location": "Kathmandu", "lat": 27.7172, "lon": 85.3240,
        "destinations": [
            ("Kathmandu Durbar Square", "UNESCO World Heritage royal palace complex in the old city center."),
            ("Swayambhunath (Monkey Temple)", "Ancient hilltop Buddhist stupa overlooking the Kathmandu valley."),
            ("Boudhanath Stupa", "One of the largest stupas in the world, a major center of Tibetan Buddhism."),
            ("Pashupatinath Temple", "Sacred Hindu temple complex on the Bagmati River, a major pilgrimage site."),
            ("Thamel", "The city's main tourist district, packed with shops, cafes, and trekking gear stores."),
        ],
        "restaurants": [
            ("Thamel Newari Bhansa Ghar", "Newari", 450, 4.3),
            ("Durbar Square Momo House", "Nepali", 220, 4.1),
            ("Kathmandu Rooftop Kitchen", "Continental", 650, 4.4),
        ],
    },
    {
        "location": "Bhaktapur", "lat": 27.6710, "lon": 85.4298,
        "destinations": [
            ("Bhaktapur Durbar Square", "UNESCO World Heritage site famed for its preserved medieval architecture."),
            ("Nyatapola Temple", "Nepal's tallest pagoda temple, a five-story structure in Taumadhi Square."),
            ("Pottery Square", "Traditional square where potters still shape and dry clay wares in the open."),
        ],
        "restaurants": [
            ("Juju Dhau House", "Newari", 200, 4.2),
            ("Bhaktapur Heritage Kitchen", "Newari", 400, 4.4),
            ("Taumadhi Cafe", "Cafe", 300, 4.0),
        ],
    },
    {
        "location": "Patan", "lat": 27.6588, "lon": 85.3247,
        "destinations": [
            ("Patan Durbar Square", "UNESCO World Heritage royal square known for its Newari architecture and metal craft."),
            ("Kumbheshwar Temple", "Historic five-story pagoda temple dedicated to Shiva."),
            ("Patan Museum", "Museum of traditional bronze and metal religious art, housed in a former palace."),
        ],
        "restaurants": [
            ("Patan Courtyard Kitchen", "Newari", 420, 4.3),
            ("Ashoka Cafe", "Cafe", 280, 4.0),
        ],
    },
    {
        "location": "Nagarkot", "lat": 27.7167, "lon": 85.5200,
        "destinations": [
            ("Nagarkot Sunrise Viewpoint", "Hill station famous for panoramic sunrise views of the Himalayan range, including Everest on clear days."),
        ],
        "restaurants": [
            ("Nagarkot View Restaurant", "Continental", 550, 4.3),
            ("Hillside Nepali Kitchen", "Nepali", 300, 4.1),
        ],
    },
    {
        "location": "Chitwan", "lat": 27.5291, "lon": 84.3542,
        "destinations": [
            ("Chitwan National Park", "UNESCO World Heritage jungle park known for one-horned rhinos, tigers, and elephant safaris."),
            ("Elephant Breeding Center", "Government-run center for breeding and caring for domesticated elephants."),
            ("Tharu Village", "Village showcasing the traditional culture and homes of the indigenous Tharu people."),
        ],
        "restaurants": [
            ("Jungle Safari Restaurant", "Nepali", 350, 4.2),
            ("Tharu Kitchen", "Tharu", 300, 4.3),
            ("Riverside Cafe Chitwan", "Continental", 500, 4.1),
        ],
    },
    {
        "location": "Lumbini", "lat": 27.4833, "lon": 83.2764,
        "destinations": [
            ("Maya Devi Temple", "UNESCO World Heritage site marking the birthplace of Gautam Buddha."),
            ("Lumbini Garden", "Sacred garden with monasteries built by Buddhist communities from around the world."),
            ("World Peace Pagoda, Lumbini", "A large white stupa built as a monument to world peace."),
        ],
        "restaurants": [
            ("Lumbini Garden Restaurant", "Nepali", 300, 4.1),
            ("Peace Pagoda Cafe", "Vegetarian", 250, 4.2),
        ],
    },
    {
        "location": "Bandipur", "lat": 27.9333, "lon": 84.4167,
        "destinations": [
            ("Bandipur Bazaar", "Beautifully preserved Newari hill-town bazaar, closed to vehicle traffic."),
            ("Tindhare Viewpoint", "Ridge viewpoint with wide views of the Annapurna and Manaslu ranges."),
            ("Siddha Cave", "One of the largest cave chambers in Nepal, a short hike from town."),
        ],
        "restaurants": [
            ("Bandipur Old Bazaar Kitchen", "Newari", 350, 4.4),
            ("Ridge View Cafe", "Cafe", 280, 4.1),
        ],
    },
    {
        "location": "Gorkha", "lat": 28.0000, "lon": 84.6333,
        "destinations": [
            ("Gorkha Durbar", "Hilltop palace and birthplace of Prithvi Narayan Shah, unifier of modern Nepal."),
            ("Gorkhnath Temple", "Cave temple within the Gorkha Durbar complex, sacred to Hindus and Buddhists alike."),
            ("Manakamana Temple", "Hilltop temple reached by cable car, believed to grant the wishes of devotees."),
        ],
        "restaurants": [
            ("Durbar View Restaurant", "Nepali", 280, 4.1),
            ("Manakamana Cable Car Cafe", "Cafe", 250, 3.9),
        ],
    },
    {
        "location": "Ilam", "lat": 26.9088, "lon": 87.9284,
        "destinations": [
            ("Ilam Tea Gardens", "Rolling hillside tea estates, among Nepal's most famous tea-growing regions."),
            ("Antu Danda Viewpoint", "Ridge viewpoint known for sunrise views over the eastern hills."),
            ("Mai Pokhari", "A sacred lake and Ramsar wetland site surrounded by forest."),
        ],
        "restaurants": [
            ("Tea Garden Restaurant", "Nepali", 280, 4.2),
            ("Antu View Cafe", "Cafe", 250, 4.0),
        ],
    },
    {
        "location": "Dharan", "lat": 26.8065, "lon": 87.2846,
        "destinations": [
            ("Dantakali Temple", "Hilltop temple overlooking Dharan, one of the region's major Shakti Peeths."),
            ("Buddha Subba Temple", "Historic temple popular with local pilgrims."),
            ("Bhedetar Viewpoint", "Nearby hill viewpoint on the road to Ilam, popular for its cool climate."),
        ],
        "restaurants": [
            ("Dharan Local Kitchen", "Nepali", 220, 4.0),
            ("Bhedetar View Restaurant", "Continental", 350, 4.1),
        ],
    },
    {
        "location": "Biratnagar", "lat": 26.4525, "lon": 87.2718,
        "destinations": [
            ("Rani Mirchaiya Wildlife Reserve area", "Wetland area near Biratnagar known for birdwatching."),
            ("Biratnagar Bazaar", "The city's main commercial and market district."),
        ],
        "restaurants": [
            ("Biratnagar Highway Dhaba", "Indian", 220, 3.9),
            ("City Center Kitchen", "Nepali", 250, 4.0),
        ],
    },
    {
        "location": "Janakpur", "lat": 26.7288, "lon": 85.9252,
        "destinations": [
            ("Janaki Mandir", "Ornate Hindu temple dedicated to Goddess Sita, a major Mithila pilgrimage site."),
            ("Ram Mandir, Janakpur", "Historic temple complex near Janaki Mandir."),
        ],
        "restaurants": [
            ("Mithila Bhojanalaya", "Nepali", 250, 4.1),
            ("Janakpur Local Kitchen", "Indian", 280, 4.0),
        ],
    },
    {
        "location": "Butwal", "lat": 27.7000, "lon": 83.4486,
        "destinations": [
            ("Tinau Riverside", "Riverside area popular for walks and picnics."),
            ("Butwal Bazaar", "The city's main market and commercial hub."),
        ],
        "restaurants": [
            ("Butwal Highway Restaurant", "Indian", 250, 3.9),
            ("Tinau View Cafe", "Cafe", 220, 4.0),
        ],
    },
    {
        "location": "Palpa (Tansen)", "lat": 27.8667, "lon": 83.5500,
        "destinations": [
            ("Tansen Durbar", "Historic palace of the Palpa Kingdom in the hill town of Tansen."),
            ("Srinagar Hill", "Viewpoint above Tansen with views of the Himalayan range."),
            ("Rani Mahal", "A grand riverside palace known as the 'Taj Mahal of Nepal'."),
        ],
        "restaurants": [
            ("Tansen Newari Kitchen", "Newari", 320, 4.2),
            ("Srinagar View Restaurant", "Nepali", 280, 4.1),
        ],
    },
    {
        "location": "Surkhet", "lat": 28.6000, "lon": 81.6167,
        "destinations": [
            ("Chandannath Temple, Surkhet", "Hilltop temple overlooking the Surkhet valley."),
            ("Birendranagar", "The main town area of Surkhet district."),
        ],
        "restaurants": [
            ("Surkhet Valley Kitchen", "Nepali", 250, 4.0),
        ],
    },
    {
        "location": "Dhangadhi", "lat": 28.6939, "lon": 80.5877,
        "destinations": [
            ("Ghodaghodi Lake", "A Ramsar wetland site known for its biodiversity and birdwatching."),
        ],
        "restaurants": [
            ("Dhangadhi Local Kitchen", "Nepali", 220, 3.9),
        ],
    },
    {
        "location": "Jumla", "lat": 29.2747, "lon": 82.1838,
        "destinations": [
            ("Chandannath Temple, Jumla", "One of the district's most important Hindu temples."),
            ("Jumla Bazaar", "The main market town, gateway to Rara Lake and the western Himalayas."),
        ],
        "restaurants": [
            ("Jumla Highland Kitchen", "Nepali", 250, 4.0),
        ],
    },
]


def main():
    total_dest, total_rest = 0, 0
    for entry in NEPAL_DATA:
        location, created = Location.objects.get_or_create(
            name__iexact=entry["location"],
            defaults={
                "name": entry["location"],
                "latitude": Decimal(str(entry["lat"])),
                "longitude": Decimal(str(entry["lon"])),
                "property_count": 0,
            },
        )
        print(f"{'Created' if created else 'Reusing'} location: {entry['location']}")

        for name, desc in entry["destinations"]:
            _, created = Destination.objects.get_or_create(
                location=location, name=name, defaults={"description": desc}
            )
            if created:
                total_dest += 1
                print(f"   + destination: {name}")

        for name, cuisine, cost, rating in entry["restaurants"]:
            price_range = 1 if cost < 200 else 2 if cost < 400 else 3 if cost < 600 else 4
            _, created = PlaceToEat.objects.get_or_create(
                location=location,
                name=name,
                defaults={
                    "cuisine": cuisine,
                    "average_cost": Decimal(str(cost)),
                    "rating": Decimal(str(rating)),
                    "seed_average_cost": Decimal(str(cost)),
                    "seed_rating": Decimal(str(rating)),
                    "price_range": price_range,
                    "business_hours": "09:00-21:00",
                },
            )
            if created:
                total_rest += 1
                print(f"   + restaurant: {name}")

        location.property_count = (
            Destination.objects.filter(location=location).count()
            + PlaceToEat.objects.filter(location=location).count()
        )
        location.save()

    print(f"\nDone. Added {total_dest} destinations and {total_rest} restaurants across {len(NEPAL_DATA)} locations.")


if __name__ == "__main__":
    main()
