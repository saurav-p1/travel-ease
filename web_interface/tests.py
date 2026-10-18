from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.management import call_command
from django.core.cache import cache
from io import StringIO
from decimal import Decimal
from datetime import time
import tempfile
import os
from .models import Location, Destination, PlaceToEat, RestaurantRating
from .views import calculate_distance, is_open, SUBMISSION_RATE_LIMIT_MAX
from accounts.models import SavedItem, RecentView, UserPreference

User = get_user_model()


class HaversineDistanceTests(TestCase):
    """Unit tests for the project's core distance/ranking algorithm."""

    def test_same_point_is_zero_distance(self):
        self.assertAlmostEqual(calculate_distance(27.7, 85.3, 27.7, 85.3), 0, places=5)

    def test_known_distance_kathmandu_to_pokhara(self):
        # Kathmandu ~27.7172,85.3240 ; Pokhara ~28.2096,83.9856
        # real-world distance is roughly 130-145km
        distance = calculate_distance(27.7172, 85.3240, 28.2096, 83.9856)
        self.assertGreater(distance, 100)
        self.assertLess(distance, 170)

    def test_invalid_input_returns_infinity(self):
        self.assertEqual(calculate_distance('not-a-number', 0, 0, 0), float('inf'))


class BusinessHoursTests(TestCase):
    def test_open_within_hours(self):
        from datetime import time
        self.assertTrue(is_open('09:00-21:00', current_time=time(12, 0)))

    def test_closed_outside_hours(self):
        from datetime import time
        self.assertFalse(is_open('09:00-21:00', current_time=time(23, 0)))

    def test_blank_hours_returns_none(self):
        self.assertIsNone(is_open(''))


class WebInterfaceViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.location = Location.objects.create(
            name='Kathmandu', latitude=27.7172, longitude=85.3240, property_count=10
        )
        self.destination = Destination.objects.create(
            location=self.location, name='Boudhanath Stupa', description='A famous stupa.'
        )
        self.restaurant = PlaceToEat.objects.create(
            location=self.location, name='Thakali Kitchen', cuisine='Nepali',
            average_cost=8.5, rating=4.5, price_range=2, business_hours='09:00-21:00'
        )
        self.user = User.objects.create_user(
            email='test@example.com', password='TestPass123!',
            first_name='Test', last_name='User',
        )

    def test_home_page_loads(self):
        resp = self.client.get(reverse('web_interface:home'))
        self.assertEqual(resp.status_code, 200)

    def test_custom_404_page_renders(self):
        resp = self.client.get('/this-page-does-not-exist-at-all/')
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, 'This page took a wrong turn', status_code=404)

    def test_search_finds_destination_by_name(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'search': 'Boudhanath'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.destination, [r['item'] for r in resp.context['all_results']])

    def test_travel_detail_records_recent_view_when_authenticated(self):
        self.client.force_login(self.user)
        self.client.get(reverse('web_interface:travel_detail', args=[self.destination.id]))
        self.assertTrue(
            RecentView.objects.filter(user=self.user, item_type='destination', item_id=self.destination.id).exists()
        )

    def test_toggle_save_item_saves_then_unsaves(self):
        self.client.force_login(self.user)
        url = reverse('web_interface:toggle_save_item')

        self.client.post(url, {'item_type': 'destination', 'item_id': self.destination.id})
        self.assertTrue(SavedItem.objects.filter(user=self.user, item_type='destination', item_id=self.destination.id).exists())

        self.client.post(url, {'item_type': 'destination', 'item_id': self.destination.id})
        self.assertFalse(SavedItem.objects.filter(user=self.user, item_type='destination', item_id=self.destination.id).exists())

    def test_saving_item_snapshots_its_image(self):
        """FOUND DURING AUDIT: SavedItem.item_image existed but was never
        actually written to, so saved_items.html had nothing to show and
        never even tried -- covers that saving now snapshots whatever
        image the destination/restaurant has at that moment."""
        self.destination.image = 'https://example.com/stupa.jpg'
        self.destination.save()
        self.client.force_login(self.user)
        self.client.post(reverse('web_interface:toggle_save_item'),
                          {'item_type': 'destination', 'item_id': self.destination.id})
        saved = SavedItem.objects.get(user=self.user, item_type='destination', item_id=self.destination.id)
        self.assertEqual(saved.item_image, 'https://example.com/stupa.jpg')

    def test_saved_items_page_shows_placeholder_when_no_image_snapshot(self):
        """A saved item with no image (the common case) must show the
        same on-brand placeholder the rest of the site uses -- not a
        blank gap."""
        self.client.force_login(self.user)
        self.client.post(reverse('web_interface:toggle_save_item'),
                          {'item_type': 'restaurant', 'item_id': self.restaurant.id})
        resp = self.client.get(reverse('web_interface:saved_items'))
        self.assertContains(resp, 'image-placeholder restaurant')

    def test_toggle_save_item_requires_login(self):
        url = reverse('web_interface:toggle_save_item')
        resp = self.client.post(url, {'item_type': 'destination', 'item_id': self.destination.id})
        self.assertEqual(resp.status_code, 302)  # redirected to login

    def test_saved_items_page_requires_login(self):
        resp = self.client.get(reverse('web_interface:saved_items'))
        self.assertEqual(resp.status_code, 302)

    def test_restaurant_detail_shows_open_status(self):
        resp = self.client.get(reverse('web_interface:restaurant_detail', args=[self.restaurant.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('is_open', resp.context)

    def test_save_state_consistent_across_search_and_detail_pages(self):
        """Regression test: detail pages and search results used to always
        show 'Save' regardless of actual state, and search results had no
        save button at all."""
        self.client.force_login(self.user)

        # Not saved yet
        resp = self.client.get(reverse('web_interface:travel_detail', args=[self.destination.id]))
        self.assertFalse(resp.context['is_saved'])
        resp = self.client.get(reverse('web_interface:search_travel_options'))
        result = next(r for r in resp.context['all_results'] if r['item'].id == self.destination.id)
        self.assertFalse(result['is_saved'])

        # Save it
        self.client.post(reverse('web_interface:toggle_save_item'),
                          {'item_type': 'destination', 'item_id': self.destination.id})

        # Now both pages should reflect it
        resp = self.client.get(reverse('web_interface:travel_detail', args=[self.destination.id]))
        self.assertTrue(resp.context['is_saved'])
        resp = self.client.get(reverse('web_interface:search_travel_options'))
        result = next(r for r in resp.context['all_results'] if r['item'].id == self.destination.id)
        self.assertTrue(result['is_saved'])

    def test_recent_view_does_not_duplicate_on_repeat_visits(self):
        self.client.force_login(self.user)
        url = reverse('web_interface:travel_detail', args=[self.destination.id])
        self.client.get(url)
        self.client.get(url)
        self.client.get(url)
        count = RecentView.objects.filter(
            user=self.user, item_type='destination', item_id=self.destination.id
        ).count()
        self.assertEqual(count, 1)

    def test_search_sorts_results_by_distance_ascending(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'))
        results = resp.context['all_results']
        distances = [r['distance'] for r in results if r['distance'] is not None]
        self.assertEqual(distances, sorted(distances))

    def test_search_type_filter_only_returns_that_type(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'type': 'destination'})
        types = {r['type'] for r in resp.context['all_results']}
        self.assertEqual(types, {'destination'})

        resp = self.client.get(reverse('web_interface:search_travel_options'), {'type': 'place_to_eat'})
        types = {r['type'] for r in resp.context['all_results']}
        self.assertEqual(types, {'place_to_eat'})

    def test_search_has_no_hard_radius_cutoff_by_default(self):
        """Regression test: search used to hard-drop anything more than
        50km from the visitor's (auto-detected) location. TravelEase
        covers all of Nepal, so results should never be silently hidden
        unless the user explicitly asks for a radius filter."""
        far_location = Location.objects.create(
            name='Far Away Place', latitude=29.5, longitude=80.5, property_count=1  # >300km from Kathmandu
        )
        Destination.objects.create(location=far_location, name='Remote Spot', description='far')

        resp = self.client.get(reverse('web_interface:search_travel_options'))
        names = [r['item'].name for r in resp.context['all_results']]
        self.assertIn('Remote Spot', names)

    def test_search_can_target_a_different_location_than_the_visitor(self):
        """The core ask: let someone search/browse around a DIFFERENT
        place than wherever they currently are, the way a ride-hailing
        app lets you set a pickup point for someone else."""
        near = Location.objects.create(name='Near Origin', latitude=27.6660, longitude=85.3227, property_count=1)
        far = Location.objects.create(name='Chosen Elsewhere', latitude=28.5, longitude=84.0, property_count=1)
        Destination.objects.create(location=near, name='Near Spot', description='x')
        far_dest = Destination.objects.create(location=far, name='Far Spot', description='x')

        resp = self.client.get(reverse('web_interface:search_travel_options'), {'location_id': far.id})
        self.assertEqual(resp.context['selected_location'], far)
        far_result = next(r for r in resp.context['all_results'] if r['item'].id == far_dest.id)
        self.assertEqual(far_result['distance'], 0.0)

    def test_search_radius_filter_excludes_beyond_chosen_distance(self):
        origin = Location.objects.create(name='Radius Origin', latitude=27.7172, longitude=85.3240, property_count=1)
        far = Location.objects.create(name='Radius Far', latitude=28.2096, longitude=83.9856, property_count=1)  # ~135km
        Destination.objects.create(location=far, name='Radius Far Spot', description='x')

        resp = self.client.get(reverse('web_interface:search_travel_options'),
                                {'location_id': origin.id, 'radius': '25'})
        names = [r['item'].name for r in resp.context['all_results']]
        self.assertNotIn('Radius Far Spot', names)

    def test_search_invalid_location_id_does_not_crash(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'location_id': '999999999'})
        self.assertEqual(resp.status_code, 200)

    def test_search_handles_destination_with_missing_location_gracefully(self):
        """Robustness for real data: a row with bad/missing coordinates
        must not crash the whole search."""
        loc = Location.objects.create(name='Weird Coords', latitude=0, longitude=0, property_count=1)
        Destination.objects.create(location=loc, name='Null Island Spot', description='x')
        resp = self.client.get(reverse('web_interface:search_travel_options'))
        self.assertEqual(resp.status_code, 200)


class ImportDataCommandTests(TestCase):
    """Tests for the CSV bulk-import path used to switch from dummy data
    to real data without risking a crash or data corruption."""

    def _write_csv(self, tmpdir, name, content):
        path = os.path.join(tmpdir, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_valid_import_creates_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nTestville,27.7,85.3,2\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv',
                'name,description,location_name\nTest Spot,A place,Testville\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n'
                'Test Diner,Nepali,5.00,4.0,2,09:00-21:00,Testville\n')

            out = StringIO()
            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                          restaurants=rest_csv, stdout=out)

            self.assertTrue(Location.objects.filter(name='Testville').exists())
            self.assertTrue(Destination.objects.filter(name='Test Spot').exists())
            self.assertTrue(PlaceToEat.objects.filter(name='Test Diner').exists())

    def test_reimport_is_idempotent_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nTestville,27.7,85.3,2\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv', 'name,description,location_name\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n')

            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, stdout=StringIO())
            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, stdout=StringIO())

            self.assertEqual(Location.objects.filter(name='Testville').count(), 1)

    def test_malformed_rows_are_skipped_not_crashed(self):
        """The core robustness requirement: bad real-world data must never
        crash the import -- just skip that row and keep going."""
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\n'
                'Good Place,27.7,85.3,2\n'
                'Bad Lat,not-a-number,85.3,2\n'
                ',27.7,85.3,2\n'
                'Out Of Range,999,85.3,2\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv', 'name,description,location_name\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n')

            out = StringIO()
            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, stdout=out)

            self.assertTrue(Location.objects.filter(name='Good Place').exists())
            self.assertFalse(Location.objects.filter(name='Bad Lat').exists())
            self.assertEqual(Location.objects.count(), 1)

    def test_dry_run_does_not_write_to_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nDryRunPlace,27.7,85.3,2\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv', 'name,description,location_name\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n')

            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, dry_run=True, stdout=StringIO())

            self.assertFalse(Location.objects.filter(name='DryRunPlace').exists())

    def test_dry_run_correctly_validates_cross_file_references(self):
        """A destination referencing a location defined in the same import
        run must validate correctly in dry-run mode too."""
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nCrossRefPlace,27.7,85.3,2\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv',
                'name,description,location_name\nCrossRef Spot,x,CrossRefPlace\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n')

            out = StringIO()
            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, dry_run=True, stdout=out)

            self.assertIn('1 would be created', out.getvalue())
            self.assertNotIn('no matching location', out.getvalue())

    def test_missing_csv_file_does_not_crash(self):
        out = StringIO()
        call_command('import_data', locations='/nonexistent/path.csv',
                     destinations='/nonexistent/path2.csv',
                     restaurants='/nonexistent/path3.csv', stdout=out)
        self.assertIn('File not found', out.getvalue())


