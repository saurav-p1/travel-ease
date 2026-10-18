"""
Tests for scripts/open_data.py -- the verification rules that decide
whether a coordinate or a photo may be attached to a place.

These deliberately import no Django and touch no database or network, so
they run under `python manage.py test` *and* standalone with

    python -m unittest web_interface.tests_open_data -v

Every test below corresponds to a specific way the previous data run went
wrong, so a regression brings back a failure that actually happened rather
than a hypothetical one.
"""
import json
import unittest
import urllib.error

from scripts.open_data import (
    MAX_CUISINE, MAX_IMAGE_URL, MAX_NAME,
    HttpClient, RateLimited, assign_to_nearest, build_overpass_query,
    build_user_agent, clean_business_hours, commons_file_url,
    completeness_score, contact_looks_real, describe_destination,
    direct_image_from_tags, element_key, element_point,
    fetch_wikidata_entities, fetch_wikipedia_pages, haversine_km,
    looks_like_placeholder, normalise_name, parse_wikipedia_tag,
    fetch_overpass, fits_model_fields, pretty_cuisine, price_range_from_tags,
    verified_wikipedia_image, wiki_file_url,
)


# --------------------------------------------------------------------------
# Fake HTTP plumbing
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, headers=None):
        self._body = json.dumps(payload).encode('utf-8')
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(responses):
    """Return an opener that yields each queued response/exception in turn
    and records the requests it saw."""
    calls = []

    def opener(req, timeout=None):
        calls.append(req)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    opener.calls = calls
    return opener


def client_with(responses):
    return HttpClient(contact='https://example.org/travelease', min_interval=0,
                      sleep=lambda _s: None, opener=fake_opener(responses))


# --------------------------------------------------------------------------

class UserAgentTests(unittest.TestCase):
    """The 429 storm's root cause: a User-Agent with no contact info gets
    sorted into Wikimedia's restrictive anonymous tier."""

    def test_old_style_contact_is_rejected(self):
        self.assertFalse(contact_looks_real(''))
        self.assertFalse(contact_looks_real('educational project; contact via project README'))
        self.assertFalse(contact_looks_real(None))

    def test_url_or_email_is_accepted(self):
        self.assertTrue(contact_looks_real('https://github.com/someone/travelease'))
        self.assertTrue(contact_looks_real('supsapo@example.com'))

    def test_user_agent_carries_the_contact(self):
        ua = build_user_agent('https://example.org/travelease')
        self.assertIn('https://example.org/travelease', ua)
        self.assertTrue(ua.startswith('TravelEase/'))


class BackoffTests(unittest.TestCase):
    """The old code turned a 429 into 'left blank', which made throttling
    indistinguishable from a place genuinely having no photo."""

    def test_retries_a_429_then_succeeds(self):
        err = urllib.error.HTTPError('u', 429, 'Too Many Requests', {'Retry-After': '1'}, None)
        client = client_with([err, {'ok': True}])
        self.assertEqual(client.get_json('https://example.org/api'), {'ok': True})
        self.assertEqual(client.stats['retries'], 1)
        self.assertEqual(client.stats['throttled'], 1)

    def test_persistent_429_raises_rate_limited_not_empty_result(self):
        err = urllib.error.HTTPError('u', 429, 'Too Many Requests', {}, None)
        client = HttpClient(contact='https://example.org/x', min_interval=0, max_retries=2,
                            sleep=lambda _s: None, opener=fake_opener([err, err, err]))
        with self.assertRaises(RateLimited):
            client.get_json('https://example.org/api')

    def test_a_404_is_not_retried(self):
        err = urllib.error.HTTPError('u', 404, 'Not Found', {}, None)
        client = client_with([err])
        with self.assertRaises(urllib.error.HTTPError):
            client.get_json('https://example.org/api')

    def test_post_is_form_encoded(self):
        """Overpass answers a raw query body with HTTP 406; it wants
        `data=<query>` as a form field."""
        client = client_with([{'elements': []}])
        client.post_json('https://overpass-api.de/api/interpreter', {'data': 'out;'})
        req = client._opener.calls[0]
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.headers['Content-type'], 'application/x-www-form-urlencoded')
        self.assertEqual(req.data, b'data=out%3B')


class WikipediaBatchingTests(unittest.TestCase):
    def test_fifty_titles_go_out_in_one_request(self):
        titles = [f'Place {i}' for i in range(50)]
        client = client_with([{'query': {'pages': []}}])
        fetch_wikipedia_pages(client, titles)
        self.assertEqual(client.stats['requests'], 1)

    def test_seventy_five_titles_cost_two_requests_not_seventy_five(self):
        titles = [f'Place {i}' for i in range(75)]
        client = client_with([{'query': {'pages': []}}, {'query': {'pages': []}}])
        fetch_wikipedia_pages(client, titles)
        self.assertEqual(client.stats['requests'], 2)

    def test_redirects_map_back_to_the_requested_title(self):
        payload = {'query': {
            'redirects': [{'from': 'Monkey Temple', 'to': 'Swayambhunath'}],
            'pages': [{'title': 'Swayambhunath', 'thumbnail': {'source': 'https://img/s.jpg'},
                       'coordinates': [{'lat': 27.7149, 'lon': 85.2904}]}],
        }}
        client = client_with([payload])
        pages = fetch_wikipedia_pages(client, ['Monkey Temple'])
        self.assertIn('Monkey Temple', pages)
        self.assertEqual(pages['Monkey Temple']['thumbnail'], 'https://img/s.jpg')

    def test_missing_pages_are_dropped_rather_than_guessed(self):
        payload = {'query': {'pages': [{'title': 'Nope', 'missing': True}]}}
        client = client_with([payload])
        self.assertEqual(fetch_wikipedia_pages(client, ['Nope']), {})


class ImageVerificationTests(unittest.TestCase):
    """The three images the old run actually got wrong."""

    BOUDHA = {'title': 'Boudhanath', 'thumbnail': 'https://img/b.jpg',
              'lat': 27.7215, 'lon': 85.3620}

    def test_accepts_an_article_whose_coordinates_match(self):
        url, reason = verified_wikipedia_image(self.BOUDHA, 27.7214, 85.3621)
        self.assertEqual(url, 'https://img/b.jpg')
        self.assertIn('verified', reason)

    def test_rejects_an_article_with_no_coordinates(self):
        """This is the Ncell-logo case: the Ncell article has a logo and no
        coordinates, so identity can never be established."""
        page = {'title': 'Ncell', 'thumbnail': 'https://img/ncell-logo.png',
                'lat': None, 'lon': None}
        url, reason = verified_wikipedia_image(page, 27.6298, 85.5203)
        self.assertEqual(url, '')
        self.assertIn('no coordinates', reason)

    def test_rejects_an_article_about_a_different_place(self):
        """'Emblem of Nepal' / a national-level article is nowhere near
        Banepa, so the distance rule throws it out."""
        page = {'title': 'Emblem of Nepal', 'thumbnail': 'https://img/emblem.png',
                'lat': 28.3949, 'lon': 84.1240}
        url, reason = verified_wikipedia_image(page, 27.6298, 85.5203)
        self.assertEqual(url, '')
        self.assertIn('different place', reason)

    def test_rejects_an_article_with_no_lead_image(self):
        page = {'title': 'Somewhere', 'thumbnail': '', 'lat': 27.6, 'lon': 85.5}
        url, reason = verified_wikipedia_image(page, 27.6, 85.5)
        self.assertEqual(url, '')
        self.assertIn('no lead image', reason)

    def test_refuses_to_verify_without_a_coordinate_of_our_own(self):
        url, reason = verified_wikipedia_image(self.BOUDHA, None, None)
        self.assertEqual(url, '')
        self.assertIn('no coordinate on our side', reason)

    def test_missing_page_is_a_clean_rejection(self):
        url, reason = verified_wikipedia_image(None, 27.6, 85.5)
        self.assertEqual(url, '')
        self.assertIn('no article', reason)