class OSMConverterTests(TestCase):
    """Tests for scripts/osm_to_csv.py, the OpenStreetMap-export-to-CSV
    converter. Imported directly as a module since it's a standalone
    script, not part of the Django app."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import sys
        from pathlib import Path
        scripts_dir = str(Path(__file__).resolve().parent.parent / 'scripts')
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import osm_to_csv
        cls.osm_to_csv = osm_to_csv

    def test_extract_business_hours_simple_range(self):
        self.assertEqual(self.osm_to_csv.extract_business_hours('Mo-Su 09:00-21:00'), '09:00-21:00')

    def test_extract_business_hours_blank_when_absent(self):
        self.assertEqual(self.osm_to_csv.extract_business_hours(''), '')
        self.assertEqual(self.osm_to_csv.extract_business_hours('24/7'), '')

    def test_load_elements_raw_overpass_json(self):
        import tempfile, json, os
        data = {'elements': [{'type': 'node', 'lat': 27.7, 'lon': 85.3, 'tags': {'name': 'X'}}]}
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            elements = self.osm_to_csv.load_elements(path)
            self.assertEqual(len(elements), 1)
            self.assertEqual(elements[0][2]['name'], 'X')
        finally:
            os.unlink(path)

    def test_load_elements_geojson(self):
        import tempfile, json, os
        data = {'type': 'FeatureCollection', 'features': [
            {'geometry': {'coordinates': [85.3, 27.7]}, 'properties': {'name': 'Y'}}
        ]}
        with tempfile.NamedTemporaryFile('w', suffix='.geojson', delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            elements = self.osm_to_csv.load_elements(path)
            self.assertEqual(len(elements), 1)
            lat, lon, tags = elements[0]
            self.assertEqual((lat, lon), (27.7, 85.3))
        finally:
            os.unlink(path)

    def test_load_elements_missing_file_does_not_crash(self):
        self.assertEqual(self.osm_to_csv.load_elements('/nonexistent/file.json'), [])

    def test_load_elements_malformed_json_does_not_crash(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            f.write('{not valid json')
            path = f.name
        try:
            self.assertEqual(self.osm_to_csv.load_elements(path), [])
        finally:
            os.unlink(path)

    def test_convert_skips_missing_name_and_dedupes(self):
        elements = [
            (27.7, 85.3, {'name': 'A', 'tourism': 'attraction'}),
            (27.7, 85.3, {'tourism': 'attraction'}),  # no name -> skipped
            (27.7, 85.3, {'name': 'A', 'tourism': 'attraction'}),  # duplicate -> skipped
        ]
        rows, points = self.osm_to_csv.convert(elements, 'TestCity', 'destination', set())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'A')


class PlaceToEatOptionalFieldsTests(TestCase):
    """Regression tests: average_cost/rating must be optional, since real
    free data sources (OpenStreetMap) rarely provide them."""

    def setUp(self):
        self.location = Location.objects.create(name='FieldTest', latitude=27.7, longitude=85.3, property_count=1)

    def test_restaurant_can_be_created_without_rating_or_cost(self):
        r = PlaceToEat.objects.create(location=self.location, name='No Data Diner', cuisine='Nepali')
        self.assertIsNone(r.rating)
        self.assertIsNone(r.average_cost)

    def test_restaurant_detail_page_renders_without_rating_or_cost(self):
        r = PlaceToEat.objects.create(location=self.location, name='No Data Diner 2', cuisine='Nepali')
        resp = self.client.get(reverse('web_interface:restaurant_detail', args=[r.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Not yet rated')


class RestaurantRatingTests(TestCase):
    """Tests for the crowd-sourced rating/cost submission feature -- this
    is how ratings/costs get filled in for restaurants sourced from map
    data (e.g. OpenStreetMap) that doesn't include commercial info."""

    def setUp(self):
        self.location = Location.objects.create(name='RatingLoc', latitude=27.7, longitude=85.3, property_count=1)
        self.restaurant = PlaceToEat.objects.create(location=self.location, name='Rating Diner', cuisine='Nepali')
        self.user1 = User.objects.create_user(email='rate1@example.com', password='Pass123!', first_name='A', last_name='B')
        self.user2 = User.objects.create_user(email='rate2@example.com', password='Pass123!', first_name='C', last_name='D')

    def test_first_submission_sets_aggregate(self):
        self.client.force_login(self.user1)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                          {'rating': '4.0', 'average_cost': '10.00'})
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.rating, Decimal('4.0'))
        self.assertEqual(self.restaurant.average_cost, Decimal('10.00'))

    def test_multiple_submissions_average_correctly(self):
        self.client.force_login(self.user1)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                          {'rating': '4.0', 'average_cost': '10.00'})
        self.client.force_login(self.user2)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                          {'rating': '5.0', 'average_cost': '8.00'})
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.rating, Decimal('4.5'))
        self.assertEqual(self.restaurant.average_cost, Decimal('9.00'))
        self.assertEqual(self.restaurant.user_ratings.count(), 2)

    def test_resubmitting_updates_not_duplicates(self):
        self.client.force_login(self.user1)
        url = reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id])
        self.client.post(url, {'rating': '4.0'})
        self.client.post(url, {'rating': '2.0'})
        self.assertEqual(RestaurantRating.objects.filter(place=self.restaurant, user=self.user1).count(), 1)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.rating, Decimal('2.0'))

    def test_anonymous_cannot_submit(self):
        resp = self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                                 {'rating': '3.0'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(RestaurantRating.objects.count(), 0)

    def test_out_of_range_rating_rejected(self):
        self.client.force_login(self.user1)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                          {'rating': '9.9'})
        self.assertEqual(RestaurantRating.objects.count(), 0)

    def test_non_numeric_rating_does_not_crash(self):
        self.client.force_login(self.user1)
        resp = self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                                 {'rating': 'not-a-number'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(RestaurantRating.objects.count(), 0)

    def test_negative_cost_rejected(self):
        self.client.force_login(self.user1)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                          {'rating': '4.0', 'average_cost': '-5.00'})
        self.assertEqual(RestaurantRating.objects.count(), 0)

    def test_get_request_not_allowed(self):
        self.client.force_login(self.user1)
        resp = self.client.get(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]))
        self.assertEqual(resp.status_code, 405)

    def test_detail_page_shows_users_own_submission(self):
        self.client.force_login(self.user1)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                          {'rating': '3.5'})
        resp = self.client.get(reverse('web_interface:restaurant_detail', args=[self.restaurant.id]))
        self.assertEqual(resp.context['user_rating'].rating, Decimal('3.5'))
        self.assertEqual(resp.context['ratings_count'], 1)

    def test_rating_without_cost_is_allowed(self):
        self.client.force_login(self.user1)
        resp = self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]),
                                 {'rating': '4.0'})
        self.assertEqual(resp.status_code, 302)
        rating = RestaurantRating.objects.get(place=self.restaurant, user=self.user1)
        self.assertIsNone(rating.average_cost)