class DirectImageTests(unittest.TestCase):
    def test_commons_tag_becomes_a_filepath_url(self):
        url, source = direct_image_from_tags({'wikimedia_commons': 'File:Phewa Lake.jpg'})
        self.assertEqual(url, 'https://commons.wikimedia.org/wiki/Special:FilePath/Phewa_Lake.jpg?width=1000')
        self.assertEqual(source, 'osm:wikimedia_commons')

    def test_https_image_tag_is_used_as_is(self):
        url, source = direct_image_from_tags({'image': 'https://example.org/cafe.jpg'})
        self.assertEqual(url, 'https://example.org/cafe.jpg')
        self.assertEqual(source, 'osm:image')

    def test_http_image_tag_is_rejected_as_mixed_content(self):
        url, _ = direct_image_from_tags({'image': 'http://example.org/cafe.jpg'})
        self.assertEqual(url, '')

    def test_no_tags_means_no_image(self):
        self.assertEqual(direct_image_from_tags({'name': 'Somewhere'}), ('', ''))

    def test_commons_filename_is_url_quoted(self):
        self.assertIn('Durbar%20', commons_file_url('File:Durbar Square (Patan).jpg').replace('_', ' ')
                      .replace(' ', '%20'))
        self.assertTrue(commons_file_url('File:A B.jpg').endswith('A_B.jpg?width=1000'))


class WikidataTests(unittest.TestCase):
    def test_extracts_image_and_coordinate(self):
        payload = {'entities': {'Q42': {
            'labels': {'en': {'value': 'Phewa Lake'}},
            'claims': {
                'P18': [{'mainsnak': {'datavalue': {'value': 'Phewa Lake.jpg'}}}],
                'P625': [{'mainsnak': {'datavalue': {'value': {'latitude': 28.21, 'longitude': 83.94}}}}],
            }}}}
        client = client_with([payload])
        out = fetch_wikidata_entities(client, ['Q42'])
        self.assertEqual(out['Q42']['label'], 'Phewa Lake')
        self.assertIn('Phewa_Lake.jpg', out['Q42']['image'])
        self.assertAlmostEqual(out['Q42']['lat'], 28.21)

    def test_ignores_malformed_ids_without_a_request(self):
        client = client_with([])
        self.assertEqual(fetch_wikidata_entities(client, ['', 'not-a-qid', None]), {})
        self.assertEqual(client.stats['requests'], 0)

    def test_missing_entity_is_skipped(self):
        client = client_with([{'entities': {'Q9': {'missing': ''}}}])
        self.assertEqual(fetch_wikidata_entities(client, ['Q9']), {})


class OverpassQueryTests(unittest.TestCase):
    def test_uses_a_radius_not_a_named_area(self):
        """area["name"="Khopasi"] returns nothing, because OSM has no
        administrative area by that name -- which read as 'OSM has no data
        here'. A radius around a coordinate always works."""
        q = build_overpass_query('restaurants', 27.6087, 85.4692, 12000)
        self.assertIn('around:12000,27.608700,85.469200', q)
        self.assertNotIn('area[', q)

    def test_requires_a_name(self):
        q = build_overpass_query('restaurants', 27.6, 85.4, 5000)
        self.assertIn('["name"]', q)

    def test_destinations_cover_temples_and_historic_sites(self):
        q = build_overpass_query('destinations', 27.6, 85.4, 5000)
        self.assertIn('place_of_worship', q)
        self.assertIn('historic', q)
        self.assertIn('waterfall', q)

    def test_unknown_kind_is_an_error(self):
        with self.assertRaises(ValueError):
            build_overpass_query('hotels', 27.6, 85.4, 5000)


class ElementTests(unittest.TestCase):
    def test_node_point(self):
        self.assertEqual(element_point({'lat': 27.1, 'lon': 85.2}), (27.1, 85.2))

    def test_way_uses_its_centre(self):
        self.assertEqual(element_point({'center': {'lat': 27.1, 'lon': 85.2}}), (27.1, 85.2))

    def test_element_with_no_geometry_is_dropped(self):
        self.assertIsNone(element_point({'type': 'relation', 'id': 1}))

    def test_key_is_stable(self):
        self.assertEqual(element_key({'type': 'way', 'id': 55}), 'way/55')