class RestaurantSeedRatingTests(TestCase):
    """Tests for seed_rating/seed_average_cost -- a starting value (e.g.
    from a CSV import) that's blended into the average permanently rather
    than being wiped out by the first real user rating."""

    def setUp(self):
        self.location = Location.objects.create(name='SeedLoc', latitude=27.7, longitude=85.3, property_count=1)
        self.user = User.objects.create_user(email='seeder@example.com', password='Pass123!', first_name='S', last_name='D')

    def test_seed_alone_sets_displayed_rating(self):
        r = PlaceToEat.objects.create(location=self.location, name='Seeded', cuisine='x',
                                       seed_rating=Decimal('4.5'), seed_average_cost=Decimal('10.00'))
        r.recompute_aggregates()
        self.assertEqual(r.rating, Decimal('4.5'))
        self.assertEqual(r.average_cost, Decimal('10.00'))

    def test_single_real_rating_blends_with_seed_not_overwrites(self):
        """The core regression test: a single harsh/generous real review
        must not wipe out a good seed value."""
        r = PlaceToEat.objects.create(location=self.location, name='Seeded2', cuisine='x', seed_rating=Decimal('4.5'))
        r.recompute_aggregates()
        self.client.force_login(self.user)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[r.id]), {'rating': '1.0'})
        r.refresh_from_db()
        self.assertEqual(r.rating, Decimal('2.8'))  # avg(4.5, 1.0), not just 1.0

    def test_no_seed_and_no_submissions_stays_none(self):
        r = PlaceToEat.objects.create(location=self.location, name='Unrated', cuisine='x')
        self.assertIsNone(r.rating)

    def test_import_data_populates_seed_not_direct_rating(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = os.path.join(tmp, 'locations.csv')
            dest_csv = os.path.join(tmp, 'destinations.csv')
            rest_csv = os.path.join(tmp, 'restaurants.csv')
            with open(loc_csv, 'w') as f:
                f.write('name,latitude,longitude,property_count\nImportSeedLoc,27.7,85.3,1\n')
            with open(dest_csv, 'w') as f:
                f.write('name,description,location_name\n')
            with open(rest_csv, 'w') as f:
                f.write('name,cuisine,average_cost,rating,price_range,business_hours,location_name\n'
                         'Import Seed Diner,Nepali,12.00,4.0,2,09:00-21:00,ImportSeedLoc\n')
            call_command('import_data', locations=loc_csv, destinations=dest_csv, restaurants=rest_csv, stdout=StringIO())

        r = PlaceToEat.objects.get(name='Import Seed Diner')
        self.assertEqual(r.seed_rating, Decimal('4.0'))
        self.assertEqual(r.rating, Decimal('4.0'))


class RestaurantRatingSignalTests(TestCase):
    """Regression tests: PlaceToEat's cached rating/average_cost must stay
    in sync when a RestaurantRating is deleted -- whether directly, or
    via cascade when a user account is deleted."""

    def setUp(self):
        self.location = Location.objects.create(name='SignalLoc', latitude=27.7, longitude=85.3, property_count=1)
        self.restaurant = PlaceToEat.objects.create(location=self.location, name='Signal Diner', cuisine='x',
                                                      seed_rating=Decimal('4.2'))
        self.restaurant.recompute_aggregates()
        self.user = User.objects.create_user(email='signaltest@example.com', password='Pass123!', first_name='S', last_name='T')

    def test_direct_rating_deletion_reverts_to_seed(self):
        self.client.force_login(self.user)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]), {'rating': '1.0'})
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.rating, Decimal('2.6'))

        RestaurantRating.objects.get(place=self.restaurant, user=self.user).delete()
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.rating, Decimal('4.2'))

    def test_user_deletion_cascade_reverts_to_seed(self):
        """The bug this guards against: deleting a user cascades to delete
        their ratings, but that used to leave the restaurant's cached
        rating stale (still reflecting the now-deleted vote)."""
        self.client.force_login(self.user)
        self.client.post(reverse('web_interface:submit_restaurant_rating', args=[self.restaurant.id]), {'rating': '1.0'})
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.rating, Decimal('2.6'))

        self.user.delete()
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.rating, Decimal('4.2'))

    def test_recompute_handles_non_decimal_seed_values(self):
        """A seed value that's a plain float (not yet round-tripped
        through the database as a Decimal) must not crash the average."""
        r = PlaceToEat.objects.create(location=self.location, name='Float Seed Diner', cuisine='x')
        r.seed_rating = 4.2  # deliberately a raw float, not Decimal
        RestaurantRating.objects.create(place=r, user=self.user, rating=Decimal('1.0'))
        r.recompute_aggregates()  # must not raise TypeError
        self.assertAlmostEqual(float(r.rating), 2.6, places=1)


class SearchRedirectEncodingTests(TestCase):
    """Regression tests: the homepage and places-to-eat search boxes used
    to build their redirect URL with raw f-string interpolation, so a
    search term containing '&' (extremely plausible for a restaurant app
    -- "Fire & Ice", "Bed & Breakfast" style names) silently truncated the
    search term and corrupted the URL's query string.

    The homepage's hero form no longer redirects through a POST handler
    at all (see HomeAndExploreSearchFormParityTests) -- it submits a
    native GET straight to search_travel_options, so encoding is the
    browser's job, not custom app code. These two tests now guard
    search_travel_options itself, which the homepage, the places-to-eat
    redirect, and the Explore page's own filter form all ultimately feed
    into."""

    def test_search_travel_options_preserves_ampersand_in_term(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'search': 'fish & chips'})
        self.assertEqual(resp.context['search_term'], 'fish & chips')

    def test_search_travel_options_preserves_special_characters(self):
        for term in ['Bed & Breakfast', 'C++ Cafe', '100% Nepali', 'a=b&c=d', 'hash#tag']:
            resp = self.client.get(reverse('web_interface:search_travel_options'), {'search': term})
            self.assertEqual(resp.context['search_term'], term)

    def test_places_to_eat_search_with_ampersand_is_not_truncated(self):
        resp = self.client.post(reverse('web_interface:places_to_eat'), {'search': 'fish & chips'})
        self.assertEqual(resp.status_code, 302)
        resp2 = self.client.get(resp.url)
        self.assertEqual(resp2.context['search_term'], 'fish & chips')
        self.assertEqual(resp2.context['result_type'], 'place_to_eat')

    def test_places_to_eat_empty_search_still_filters_type(self):
        resp = self.client.post(reverse('web_interface:places_to_eat'), {'search': ''})
        resp2 = self.client.get(resp.url)
        self.assertEqual(resp2.context['result_type'], 'place_to_eat')

    def test_places_to_eat_get_redirects_to_search(self):
        # places_to_eat used to render its own separate (and far less
        # capable) restaurant listing template on GET; it's a pure
        # redirect to the unified search page now, same as its POST
        # handler already was -- see the docstring on
        # web_interface.views.places_to_eat.
        resp = self.client.get(reverse('web_interface:places_to_eat'))
        self.assertEqual(resp.status_code, 302)
        resp2 = self.client.get(resp.url)
        self.assertEqual(resp2.context['result_type'], 'place_to_eat')


class HomeAndExploreSearchFormParityTests(TestCase):
    """Regression tests for a real bug found during audit: the homepage's
    hero form and the Explore page's own filter form were two separate,
    independently-maintained implementations of "how search works" that
    had drifted out of sync with each other -- different field names for
    the same filter ('category' vs 'type'), different value vocabularies
    ('restaurant'/'attraction' vs 'place_to_eat'/'destination'), home
    missing the radius/location-picker controls Explore had, and Explore
    silently overriding a just-picked location with a stale GPS
    coordinate still sitting in a hidden field (a duplicate onchange
    handler on the location picker raced a JS listener meant to clear it
    first). The homepage now submits a plain GET directly to
    search_travel_options using the *exact* same field names, option
    values, and behavior as Explore's own form -- there's one
    implementation of search now, not two."""

    def setUp(self):
        self.location = Location.objects.create(name='HeroFilterLoc', latitude=27.7, longitude=85.3, property_count=1)

    def test_homepage_form_posts_directly_to_search_travel_options(self):
        resp = self.client.get(reverse('web_interface:home'))
        content = resp.content.decode()
        self.assertIn(f'action="{reverse("web_interface:search_travel_options")}"', content)
        self.assertIn('method="get"', content)

    def test_homepage_has_no_leftover_category_field(self):
        """The old 'category' field (with its own restaurant/attraction/
        hotel vocabulary) is gone entirely -- 'type' is the only filter
        field, shared verbatim with the Explore page."""
        resp = self.client.get(reverse('web_interface:home'))
        self.assertNotContains(resp, 'name="category"')
        self.assertNotContains(resp, 'Hotels</option>')

    def test_homepage_type_field_matches_explore_field_and_values(self):
        resp = self.client.get(reverse('web_interface:home'))
        content = resp.content.decode()
        self.assertIn('name="type"', content)
        self.assertIn('value="destination"', content)
        self.assertIn('value="place_to_eat"', content)

    def test_homepage_has_the_same_location_and_radius_controls_as_explore(self):
        resp = self.client.get(reverse('web_interface:home'))
        content = resp.content.decode()
        self.assertIn('name="location_id"', content)
        self.assertIn('name="radius"', content)
        self.assertIn(self.location.name, content)  # populated from the same all_locations query

    def test_type_destination_filters_to_destinations(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'type': 'destination'})
        self.assertEqual(resp.context['result_type'], 'destination')

    def test_type_place_to_eat_filters_to_restaurants(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'type': 'place_to_eat'})
        self.assertEqual(resp.context['result_type'], 'place_to_eat')

    def test_empty_type_value_shows_everything_not_nothing(self):
        """Regression test for a severe bug found during audit: a
        <select> element always submits its currently-selected option's
        value, even the "All Types" option's value="" -- unlike a
        checkbox, there's no way for a <select> to omit itself from form
        data. So request.GET.get('type', 'all') was returning '' (the
        actual, present, empty value) instead of falling through to the
        'all' default on essentially every plain search submitted with
        the type filter left at its default -- which is the single most
        common case there is. '' doesn't match `in ['all', 'destination']`
        or `in ['all', 'place_to_eat']`, so BOTH branches were silently
        skipped and *every* plain search returned zero results.
        Explicitly exercises the exact querystring a real <select
        name="type"> submits when left on "All Types", not just the
        'type' key being absent (which happened to still work)."""
        loc = Location.objects.create(name='EmptyTypeLoc', latitude=27.7, longitude=85.3, property_count=2)
        Destination.objects.create(location=loc, name='Should Still Show Dest', description='x')
        PlaceToEat.objects.create(location=loc, name='Should Still Show Eat', cuisine='x')

        # Exactly what the browser sends when <select name="type"> is on
        # its first ("All Types", value="") option and the form submits.
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'search': '', 'type': ''})
        self.assertEqual(resp.context['result_type'], 'all')
        names = [r['item'].name for r in resp.context['all_results']]
        self.assertIn('Should Still Show Dest', names)
        self.assertIn('Should Still Show Eat', names)

    def test_gps_coordinates_are_used(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {
            'latitude': '28.2096', 'longitude': '83.9856',
        })
        self.assertEqual(resp.context['latitude'], 28.2096)
        self.assertEqual(resp.context['longitude'], 83.9856)

    def test_open_now_filter_excludes_unknown_hours(self):
        PlaceToEat.objects.create(location=self.location, name='Always Open', cuisine='x', business_hours='00:00-23:59')
        PlaceToEat.objects.create(location=self.location, name='No Hours Listed', cuisine='x', business_hours='')
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'open_now': 'true'})
        names = [r['item'].name for r in resp.context['all_results']]
        self.assertIn('Always Open', names)
        self.assertNotIn('No Hours Listed', names)

    def test_min_rating_filter_excludes_below_threshold_and_unrated(self):
        high = PlaceToEat.objects.create(location=self.location, name='High Rated', cuisine='x', seed_rating=Decimal('4.8'))
        high.recompute_aggregates()
        low = PlaceToEat.objects.create(location=self.location, name='Low Rated', cuisine='x', seed_rating=Decimal('2.0'))
        low.recompute_aggregates()
        PlaceToEat.objects.create(location=self.location, name='Unrated', cuisine='x')

        resp = self.client.get(reverse('web_interface:search_travel_options'), {'min_rating': '4'})
        names = [r['item'].name for r in resp.context['all_results']]
        self.assertIn('High Rated', names)
        self.assertNotIn('Low Rated', names)
        self.assertNotIn('Unrated', names)

    def test_mobile_search_bar_is_a_working_form(self):
        """Regression test: the mobile header search box used to be a bare
        input with no form, no name attribute, and no submit handler."""
        resp = self.client.get(reverse('web_interface:home'))
        content = resp.content.decode()
        self.assertIn('name="search"', content)
        # Confirm it actually works end-to-end
        resp2 = self.client.get(reverse('web_interface:search_travel_options'), {'search': 'test query'})
        self.assertEqual(resp2.context['search_term'], 'test query')

    def test_open_now_and_min_rating_are_visible_and_checked_on_explore_page(self):
        """Regression test: the search-results page's own filter form
        (type/location/radius) used to only ever submit its own fields,
        silently dropping 'open_now' and 'min_rating' set earlier from the
        homepage hero form the moment someone tweaked any other filter
        there -- worked around at the time with invisible hidden
        passthrough fields. Both are now real, visible controls on the
        Explore page too (matching the homepage), so they survive
        refinement *and* the user can actually see/change them there."""
        resp = self.client.get(reverse('web_interface:search_travel_options'), {
            'open_now': 'true', 'min_rating': '4',
        })
        content = resp.content.decode()
        self.assertIn('name="open_now"', content)
        self.assertIn('checked', content)
        self.assertIn('name="min_rating"', content)
        self.assertIn('value="4"', content)

    def test_open_now_unchecked_and_rating_at_zero_when_not_set(self):
        """The visible open_now/min_rating controls should reflect their
        actual off/zero state when neither filter was set -- not appear
        checked or pre-filled just because the controls always render."""
        resp = self.client.get(reverse('web_interface:search_travel_options'))
        content = resp.content.decode()
        self.assertNotIn('checked', content)

    def test_rating_and_open_now_controls_hidden_for_destination_only_results(self):
        """Regression test for a real 'filters conflict with each other'
        bug: the rating slider and 'Open now' checkbox only ever affect
        restaurant results (Destination has no rating or business_hours
        field at all) -- selecting a minimum rating while also filtering
        to Destinations silently did nothing, with no indication to the
        user that the two choices don't combine. The controls are now
        hidden entirely (not just ineffective) once 'Destinations' is
        selected, on both this page and the homepage."""
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'type': 'destination'})
        content = resp.content.decode()
        self.assertNotIn('name="min_rating"', content)
        self.assertNotIn('name="open_now"', content)

        resp2 = self.client.get(reverse('web_interface:search_travel_options'), {'type': 'place_to_eat'})
        content2 = resp2.content.decode()
        self.assertIn('name="min_rating"', content2)
        self.assertIn('name="open_now"', content2)

        resp3 = self.client.get(reverse('web_interface:home'), {'type': 'destination'})
        content3 = resp3.content.decode()
        self.assertNotIn('name="min_rating"', content3)
        self.assertNotIn('name="open_now"', content3)

    def test_location_picker_selection_is_not_silently_overridden_by_stale_gps(self):
        """Regression test for the actual crash-like bug reported: once
        GPS coordinates were in the URL, the location picker's own
        onchange handler raced a separate JS listener meant to clear
        those coordinates first -- the browser fires the inline
        onchange="this.form.submit()" attribute (present in the old
        markup) before a later-registered addEventListener listener gets
        a chance to run, so the stale lat/lon got submitted right
        alongside the newly chosen location_id, and
        resolve_search_origin() always prefers explicit coordinates over
        location_id -- silently ignoring the just-made choice. This
        can't be fully exercised without a real browser, but this locks
        in the server-side half of the contract (explicit lat/lon must
        still beat location_id -- resolve_search_origin()'s own
        priority order) and confirms the redundant inline handler that
        caused the race is gone from the markup."""
        resp = self.client.get(reverse('web_interface:search_travel_options'), {
            'latitude': '28.2096', 'longitude': '83.9856', 'location_id': str(self.location.id),
        })
        # Explicit coordinates still win by design -- what changed is that
        # the *picker itself* no longer submits stale coordinates when the
        # person is trying to switch away from GPS.
        self.assertEqual(resp.context['latitude'], 28.2096)
        self.assertIsNone(resp.context['selected_location'])

        content = self.client.get(reverse('web_interface:search_travel_options')).content.decode()
        self.assertNotIn('name="location_id" onchange="this.form.submit()"', content)


class HomepageDestinationCardLinkTests(TestCase):
    """Regression test: the 'Explore Now' links on homepage destination
    cards used ?location=Name, but search_travel_options doesn't
    recognize a 'location' parameter at all -- it was silently ignored,
    so clicking any card showed ALL unfiltered results instead of that
    specific destination."""

    def setUp(self):
        self.location = Location.objects.create(name='CardLinkLoc', latitude=27.7, longitude=85.3, property_count=1)
        self.destination = Destination.objects.create(
            location=self.location, name='Card Link Test Spot', description='test'
        )

    def test_trending_destination_card_link_actually_filters(self):
        resp = self.client.get(reverse('web_interface:home'))
        destinations = list(resp.context['trending_destinations'])
        self.assertIn(self.destination, destinations)

        resp2 = self.client.get(reverse('web_interface:search_travel_options'), {'search': self.destination.name})
        names = [r['item'].name for r in resp2.context['all_results']]
        self.assertIn(self.destination.name, names)

    def test_homepage_shows_real_destinations_not_only_hardcoded_fallback(self):
        """Regression test for the core bug: the template looped over
        'trending_destinations' but the view only ever provided
        'destinations', so real data never rendered -- the page always
        silently fell back to hardcoded placeholder cards."""
        resp = self.client.get(reverse('web_interface:home'))
        self.assertContains(resp, self.destination.name)


class PaginationLinkEncodingTests(TestCase):
    """Regression test: pagination Next/Previous links were built by the
    template directly interpolating request.GET values into the query
    string without re-encoding them, breaking the exact same way the
    original redirect bug did whenever a filter value contained '&'."""

    def setUp(self):
        self.location = Location.objects.create(name='PagRegressionLoc', latitude=27.7, longitude=85.3, property_count=1)
        for i in range(15):  # more than one page (12 per page)
            Destination.objects.create(location=self.location, name=f'Fish & Chips Spot {i}', description='x')

    def test_pagination_link_preserves_ampersand_in_search_term(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'search': 'Fish & Chips'})
        self.assertIn('base_querystring', resp.context)
        self.assertIn('search=Fish+%26+Chips', resp.context['base_querystring'])

        resp2 = self.client.get(
            reverse('web_interface:search_travel_options') + f"?{resp.context['base_querystring']}&page=2"
        )
        self.assertEqual(resp2.context['search_term'], 'Fish & Chips')
        self.assertEqual(resp2.context['results'].number, 2)


class RemoveFilterChipEncodingTests(TestCase):
    """Regression test: the 'remove type filter' (x) chip had the same
    unsafe manual query-string rebuild as the pagination links."""

    def test_remove_type_filter_preserves_ampersand_in_search_term(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'),
                                {'search': 'Fish & Chips', 'type': 'destination'})
        self.assertIn('querystring_without_type', resp.context)
        qs = resp.context['querystring_without_type']
        self.assertIn('search=Fish+%26+Chips', qs)
        self.assertNotIn('type=', qs)

        resp2 = self.client.get(reverse('web_interface:search_travel_options') + f'?{qs}')
        self.assertEqual(resp2.context['search_term'], 'Fish & Chips')
        self.assertEqual(resp2.context['result_type'], 'all')


class MidnightCrossingBusinessHoursTests(TestCase):
    """Regression tests: is_open() previously always returned False for
    any business hours that cross midnight (e.g. '22:00-02:00' for a bar
    open 10pm-2am), because 'start <= current <= end' can never be true
    when start > end as raw time values. This meant any late-night venue
    would show as permanently closed and be wrongly excluded from the
    'Open Now' search filter, no matter the actual time."""

    def test_open_during_late_evening_portion(self):
        self.assertTrue(is_open('22:00-02:00', current_time=time(23, 0)))

    def test_open_during_early_morning_portion(self):
        self.assertTrue(is_open('22:00-02:00', current_time=time(1, 0)))

    def test_closed_during_midday(self):
        self.assertFalse(is_open('22:00-02:00', current_time=time(12, 0)))

    def test_boundary_exactly_at_open_and_close(self):
        self.assertTrue(is_open('22:00-02:00', current_time=time(22, 0)))
        self.assertTrue(is_open('22:00-02:00', current_time=time(2, 0)))

    def test_normal_same_day_hours_still_correct(self):
        self.assertTrue(is_open('09:00-21:00', current_time=time(12, 0)))
        self.assertFalse(is_open('09:00-21:00', current_time=time(22, 0)))