class AssignmentTests(unittest.TestCase):
    """The brief's 'no place under two locations' requirement."""

    LOCATIONS = [('Banepa', 27.6298, 85.5203), ('Dhulikhel', 27.6159, 85.5391),
                 ('Panauti', 27.5855, 85.5146)]

    def test_each_element_goes_to_exactly_one_town(self):
        candidates = {
            'node/1': (None, {}, (27.6300, 85.5200)),   # on top of Banepa
            'node/2': (None, {}, (27.5856, 85.5147)),   # on top of Panauti
        }
        out = assign_to_nearest(candidates, self.LOCATIONS)
        self.assertEqual(out['node/1'][0], 'Banepa')
        self.assertEqual(out['node/2'][0], 'Panauti')
        self.assertEqual(len(out), 2)

    def test_an_overlap_resolves_to_the_nearer_town(self):
        """Banepa and Dhulikhel are ~2km apart here; a 12km radius around
        each returns the same cafe twice. Nearest-centroid makes the
        duplicate impossible rather than merely unlikely."""
        candidates = {'node/3': (None, {}, (27.6165, 85.5385))}
        out = assign_to_nearest(candidates, self.LOCATIONS)
        self.assertEqual(out['node/3'][0], 'Dhulikhel')

    def test_distance_is_reported(self):
        out = assign_to_nearest({'node/1': (None, {}, (27.6298, 85.5203))}, self.LOCATIONS)
        self.assertLess(out['node/1'][1], 0.1)


class FieldShapeTests(unittest.TestCase):
    def test_long_opening_hours_are_dropped_not_truncated(self):
        """PlaceToEat.business_hours is CharField(max_length=20); OSM
        opening_hours routinely exceeds that. Truncating would render
        'Mo-Sa 07:00-21:00; Su' on the card, which is worse than blank."""
        self.assertEqual(clean_business_hours({'opening_hours': 'Mo-Sa 07:00-21:00; Su 08:00-15:00'}), '')
        self.assertEqual(clean_business_hours({'opening_hours': '09:00-21:00'}), '09:00-21:00')
        self.assertEqual(clean_business_hours({}), '')

    def test_cuisine_is_humanised_with_a_fallback(self):
        self.assertEqual(pretty_cuisine({'cuisine': 'nepalese;indian'}), 'Nepalese, Indian')
        self.assertEqual(pretty_cuisine({'amenity': 'cafe'}), 'Cafe')
        self.assertEqual(pretty_cuisine({'amenity': 'restaurant'}), 'Restaurant')

    def test_price_range_stays_in_the_models_choices(self):
        for tags in ({'amenity': 'fast_food'}, {'amenity': 'cafe'}, {'amenity': 'restaurant'}, {}):
            self.assertIn(price_range_from_tags(tags), (1, 2, 3, 4))

    def test_description_is_never_empty(self):
        """import_data requires a description; an empty one skips the row."""
        for tags in ({}, {'historic': 'temple'}, {'amenity': 'place_of_worship', 'religion': 'hindu'}):
            self.assertTrue(describe_destination(tags, 'Banepa').strip())

    def test_description_only_states_what_the_tags_say(self):
        text = describe_destination({'amenity': 'place_of_worship', 'religion': 'buddhist'}, 'Namobuddha')
        self.assertIn('Buddhist', text)
        self.assertIn('Namobuddha', text)


class PlaceholderTests(unittest.TestCase):
    """Names from the seed scripts that no geocoder could ever resolve,
    because they do not name a real mapped object."""

    def test_flags_the_invented_seed_names(self):
        for name in ('Local Bhatti', 'Traditional Villages', 'Highway Dhaba',
                     'City Center Kitchen', 'Banepa Heritage Walk', 'Local Temples',
                     'Truck Stop Cafe', 'Meditation Center', 'Khopasi Market'):
            self.assertTrue(looks_like_placeholder(name), name)

    def test_leaves_real_places_alone(self):
        for name in ('Boudhanath Stupa', 'Phewa Lake', 'Indreshwar Temple',
                     'Nyatapola Temple', 'Chitwan National Park', 'Janaki Mandir'):
            self.assertFalse(looks_like_placeholder(name), name)