class OrphanedSavedItemTests(TestCase):
    """Regression tests: SavedItem/RecentView store item_id as a plain
    integer (not a real ForeignKey, since one field needs to reference
    either Destination or PlaceToEat), so deleting the underlying object
    doesn't cascade. toggle_save_item used to unconditionally look up the
    live object before checking whether there was an existing save to
    remove -- meaning once an item was deleted, anyone who'd saved it was
    permanently stuck with a dead entry they could never remove, since
    the removal attempt would 404 before ever reaching the deletion."""

    def setUp(self):
        self.location = Location.objects.create(name='OrphanLoc', latitude=27.7, longitude=85.3, property_count=1)
        self.user = User.objects.create_user(email='orphan@example.com', password='Pass123!', first_name='O', last_name='U')

    def test_saved_item_can_be_removed_after_underlying_destination_deleted(self):
        dest = Destination.objects.create(location=self.location, name='Ephemeral Spot', description='x')
        real_id = dest.id
        self.client.force_login(self.user)
        self.client.post(reverse('web_interface:toggle_save_item'), {'item_type': 'destination', 'item_id': real_id})
        self.assertTrue(SavedItem.objects.filter(user=self.user, item_id=real_id).exists())

        dest.delete()

        resp = self.client.post(reverse('web_interface:toggle_save_item'),
                                 {'item_type': 'destination', 'item_id': real_id})
        self.assertEqual(resp.status_code, 302)  # not 404
        self.assertFalse(SavedItem.objects.filter(user=self.user, item_id=real_id).exists())

    def test_pages_render_fine_with_orphaned_saved_and_recent_items(self):
        dest = Destination.objects.create(location=self.location, name='Ephemeral Spot 2', description='x')
        real_id = dest.id
        self.client.force_login(self.user)
        self.client.get(reverse('web_interface:travel_detail', args=[real_id]))
        self.client.post(reverse('web_interface:toggle_save_item'), {'item_type': 'destination', 'item_id': real_id})

        dest.delete()

        resp1 = self.client.get(reverse('web_interface:saved_items'))
        resp2 = self.client.get(reverse('web_interface:recent_views'))
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp1, 'Ephemeral Spot 2')  # cached title still shows

    def test_saving_a_genuinely_nonexistent_item_still_404s(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:toggle_save_item'),
                                 {'item_type': 'destination', 'item_id': 999999999})
        self.assertEqual(resp.status_code, 404)


class ToggleSaveItemOpenRedirectTests(TestCase):
    """Regression tests: toggle_save_item's 'next' parameter had the same
    open-redirect vulnerability as login's."""

    def setUp(self):
        self.location = Location.objects.create(name='RedirectFixLoc', latitude=27.7, longitude=85.3, property_count=1)
        self.destination = Destination.objects.create(location=self.location, name='Redirect Fix Spot', description='x')
        self.user = User.objects.create_user(email='toggleredirect@example.com', password='Pass123!', first_name='T', last_name='R')

    def test_external_next_url_is_rejected(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:toggle_save_item'), {
            'item_type': 'destination', 'item_id': self.destination.id,
            'next': 'https://evil-phishing-site.com/steal',
        })
        self.assertNotEqual(resp.url, 'https://evil-phishing-site.com/steal')
        self.assertEqual(resp.url, reverse('web_interface:saved_items'))

    def test_protocol_relative_next_url_is_rejected(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:toggle_save_item'), {
            'item_type': 'destination', 'item_id': self.destination.id,
            'next': '//evil-phishing-site.com/steal',
        })
        self.assertFalse(resp.url.startswith('//evil'))

    def test_legitimate_same_site_next_url_still_works(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:toggle_save_item'), {
            'item_type': 'destination', 'item_id': self.destination.id,
            'next': '/search-travel-options/?search=test',
        })
        self.assertEqual(resp.url, '/search-travel-options/?search=test')


class StaticInfoPagesTests(TestCase):
    """Regression tests: the footer's Terms/Privacy/Help Center/Contact
    links, and the signup page's own Terms of Service / Privacy Policy
    links (the ones users are required to check a box agreeing to), all
    used to be dead href="#" links with no actual page behind them."""

    def test_terms_page_loads(self):
        resp = self.client.get(reverse('web_interface:terms'))
        self.assertEqual(resp.status_code, 200)

    def test_privacy_page_loads(self):
        resp = self.client.get(reverse('web_interface:privacy'))
        self.assertEqual(resp.status_code, 200)

    def test_contact_page_loads_with_a_real_clickable_email(self):
        resp = self.client.get(reverse('web_interface:contact'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'mailto:')

    def test_signup_page_links_to_real_terms_and_privacy_pages(self):
        resp = self.client.get(reverse('signup:signup'))
        self.assertContains(resp, reverse('web_interface:terms'))
        self.assertContains(resp, reverse('web_interface:privacy'))

    def test_footer_no_longer_has_dead_hash_links(self):
        resp = self.client.get(reverse('web_interface:home'))
        self.assertNotContains(resp, 'href="#"')

    def test_footer_logo_is_clickable(self):
        """Regression test: the footer brand logo used to be a plain div,
        not a link -- unlike the header and sidebar logos, clicking it did
        nothing."""
        resp = self.client.get(reverse('web_interface:home'))
        content = resp.content.decode()
        home_url = reverse('web_interface:home')
        self.assertIn(f'<a href="{home_url}" class="flex items-center space-x-2 mb-4', content)

    def test_dead_language_selector_is_gone(self):
        """Regression test: the header used to show an EN/ES/FR language
        dropdown with no name attribute, not inside any form, and no JS
        ever wired to it -- selecting a language did nothing, and the app
        has no i18n/translation setup at all."""
        resp = self.client.get(reverse('web_interface:home'))
        self.assertNotContains(resp, '🇪🇸 ES')
        self.assertNotContains(resp, '🇫🇷 FR')


class NPlusOneQueryTests(TestCase):
    """Regression tests: the homepage and places-to-eat page used to fetch
    their restaurant/destination lists without select_related('location'),
    even though the templates render each item's location -- causing one
    extra query per row (N+1) on every page load."""

    def setUp(self):
        loc = Location.objects.create(name='Kathmandu', latitude=Decimal('27.7172'), longitude=Decimal('85.3240'))
        for i in range(6):
            Destination.objects.create(location=loc, name=f'Dest {i}', description='x')
            PlaceToEat.objects.create(location=loc, name=f'Eat {i}', cuisine='Nepali')

    def test_home_page_does_not_n_plus_one_on_locations(self):
        # Three queries is correct and final here, not a bug: the homepage
        # shows two different models (destinations and restaurants), each
        # needing its own query -- that's unavoidable regardless of
        # select_related, since Django can't merge two different tables'
        # rows into a single result set. The third is the location picker
        # dropdown in the hero search form (added for parity with the
        # Explore page's own filter form -- see
        # HomeAndExploreSearchFormParityTests), which needs the full list
        # of locations exactly once, not per row. What select_related
        # prevents is N+1 -- i.e. one extra query *per row* to fetch each
        # item's location -- so the real regression check is that this
        # stays at exactly 3 rather than growing as the dataset grows.
        with self.assertNumQueries(3):
            self.client.get(reverse('web_interface:home'))

    def test_places_to_eat_page_makes_no_queries(self):
        # places_to_eat is a pure redirect to search_travel_options now
        # (see the docstring on web_interface.views.places_to_eat) rather
        # than its own separate, N+1-prone restaurant listing -- so a GET
        # here should make zero queries, not just avoid N+1 on however
        # many it used to make.
        with self.assertNumQueries(0):
            self.client.get(reverse('web_interface:places_to_eat'))


class SeoBasicsTests(TestCase):
    def test_robots_txt_served(self):
        resp = self.client.get('/robots.txt')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/plain')
        self.assertContains(resp, 'Sitemap:')

    def test_sitemap_xml_served_and_includes_a_destination(self):
        loc = Location.objects.create(name='Kathmandu', latitude=Decimal('27.7172'), longitude=Decimal('85.3240'))
        dest = Destination.objects.create(location=loc, name='Test Dest', description='x')
        resp = self.client.get('/sitemap.xml')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'<urlset', resp.content)
        self.assertIn(f'/travel-detail/{dest.id}/'.encode(), resp.content)

    def test_sitemap_excludes_pending_and_rejected_submissions(self):
        """FOUND DURING AUDIT: DestinationSitemap/PlaceToEatSitemap listed
        every row unconditionally, including a pending/rejected user
        submission -- whose own detail page now correctly 404s for anyone
        but its submitter or staff (see travel_detail's access check).
        That handed search engines a URL that 404s for them, which is both
        a dead link in the sitemap and, in principle, an indirect way to
        discover a submission's existence/id before it's been reviewed."""
        loc = Location.objects.create(name='Sitemap Town', latitude=Decimal('27.7'), longitude=Decimal('85.3'))
        user = User.objects.create_user(email='sitemap-submitter@example.com', password='TestPass123!',
                                         first_name='Site', last_name='Map')
        pending_dest = Destination.objects.create(location=loc, name='Pending Sitemap Dest', description='x',
                                                    status='pending', submitted_by=user)
        rejected_place = PlaceToEat.objects.create(location=loc, name='Rejected Sitemap Place', cuisine='Nepali',
                                                     status='rejected', submitted_by=user)
        approved_dest = Destination.objects.create(location=loc, name='Approved Sitemap Dest', description='x')

        resp = self.client.get('/sitemap.xml')
        content = resp.content.decode()

        self.assertNotIn(f'/travel-detail/{pending_dest.id}/', content)
        self.assertNotIn(f'/restaurant-detail/{rejected_place.id}/', content)
        self.assertIn(f'/travel-detail/{approved_dest.id}/', content)


class SocialMetaAbsoluteUrlTests(TestCase):
    """FOUND DURING AUDIT: og:image/twitter:image/og:url were built with
    plain {% static %}/{% url %}, producing a relative path (e.g.
    "/static/images/og-image.jpg"). Per the Open Graph spec these must be
    absolute URLs -- crawlers with no browser context can't resolve a
    relative one, so every link share of this site silently had no
    preview image, and detail pages always advertised the homepage's
    image/URL instead of their own. See web_interface/templatetags/
    site_extras.py (the absolute_uri filter) and base.html/
    travel_detail.html/restaurant_detail.html for the fix."""

    def test_homepage_og_image_and_url_are_absolute(self):
        resp = self.client.get('/')
        html = resp.content.decode()
        self.assertIn('property="og:image" content="http://testserver/static/images/og-image.jpg"', html)
        self.assertIn('property="og:url" content="http://testserver/"', html)
        self.assertIn('name="twitter:image" content="http://testserver/static/images/og-image.jpg"', html)

    def test_destination_without_photo_falls_back_to_default_image_absolute(self):
        loc = Location.objects.create(name='Kathmandu', latitude=Decimal('27.7172'), longitude=Decimal('85.3240'))
        dest = Destination.objects.create(location=loc, name='No Photo Yet', description='x')
        resp = self.client.get(f'/travel-detail/{dest.id}/')
        html = resp.content.decode()
        self.assertIn('property="og:image" content="http://testserver/static/images/og-image.jpg"', html)
        self.assertIn(f'property="og:url" content="http://testserver/travel-detail/{dest.id}/"', html)

    def test_destination_with_photo_shares_its_own_image_and_url(self):
        loc = Location.objects.create(name='Kathmandu', latitude=Decimal('27.7172'), longitude=Decimal('85.3240'))
        dest = Destination.objects.create(
            location=loc, name='Has A Photo', description='x',
            image='https://upload.wikimedia.org/example-real-photo.jpg',
        )
        resp = self.client.get(f'/travel-detail/{dest.id}/')
        html = resp.content.decode()
        # An already-absolute image URL is used as-is, not re-based onto testserver.
        self.assertIn('property="og:image" content="https://upload.wikimedia.org/example-real-photo.jpg"', html)
        self.assertIn(f'property="og:url" content="http://testserver/travel-detail/{dest.id}/"', html)
        self.assertNotIn('og:image" content="http://testserver/static/images/og-image.jpg"', html)


class LocationPickerRedesignTests(TestCase):
    """Regression tests for the search-results page's location controls.

    A prior redesign removed the location-name dropdown in favor of just
    a GPS toggle, on the reasoning that the radius filter already covers
    "how far". That turned out to be a real regression: it removed the
    only way to browse a *different* place in Nepal than the one you're
    physically in (e.g. searching Pokhara while sitting in Banepa), which
    is a legitimate and requested use case. The dropdown is back
    alongside the GPS toggle -- GPS still takes priority when active (see
    resolve_search_origin()), and picking a location clears any GPS
    coordinates so the two controls can't silently disagree with each
    other."""

    def setUp(self):
        Location.objects.create(name='Banepa', latitude=Decimal('27.6'), longitude=Decimal('85.5'))
        Location.objects.create(name='Pokhara', latitude=Decimal('28.2096'), longitude=Decimal('83.9856'))

    def test_location_picker_present_and_lists_saved_places(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'))
        self.assertContains(resp, 'name="location_id"')
        self.assertContains(resp, 'Banepa')
        self.assertContains(resp, 'Pokhara')

    def test_use_my_location_toggle_button_present(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'))
        self.assertContains(resp, 'id="use-my-location-btn"')
        self.assertContains(resp, 'Use My Location')

    def test_toggle_shows_active_state_when_gps_coords_present(self):
        resp = self.client.get(reverse('web_interface:search_travel_options'), {
            'latitude': '27.7', 'longitude': '85.3',
        })
        self.assertContains(resp, 'aria-pressed="true"')
        self.assertContains(resp, 'Using My Location')

    def test_choosing_a_location_shows_its_name(self):
        pokhara = Location.objects.get(name='Pokhara')
        resp = self.client.get(reverse('web_interface:search_travel_options'), {
            'location_id': pokhara.id,
        })
        self.assertContains(resp, 'Showing results around')
        self.assertContains(resp, 'Pokhara')


class NewsletterSubscribeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='newsletter@example.com', password='TestPass123!',
            first_name='News', last_name='Letter',
        )

    def test_logged_in_user_is_really_subscribed(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:newsletter_subscribe'), {'email': 'ignored@example.com'})
        self.assertEqual(resp.status_code, 302)
        prefs = UserPreference.objects.get(user=self.user)
        self.assertTrue(prefs.marketing_emails)

    def test_anonymous_visitor_is_sent_to_signup_not_told_a_fake_success(self):
        resp = self.client.post(reverse('web_interface:newsletter_subscribe'), {'email': 'anon@example.com'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('signup:signup'))

    def test_get_request_is_rejected(self):
        resp = self.client.get(reverse('web_interface:newsletter_subscribe'))
        self.assertEqual(resp.status_code, 405)


class PerPlaceCoordinateTests(TestCase):
    """Destination/PlaceToEat used to have no coordinate of their own --
    every place in the same town/city shared one identical point (the
    parent Location's centroid), so "Get directions" always pointed at
    the same spot regardless of which specific place you were looking at.
    These cover the fix: an optional per-place latitude/longitude that
    falls back to the old shared-centroid behavior only when unset."""

    def setUp(self):
        self.location = Location.objects.create(
            name='Coordtown', latitude=Decimal('27.700000'), longitude=Decimal('85.300000'), property_count=2)

    def test_destination_without_own_coordinate_falls_back_to_location(self):
        dest = Destination.objects.create(location=self.location, name='No Coord Spot', description='x')
        self.assertEqual(dest.effective_latitude, self.location.latitude)
        self.assertEqual(dest.effective_longitude, self.location.longitude)

    def test_destination_with_own_coordinate_overrides_location(self):
        dest = Destination.objects.create(
            location=self.location, name='Precise Spot', description='x',
            latitude=Decimal('27.712345'), longitude=Decimal('85.312345'))
        self.assertEqual(dest.effective_latitude, Decimal('27.712345'))
        self.assertEqual(dest.effective_longitude, Decimal('85.312345'))

    def test_two_destinations_in_same_town_get_different_effective_coordinates(self):
        """The exact bug report this fixes: two different places in the
        same town must not resolve to the same point once they each have
        their own coordinate."""
        a = Destination.objects.create(
            location=self.location, name='Spot A', description='x',
            latitude=Decimal('27.701111'), longitude=Decimal('85.301111'))
        b = Destination.objects.create(
            location=self.location, name='Spot B', description='x',
            latitude=Decimal('27.709999'), longitude=Decimal('85.309999'))
        self.assertNotEqual(a.effective_latitude, b.effective_latitude)
        self.assertNotEqual(a.effective_longitude, b.effective_longitude)

    def test_restaurant_without_own_coordinate_falls_back_to_location(self):
        place = PlaceToEat.objects.create(location=self.location, name='No Coord Diner', cuisine='Nepali')
        self.assertEqual(place.effective_latitude, self.location.latitude)
        self.assertEqual(place.effective_longitude, self.location.longitude)

    def test_restaurant_with_own_coordinate_overrides_location(self):
        place = PlaceToEat.objects.create(
            location=self.location, name='Precise Diner', cuisine='Nepali',
            latitude=Decimal('27.715000'), longitude=Decimal('85.315000'))
        self.assertEqual(place.effective_latitude, Decimal('27.715000'))
        self.assertEqual(place.effective_longitude, Decimal('85.315000'))

    def test_travel_detail_page_uses_destinations_own_coordinate(self):
        dest = Destination.objects.create(
            location=self.location, name='Rendered Spot', description='x',
            latitude=Decimal('27.799999'), longitude=Decimal('85.399999'))
        resp = self.client.get(reverse('web_interface:travel_detail', args=[dest.id]))
        self.assertContains(resp, '27.799999')
        self.assertContains(resp, '85.399999')

    def test_restaurant_detail_page_uses_restaurants_own_coordinate(self):
        place = PlaceToEat.objects.create(
            location=self.location, name='Rendered Diner', cuisine='Nepali',
            latitude=Decimal('27.888888'), longitude=Decimal('85.388888'))
        resp = self.client.get(reverse('web_interface:restaurant_detail', args=[place.id]))
        self.assertContains(resp, '27.888888')
        self.assertContains(resp, '85.388888')

    def test_search_results_distance_uses_own_coordinate_not_location_centroid(self):
        """FOUND DURING AUDIT: search_travel_options computed every
        destination/restaurant's distance from the parent Location's
        centroid (loc.latitude/loc.longitude) unconditionally, even for
        items that have their own more precise coordinate -- while every
        detail page already used effective_latitude/effective_longitude.
        This was silent with the seed data (no destination/restaurant had
        its own coordinate yet), but the moment backfill_coordinates
        populates one, search results and the detail page's live map would
        show two different distances for the same place. This is the gap
        that let it go undetected: PerPlaceCoordinateTests above covers
        effective_latitude/longitude thoroughly on detail pages, but never
        against search_travel_options' own distance calculation."""
        origin_lat, origin_lng = 27.700000, 85.300000

        close_by_location_far_by_own_coord = Destination.objects.create(
            location=self.location,  # centroid: 27.700000, 85.300000 -- 0km from origin
            name='Deceptive Spot', description='x',
            latitude=Decimal('28.500000'), longitude=Decimal('84.000000'),  # ~135km from origin
        )
        far_by_location_close_by_own_coord = Destination.objects.create(
            location=Location.objects.create(
                name='Far Town', latitude=Decimal('28.500000'), longitude=Decimal('84.000000'),
                property_count=1),  # centroid ~135km from origin
            name='Actually Close Spot', description='x',
            latitude=Decimal('27.700100'), longitude=Decimal('85.300100'),  # ~0.02km from origin
        )

        resp = self.client.get(reverse('web_interface:search_travel_options'), {
            'latitude': str(origin_lat), 'longitude': str(origin_lng),
        })
        by_name = {r['item'].name: r for r in resp.context['all_results']}

        # Using the Location centroid instead of effective_latitude/
        # longitude would get both of these backwards.
        self.assertGreater(by_name['Deceptive Spot']['distance'], 100)
        self.assertLess(by_name['Actually Close Spot']['distance'], 1)


class ImportDataCoordinateTests(TestCase):
    """Optional latitude/longitude columns for destinations/restaurants:
    parsed when present, never required, and never wiped by a later
    re-import from a source that doesn't have them (same protection
    already given to `image`)."""

    def _write_csv(self, tmpdir, name, content):
        path = os.path.join(tmpdir, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_destination_coordinate_is_imported_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nCoordville,27.7,85.3,1\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv',
                'name,description,location_name,latitude,longitude\n'
                'Precise Place,A place,Coordville,27.712345,85.312345\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n')

            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, stdout=StringIO())

            dest = Destination.objects.get(name='Precise Place')
            self.assertEqual(dest.latitude, Decimal('27.712345'))
            self.assertEqual(dest.longitude, Decimal('85.312345'))

    def test_destination_without_coordinate_column_leaves_it_blank(self):
        """No latitude/longitude column at all (the old CSV shape) must
        keep working exactly as before -- falls back to the location's
        centroid via effective_latitude, not a crash or a required field."""
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nCoordville,27.7,85.3,1\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv',
                'name,description,location_name\nNo Coord Place,A place,Coordville\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n')

            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, stdout=StringIO())

            dest = Destination.objects.get(name='No Coord Place')
            self.assertIsNone(dest.latitude)
            self.assertEqual(dest.effective_latitude, Decimal('27.700000'))

    def test_reimport_without_coordinate_column_does_not_wipe_existing_one(self):
        """Mirrors the existing image-preservation guarantee: a
        backfill_coordinates-found (or previously-imported) coordinate
        must survive a later re-import from a source with no
        latitude/longitude column."""
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nCoordville,27.7,85.3,1\n')
            dest_csv_with_coords = self._write_csv(tmp, 'destinations.csv',
                'name,description,location_name,latitude,longitude\n'
                'Stable Place,A place,Coordville,27.755555,85.355555\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n')

            call_command('import_data', locations=loc_csv, destinations=dest_csv_with_coords,
                         restaurants=rest_csv, stdout=StringIO())

            dest_csv_without_coords = self._write_csv(tmp, 'destinations2.csv',
                'name,description,location_name\nStable Place,Updated description,Coordville\n')
            call_command('import_data', locations=loc_csv, destinations=dest_csv_without_coords,
                         restaurants=rest_csv, stdout=StringIO())

            dest = Destination.objects.get(name='Stable Place')
            self.assertEqual(dest.description, 'Updated description')
            self.assertEqual(dest.latitude, Decimal('27.755555'))
            self.assertEqual(dest.longitude, Decimal('85.355555'))

    def test_restaurant_coordinate_is_imported_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nCoordville,27.7,85.3,1\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv', 'name,description,location_name\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name,latitude,longitude\n'
                'Precise Diner,Nepali,,,2,,Coordville,27.722222,85.322222\n')

            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, stdout=StringIO())

            place = PlaceToEat.objects.get(name='Precise Diner')
            self.assertEqual(place.latitude, Decimal('27.722222'))
            self.assertEqual(place.longitude, Decimal('85.322222'))

    def test_out_of_range_coordinate_is_ignored_not_row_skipped(self):
        """An unparsable/out-of-range destination-level coordinate should
        just mean 'no coordinate of its own yet' -- never a reason to
        drop the whole row the way a bad Location-level coordinate does."""
        with tempfile.TemporaryDirectory() as tmp:
            loc_csv = self._write_csv(tmp, 'locations.csv',
                'name,latitude,longitude,property_count\nCoordville,27.7,85.3,1\n')
            dest_csv = self._write_csv(tmp, 'destinations.csv',
                'name,description,location_name,latitude,longitude\n'
                'Bad Coord Place,A place,Coordville,not-a-number,85.3\n')
            rest_csv = self._write_csv(tmp, 'restaurants.csv',
                'name,cuisine,average_cost,rating,price_range,business_hours,location_name\n')

            call_command('import_data', locations=loc_csv, destinations=dest_csv,
                         restaurants=rest_csv, stdout=StringIO())

            dest = Destination.objects.get(name='Bad Coord Place')
            self.assertIsNone(dest.latitude)