class MiscTests(unittest.TestCase):
    def test_haversine_matches_a_known_distance(self):
        # Kathmandu -> Pokhara is about 140km in a straight line.
        km = haversine_km(27.7172, 85.3240, 28.2096, 83.9856)
        self.assertTrue(135 < km < 150, km)

    def test_wikipedia_tag_parsing(self):
        self.assertEqual(parse_wikipedia_tag('en:Boudhanath'), ('en', 'Boudhanath'))
        self.assertIsNone(parse_wikipedia_tag('Boudhanath'))
        self.assertIsNone(parse_wikipedia_tag(''))

    def test_normalise_name_collapses_case_and_punctuation(self):
        self.assertEqual(normalise_name('Swayambhunath (Monkey Temple)'),
                         normalise_name('swayambhunath  monkey temple'))

    def test_completeness_prefers_documented_places(self):
        rich = {'wikidata': 'Q1', 'wikimedia_commons': 'File:x.jpg', 'cuisine': 'nepalese'}
        bare = {'name': 'x'}
        self.assertGreater(completeness_score(rich), completeness_score(bare))


class BusinessHoursContractTests(unittest.TestCase):
    """web_interface.views.is_open() parses business_hours with one
    `HH:MM-HH:MM` split. Anything else it is handed comes back as None, so
    the "Open now" filter silently treats the venue as unknown. These tests
    pin the contract from the producing side.
    """

    @staticmethod
    def _is_open_parses(value):
        """A faithful, Django-free copy of the parse is_open() performs."""
        from datetime import datetime
        if not value or '-' not in value:
            return False
        start, _, end = value.partition('-')
        try:
            datetime.strptime(start.strip(), '%H:%M')
            datetime.strptime(end.strip(), '%H:%M')
        except ValueError:
            return False
        return True

    def test_everything_emitted_is_parseable_by_is_open(self):
        samples = [
            {'opening_hours': '09:00-21:00'},
            {'opening_hours': 'Mo-Su 07:00-22:00'},
            {'opening_hours': '24/7'},
            {'opening_hours': 'Mo-Sa 8:00-20:00'},
            {'opening_hours': 'Mo-Fr 09:00-17:00; Sa 10:00-14:00'},
            {'opening_hours': 'sunrise-sunset'},
            {'opening_hours': ''},
            {},
        ]
        for tags in samples:
            value = clean_business_hours(tags)
            if value:
                self.assertTrue(self._is_open_parses(value),
                                f'{tags!r} produced unparseable {value!r}')

    def test_partial_day_rules_are_dropped_not_flattened(self):
        """Keeping only 'Mo-Fr 09:00-17:00' from a multi-rule string would
        tell a Saturday visitor the place is closed when it is open."""
        self.assertEqual(clean_business_hours({'opening_hours': 'Mo-Fr 09:00-17:00; Sa 10:00-14:00'}), '')

    def test_all_week_prefix_is_flattened(self):
        self.assertEqual(clean_business_hours({'opening_hours': 'Mo-Su 07:00-22:00'}), '07:00-22:00')

    def test_24_7_becomes_the_always_open_encoding(self):
        self.assertEqual(clean_business_hours({'opening_hours': '24/7'}), '00:00-23:59')

    def test_single_digit_hours_are_padded(self):
        self.assertEqual(clean_business_hours({'opening_hours': 'Mo-Sa 8:00-20:00'}), '08:00-20:00')

    def test_result_always_fits_the_column(self):
        for tags in ({'opening_hours': '24/7'}, {'opening_hours': 'Mo-Su 07:00-22:00'}):
            self.assertLessEqual(len(clean_business_hours(tags)), 20)