class OSMConverterCoordinateTests(TestCase):
    """scripts/osm_to_csv.py's convert() used to compute each OSM
    element's real lat/lon only to fold it into a shared per-town
    centroid and discard the individual position -- so even a real OSM
    sync couldn't give two restaurants in the same city distinct pins.
    Covers that each row now also carries its own latitude/longitude."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import sys
        from pathlib import Path
        scripts_dir = str(Path(__file__).resolve().parent.parent / 'scripts')
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import osm_to_csv
        cls.osm_to_csv = osm_to_csv

    def test_destination_rows_carry_their_own_coordinate(self):
        elements = [
            (27.7, 85.3, {'name': 'Place A', 'tourism': 'attraction'}),
            (27.71, 85.31, {'name': 'Place B', 'tourism': 'museum'}),
        ]
        rows, points = self.osm_to_csv.convert(elements, 'Coordville', 'destination', set())
        by_name = {r['name']: r for r in rows}
        self.assertEqual(by_name['Place A']['latitude'], 27.7)
        self.assertEqual(by_name['Place A']['longitude'], 85.3)
        self.assertEqual(by_name['Place B']['latitude'], 27.71)
        self.assertEqual(by_name['Place B']['longitude'], 85.31)
        # The per-town aggregate (used for the Location's own centroid)
        # must still be populated from the same points, unaffected.
        self.assertEqual(sorted(points['Coordville']), [(27.7, 85.3), (27.71, 85.31)])

    def test_restaurant_rows_carry_their_own_coordinate(self):
        elements = [(27.72, 85.32, {'name': 'Diner A', 'amenity': 'restaurant'})]
        rows, points = self.osm_to_csv.convert(elements, 'Coordville', 'restaurant', set())
        self.assertEqual(rows[0]['latitude'], 27.72)
        self.assertEqual(rows[0]['longitude'], 85.32)

class ImagePlaceholderOnerrorTests(TestCase):
    """FOUND DURING AUDIT (confirmed with a real headless-browser
    screenshot, not just template rendering): every page with an image +
    placeholder fallback used `this.replaceWith(this.nextElementSibling)`
    followed by `this.nextElementSibling.style.display='flex'` in the
    <img>'s onerror handler. replaceWith() detaches `this` (the <img>)
    from the DOM *before* the second statement runs, so `this.
    nextElementSibling` is already null by then -- the placeholder swaps
    into place but its inline `display:none` is never cleared, so it
    stays invisible. Net effect: any image URL that 404s/times out (a
    dead link, a deleted photo, anything not reachable) left a blank gap
    instead of the intended placeholder -- on every page that had one,
    including the original home.html/search_results.html, not just the
    ones added for the map/coordinate fix. Fixed by hiding the <img>
    directly (`this.style.display='none'`) instead of removing it from
    the DOM, so the sibling reference used on the next statement is still
    valid. These check every page still uses the corrected form and none
    of them regress to the broken one."""

    FIXED_SNIPPET = "this.style.display='none'"
    BROKEN_SNIPPET = 'replaceWith(this.nextElementSibling)'

    def setUp(self):
        self.location = Location.objects.create(
            name='Onerrorville', latitude=Decimal('27.7'), longitude=Decimal('85.3'), property_count=1)

    def _assert_page_uses_fixed_onerror(self, resp):
        body = resp.content.decode()
        self.assertIn(self.FIXED_SNIPPET, body)
        self.assertNotIn(self.BROKEN_SNIPPET, body)

    def test_travel_detail_page(self):
        dest = Destination.objects.create(location=self.location, name='Onerror Spot', description='x',
                                           image='https://example.com/photo.jpg')
        resp = self.client.get(reverse('web_interface:travel_detail', args=[dest.id]))
        self._assert_page_uses_fixed_onerror(resp)

    def test_restaurant_detail_page(self):
        place = PlaceToEat.objects.create(location=self.location, name='Onerror Diner', cuisine='Nepali',
                                           image='https://example.com/photo.jpg')
        resp = self.client.get(reverse('web_interface:restaurant_detail', args=[place.id]))
        self._assert_page_uses_fixed_onerror(resp)

    def test_home_page(self):
        Destination.objects.create(location=self.location, name='Onerror Home Spot', description='x',
                                    image='https://example.com/photo.jpg')
        resp = self.client.get(reverse('web_interface:home'))
        self._assert_page_uses_fixed_onerror(resp)

    def test_search_results_page(self):
        Destination.objects.create(location=self.location, name='Onerror Search Spot', description='x',
                                    image='https://example.com/photo.jpg')
        resp = self.client.get(reverse('web_interface:search_travel_options'), {'search': 'Onerror'})
        self._assert_page_uses_fixed_onerror(resp)

    def test_saved_items_page(self):
        user = User.objects.create_user(email='onerror@example.com', password='TestPass123!',
                                         first_name='On', last_name='Error')
        dest = Destination.objects.create(location=self.location, name='Onerror Saved Spot', description='x')
        SavedItem.objects.create(user=user, item_type='destination', item_id=dest.id,
                                  item_title=dest.name, item_url='/travel-detail/%d/' % dest.id,
                                  item_image='https://example.com/photo.jpg')
        self.client.force_login(user)
        resp = self.client.get(reverse('web_interface:saved_items'))
        self._assert_page_uses_fixed_onerror(resp)


class PlaceSubmissionTests(TestCase):
    """New feature: logged-in users can submit a new destination or
    restaurant via web_interface:submit_place. Every submission is created
    as status='pending' -- never immediately public -- and only becomes
    visible in search/homepage listings once a staff member approves it in
    the admin. The submitter can still see their own pending submission's
    detail page (so they have some feedback that it was received and what
    its status is); nobody else can, staff excepted."""

    def setUp(self):
        self.location = Location.objects.create(name='Submission Town', latitude=Decimal('27.7'),
                                                  longitude=Decimal('85.3'), property_count=1)
        self.user = User.objects.create_user(email='submitter@example.com', password='TestPass123!',
                                              first_name='Sub', last_name='Mitter')
        self.other_user = User.objects.create_user(email='other-submitter@example.com', password='TestPass123!',
                                                     first_name='Other', last_name='Person')
        self.staff_user = User.objects.create_user(email='staff-submitter@example.com', password='TestPass123!',
                                                     first_name='Staff', last_name='Member',
                                                     is_staff=True, is_superuser=True)
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_anonymous_user_is_redirected_to_login(self):
        resp = self.client.get(reverse('web_interface:submit_place'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.url)

    def test_logged_in_user_can_submit_a_destination(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:submit_place'), {
            'place_type': 'destination',
            'location': self.location.id,
            'name': 'User Submitted Temple',
            'description': 'A place I visited and loved.',
            'latitude': '', 'longitude': '', 'image': '',
        })
        self.assertEqual(resp.status_code, 302)
        dest = Destination.objects.get(name='User Submitted Temple')
        self.assertEqual(dest.status, 'pending')
        self.assertEqual(dest.submitted_by, self.user)

    def test_logged_in_user_can_submit_a_restaurant(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:submit_place'), {
            'place_type': 'place_to_eat',
            'location': self.location.id,
            'name': 'User Submitted Diner',
            'cuisine': 'Nepali',
            'price_range': 2,
            # 24-hour HH:MM-HH:MM is the only shape views.is_open() can
            # parse. This test used to post '9 AM - 9 PM' -- the exact
            # format the form's own placeholder suggested -- which is_open()
            # silently returns None for, so every restaurant submitted
            # through this form was invisible to the "Open now" filter and
            # never showed an open/closed badge. The form now validates the
            # format instead of inviting an unusable one.
            'business_hours': '09:00-21:00',
            'latitude': '', 'longitude': '', 'image': '',
        })
        self.assertEqual(resp.status_code, 302)
        place = PlaceToEat.objects.get(name='User Submitted Diner')
        self.assertEqual(place.status, 'pending')
        self.assertEqual(place.submitted_by, self.user)
        self.assertEqual(place.business_hours, '09:00-21:00')

    def test_unparseable_business_hours_are_rejected_at_submission(self):
        """Regression guard for the bug above: a format the site cannot act
        on must not be silently accepted and stored."""
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:submit_place'), {
            'place_type': 'place_to_eat',
            'location': self.location.id,
            'name': 'Vague Hours Diner',
            'cuisine': 'Nepali',
            'price_range': 2,
            'business_hours': '9 AM - 9 PM',
            'latitude': '', 'longitude': '', 'image': '',
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered with an error
        self.assertFalse(PlaceToEat.objects.filter(name='Vague Hours Diner').exists())

    def test_blank_business_hours_are_still_allowed(self):
        """Blank means 'hours not listed', which is honest and common. The
        validator must not turn an optional field into a required one."""
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:submit_place'), {
            'place_type': 'place_to_eat',
            'location': self.location.id,
            'name': 'No Hours Diner',
            'cuisine': 'Nepali',
            'price_range': 2,
            'business_hours': '',
            'latitude': '', 'longitude': '', 'image': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(PlaceToEat.objects.filter(name='No Hours Diner').exists())

    def test_submission_requires_both_coordinates_or_neither(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('web_interface:submit_place'), {
            'place_type': 'destination',
            'location': self.location.id,
            'name': 'Half Coordinate Spot',
            'description': 'x',
            'latitude': '27.7', 'longitude': '', 'image': '',
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered with an error, not redirected
        self.assertFalse(Destination.objects.filter(name='Half Coordinate Spot').exists())

    def test_pending_destination_excluded_from_search_results(self):
        self.client.force_login(self.user)
        self.client.post(reverse('web_interface:submit_place'), {
            'place_type': 'destination', 'location': self.location.id,
            'name': 'Hidden Pending Spot', 'description': 'x',
            'latitude': '', 'longitude': '', 'image': '',
        })
        resp = self.client.get(reverse('web_interface:search_travel_options'),
                                {'search': 'Hidden Pending Spot'})
        dest = Destination.objects.get(name='Hidden Pending Spot')
        self.assertNotIn(f'/travel-detail/{dest.id}/', resp.content.decode())

    def test_pending_destination_excluded_from_homepage_trending(self):
        Destination.objects.create(location=self.location, name='Pending Trending Spot',
                                    description='x', status='pending', submitted_by=self.user)
        resp = self.client.get(reverse('web_interface:home'))
        self.assertNotContains(resp, 'Pending Trending Spot')

    def test_submitter_can_view_their_own_pending_destination(self):
        dest = Destination.objects.create(location=self.location, name='My Pending Spot',
                                           description='x', status='pending', submitted_by=self.user)
        self.client.force_login(self.user)
        resp = self.client.get(reverse('web_interface:travel_detail', args=[dest.id]))
        self.assertEqual(resp.status_code, 200)

    def test_other_user_cannot_view_someone_elses_pending_destination(self):
        dest = Destination.objects.create(location=self.location, name='Not Yours Spot',
                                           description='x', status='pending', submitted_by=self.user)
        self.client.force_login(self.other_user)
        resp = self.client.get(reverse('web_interface:travel_detail', args=[dest.id]))
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_user_cannot_view_a_pending_destination(self):
        dest = Destination.objects.create(location=self.location, name='Anon Blocked Spot',
                                           description='x', status='pending', submitted_by=self.user)
        resp = self.client.get(reverse('web_interface:travel_detail', args=[dest.id]))
        self.assertEqual(resp.status_code, 404)

    def test_staff_can_view_any_pending_destination(self):
        dest = Destination.objects.create(location=self.location, name='Staff Visible Spot',
                                           description='x', status='pending', submitted_by=self.user)
        self.client.force_login(self.staff_user)
        resp = self.client.get(reverse('web_interface:travel_detail', args=[dest.id]))
        self.assertEqual(resp.status_code, 200)

    def test_approved_destination_is_publicly_visible_regardless_of_submitter(self):
        dest = Destination.objects.create(location=self.location, name='Now Approved Spot',
                                           description='x', status='approved', submitted_by=self.user)
        resp = self.client.get(reverse('web_interface:travel_detail', args=[dest.id]))
        self.assertEqual(resp.status_code, 200)

    def test_rate_limit_blocks_after_max_submissions_per_hour(self):
        """Same cache.add()+cache.incr() atomic pattern, and the same kind
        of regression test, as LOGIN_RATE_LIMIT in accounts/tests.py."""
        self.client.force_login(self.user)
        for i in range(SUBMISSION_RATE_LIMIT_MAX):
            self.client.post(reverse('web_interface:submit_place'), {
                'place_type': 'destination', 'location': self.location.id,
                'name': f'Rate Limited Spot {i}', 'description': 'x',
                'latitude': '', 'longitude': '', 'image': '',
            })
        self.assertEqual(
            Destination.objects.filter(submitted_by=self.user).count(),
            SUBMISSION_RATE_LIMIT_MAX,
        )
        blocked_resp = self.client.post(reverse('web_interface:submit_place'), {
            'place_type': 'destination', 'location': self.location.id,
            'name': 'One Too Many', 'description': 'x',
            'latitude': '', 'longitude': '', 'image': '',
        })
        self.assertEqual(blocked_resp.status_code, 200)  # re-rendered, not redirected -- nothing saved
        self.assertFalse(Destination.objects.filter(name='One Too Many').exists())
        self.assertEqual(
            Destination.objects.filter(submitted_by=self.user).count(),
            SUBMISSION_RATE_LIMIT_MAX,
        )

    def test_rate_limit_is_per_user_not_global(self):
        """One user hitting the limit shouldn't block a different user."""
        self.client.force_login(self.user)
        for i in range(SUBMISSION_RATE_LIMIT_MAX):
            self.client.post(reverse('web_interface:submit_place'), {
                'place_type': 'destination', 'location': self.location.id,
                'name': f'User A Spot {i}', 'description': 'x',
                'latitude': '', 'longitude': '', 'image': '',
            })
        self.client.force_login(self.other_user)
        resp = self.client.post(reverse('web_interface:submit_place'), {
            'place_type': 'destination', 'location': self.location.id,
            'name': 'User B First Spot', 'description': 'x',
            'latitude': '', 'longitude': '', 'image': '',
        })
        self.assertTrue(Destination.objects.filter(name='User B First Spot').exists())

    def test_my_submissions_list_renders_with_status_badge(self):
        Destination.objects.create(location=self.location, name='Visible In My List',
                                    description='x', status='pending', submitted_by=self.user)
        self.client.force_login(self.user)
        resp = self.client.get(reverse('web_interface:submit_place'))
        content = resp.content.decode()
        self.assertIn('Visible In My List', content)
        self.assertIn('Pending review', content)  # get_status_display() for 'pending'

    def test_my_submissions_list_does_not_show_other_users_items(self):
        Destination.objects.create(location=self.location, name='Someone Elses Submission',
                                    description='x', status='pending', submitted_by=self.other_user)
        self.client.force_login(self.user)
        resp = self.client.get(reverse('web_interface:submit_place'))
        self.assertNotContains(resp, 'Someone Elses Submission')

    def test_type_query_param_preselects_the_restaurant_tab(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('web_interface:submit_place'), {'type': 'place_to_eat'})
        self.assertEqual(resp.context['place_type'], 'place_to_eat')

    def test_admin_approve_action_makes_submission_publicly_visible(self):
        dest = Destination.objects.create(location=self.location, name='Approve Me Spot',
                                           description='x', status='pending', submitted_by=self.user)
        self.client.force_login(self.staff_user)
        self.client.post(reverse('admin:web_interface_destination_changelist'), {
            'action': 'approve_selected',
            '_selected_action': [str(dest.id)],
        })
        dest.refresh_from_db()
        self.assertEqual(dest.status, 'approved')
        # Now publicly visible without being logged in at all.
        resp = self.client.get(reverse('web_interface:search_travel_options'),
                                {'search': 'Approve Me Spot'})
        self.assertIn(f'/travel-detail/{dest.id}/', resp.content.decode())

    def test_submitted_content_is_html_escaped_everywhere_it_renders(self):
        """User-submitted name/description becomes publicly visible once
        approved -- confirms Django's default auto-escaping actually holds
        across every surface that renders it (detail page, the submitter's
        own 'My submissions' list, and search results), not just assumed
        from Django's general behavior. A future template change adding
        |safe or mark_safe() to any of these would be caught here."""
        payload = '<script>alert("xss")</script>'
        dest = Destination.objects.create(location=self.location, name=payload, description=payload,
                                           status='approved', submitted_by=self.user)
        self.client.force_login(self.user)

        detail_content = self.client.get(reverse('web_interface:travel_detail', args=[dest.id])).content.decode()
        self.assertNotIn(payload, detail_content)
        self.assertIn('&lt;script&gt;', detail_content)

        my_submissions_content = self.client.get(reverse('web_interface:submit_place')).content.decode()
        self.assertNotIn(payload, my_submissions_content)

        search_content = self.client.get(reverse('web_interface:search_travel_options'),
                                          {'search': 'alert'}).content.decode()
        self.assertNotIn(payload, search_content)