class ModelFieldWidthTests(unittest.TestCase):
    """A staged row that exceeds a column width is rejected by the database
    and shows up only as a skipped row at import time. Catching it while
    staging turns a silent loss into an explained one."""

    def test_over_long_name_is_rejected_with_a_reason(self):
        ok, why = fits_model_fields('x' * (MAX_NAME + 1))
        self.assertFalse(ok)
        self.assertIn('name', why)

    def test_over_long_cuisine_is_rejected(self):
        ok, why = fits_model_fields('Cafe', 'y' * (MAX_CUISINE + 1))
        self.assertFalse(ok)
        self.assertIn('cuisine', why)

    def test_over_long_image_url_is_rejected(self):
        ok, why = fits_model_fields('Cafe', 'Nepali', 'https://x/' + 'z' * MAX_IMAGE_URL)
        self.assertFalse(ok)
        self.assertIn('image', why)

    def test_normal_row_passes(self):
        self.assertEqual(fits_model_fields('Boudhanath Stupa', 'Nepali', 'https://x/y.jpg'),
                         (True, ''))

    def test_cuisine_is_trimmed_to_fit_before_it_can_fail(self):
        tags = {'cuisine': ';'.join(['very_long_cuisine_name_here'] * 3)}
        self.assertLessEqual(len(pretty_cuisine(tags)), MAX_CUISINE)


class ImageProvenanceTests(unittest.TestCase):
    """The old command's images all live on upload.wikimedia.org; the new
    pipeline's all live on a Special:FilePath path. Keeping the two sets on
    distinguishable hosts is what lets audit_places decide whether a stored
    image was ever verified -- the old command recorded no provenance."""

    def test_wiki_file_url_uses_the_articles_own_wiki(self):
        url = wiki_file_url('https://en.wikipedia.org/w/api.php', 'Boudha Stupa.jpg')
        self.assertTrue(url.startswith('https://en.wikipedia.org/wiki/Special:FilePath/'))
        self.assertIn('Boudha_Stupa.jpg', url)

    def test_verified_image_prefers_the_filepath_form(self):
        page = {'title': 'Boudhanath', 'thumbnail': 'https://upload.wikimedia.org/x/thumb.jpg',
                'filepath': 'https://en.wikipedia.org/wiki/Special:FilePath/Boudha.jpg',
                'lat': 27.7215, 'lon': 85.3620}
        url, _ = verified_wikipedia_image(page, 27.7215, 85.3620)
        self.assertIn('Special:FilePath', url)
        self.assertNotIn('upload.wikimedia.org', url)

    def test_falls_back_to_the_thumbnail_when_no_file_name(self):
        page = {'title': 'X', 'thumbnail': 'https://upload.wikimedia.org/x.jpg',
                'filepath': '', 'lat': 27.0, 'lon': 85.0}
        url, _ = verified_wikipedia_image(page, 27.0, 85.0)
        self.assertEqual(url, 'https://upload.wikimedia.org/x.jpg')

    def test_page_records_carry_a_filepath(self):
        payload = {'query': {'pages': [
            {'title': 'Boudhanath', 'pageimage': 'Boudha.jpg',
             'thumbnail': {'source': 'https://upload.wikimedia.org/t.jpg'},
             'coordinates': [{'lat': 27.72, 'lon': 85.36}]}]}}
        client = client_with([payload])
        pages = fetch_wikipedia_pages(client, ['Boudhanath'])
        self.assertIn('Special:FilePath', pages['Boudhanath']['filepath'])


class OverpassFailureTests(unittest.TestCase):
    def test_a_remark_is_not_treated_as_an_empty_town(self):
        """Overpass reports timeouts and out-of-memory as HTTP 200 with a
        `remark` and no elements. Reading that as 'nothing is mapped here'
        is the silent-empty-result mistake that made the old data look
        finished."""
        remark = {'remark': 'runtime error: Query timed out', 'elements': []}
        client = client_with([remark, remark, remark])
        with self.assertRaises(RateLimited):
            fetch_overpass(client, 'out;',
                           endpoints=('https://a/i', 'https://b/i', 'https://c/i'))

    def test_a_later_mirror_can_rescue_the_query(self):
        client = client_with([{'remark': 'runtime error', 'elements': []},
                              {'elements': [{'type': 'node', 'id': 1}]}])
        out = fetch_overpass(client, 'out;', endpoints=('https://a/i', 'https://b/i'))
        self.assertEqual(len(out), 1)


if __name__ == '__main__':
    unittest.main()
