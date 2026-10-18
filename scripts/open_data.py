"""
Shared, Django-free helpers for collecting *verified* place data from the
free/open sources this project is allowed to use: OpenStreetMap (via the
Overpass API), Wikidata, Wikimedia Commons and Wikipedia.

WHY THIS MODULE EXISTS SEPARATELY FROM THE MANAGEMENT COMMANDS

Everything here is plain Python with no Django import, for two reasons:

1. It can be unit-tested directly (see scripts/tests/test_open_data.py and
   web_interface/tests.py) with fake HTTP responses, with no database and
   no network -- which is the only way the matching/verification rules
   below can actually be proved correct rather than assumed.
2. The management commands stay thin: they read Locations out of the
   database, call in here, and write CSVs. No network logic, no matching
   heuristics, and no "take the first search result" anywhere in a
   command.

THE THREE BUGS THIS MODULE IS BUILT TO NOT HAVE
-----------------------------------------------

(a) "no match -- left as-is" for ~75% of places.
    Cause: the old backfill_coordinates asked Nominatim to *free-text
    search* a name that, in most cases, does not name a real mapped
    object ("Local Temples", "Traditional Villages", "Khopasi Local").
    Geocoding cannot invent a place that isn't in OSM. The fix is to
    stop asking "where is this name?" and instead ask Overpass "what is
    actually mapped near this town?" -- which returns real objects that
    come with their own exact coordinates, so no geocoding step exists
    to fail.

(b) "HTTP 429 from Wikipedia" on nearly every request.
    Cause: two things at once.
      * The old User-Agent was 'TravelEase/1.0 (educational project;
        contact via project README)'. That contains no URL and no email,
        so it does not satisfy the Wikimedia Foundation User-Agent
        policy. Since 2026 the API gateway sorts non-compliant clients
        into a restrictive anonymous-scraping rate-limit tier, and 429s
        start almost immediately. See USER_AGENT_TEMPLATE below.
      * It made one HTTP request *per destination*. The MediaWiki Action
        API accepts up to 50 titles in a single query. 75 destinations
        should be 2 requests, not 75.
    Both are fixed here: a policy-compliant UA built from settings, hard
    batching at 50, a token-bucket pacer, and real Retry-After/backoff
    handling instead of a bare `except HTTPError: return ''`.

(c) Confidently wrong images (an Ncell logo on "Banepa Market", Nepal's
    national emblem on "Banepa Heritage Walk", an old map on "Banepa
    Durbar Square").
    Cause: `generator=search&gsrlimit=1` -- the top full-text search hit
    for any string always exists, so "no confident match" was never
    actually reachable for a name containing a common word. Nothing
    checked that the article was *about that place*.
    Fix: this module never accepts a search hit. An image is accepted
    only when the identity link is explicit -- the OSM object itself
    carries a wikidata/wikimedia_commons/wikipedia/image tag -- or, for
    legacy rows with no OSM link, only when the Wikipedia article's own
    coordinates (prop=coordinates) land within VERIFY_RADIUS_KM of where
    we think the place is. An article with no coordinates is rejected,
    which is exactly what would have thrown out all three bad images
    above.
"""
from __future__ import annotations

import gzip
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

# Overpass has several public mirrors. They go down / get busy independently,
# so the client tries them in order rather than failing the whole run because
# one instance was overloaded for ten seconds.
OVERPASS_ENDPOINTS = (
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
)

WIKIDATA_API = 'https://www.wikidata.org/w/api.php'
WIKIPEDIA_API = 'https://en.wikipedia.org/w/api.php'
COMMONS_FILEPATH = 'https://commons.wikimedia.org/wiki/Special:FilePath/'

# Wikimedia's Action API takes up to 50 titles/ids per request for
# non-bot clients. Going over this silently truncates, so it is a hard cap
# rather than a tunable.
WIKIMEDIA_BATCH = 50

# How far a *claimed* identity is allowed to be from where we think a place
# is before we refuse to believe it's the same place. Used to validate
# Wikipedia/Wikidata coordinates against the OSM/town position. 5km is wide
# enough for a large temple complex or a lake described from its far shore,
# and tight enough that a same-named place in the next district is rejected.
VERIFY_RADIUS_KM = 5.0

# Preferred User-Agent shape from Wikimedia's access policy:
#     <client name>/<version> (<contact information>) <library>/<version>
# The contact part is the bit the old code was missing, and the reason
# every run ended in a wall of 429s.
USER_AGENT_TEMPLATE = 'TravelEase/2.0 ({contact}) Python-urllib/3'
DEFAULT_CONTACT = 'https://github.com/  -- set TRAVELEASE_CONTACT in .env'


def build_user_agent(contact: str | None) -> str:
    """Build a policy-compliant User-Agent. `contact` must be a real URL or
    email -- it is what lets Wikimedia/OSM sysadmins reach you instead of
    silently throttling you."""
    contact = (contact or '').strip()
    if not contact:
        contact = DEFAULT_CONTACT
    return USER_AGENT_TEMPLATE.format(contact=contact)


def contact_looks_real(contact: str | None) -> bool:
    """True if `contact` is something a sysadmin could actually use. The
    commands refuse to start a large run without one, because running with
    a placeholder is what gets an IP throttled."""
    c = (contact or '').strip()
    if not c or 'set TRAVELEASE_CONTACT' in c:
        return False
    return bool(re.match(r'^https?://\S+\.\S+', c) or re.match(r'^[^@\s]+@[^@\s]+\.\S+$', c))


# --------------------------------------------------------------------------
# HTTP client: paced, identified, and honest about rate limiting
# --------------------------------------------------------------------------

class RateLimited(Exception):
    """Raised when a host kept returning 429/503 after every retry. Kept
    distinct from a generic failure so callers can report 'we were throttled'
    rather than 'this place has no photo' -- conflating those two is exactly
    what made the old run look like 51 destinations had no image."""


class HttpClient:
    """Minimal, dependency-free HTTP client with the three behaviours the
    old code was missing: a compliant identity, a per-host minimum interval,
    and real backoff that honours Retry-After.

    Deliberately stdlib-only so it works in the same environments
    scripts/osm_to_csv.py already runs in.
    """

    def __init__(self, contact=None, user_agent=None, min_interval=1.0,
                 max_retries=4, sleep=time.sleep, opener=None):
        self.user_agent = user_agent or build_user_agent(contact)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._sleep = sleep
        self._opener = opener or urllib.request.urlopen
        self._last_call = {}
        self.stats = {'requests': 0, 'retries': 0, 'throttled': 0}

    # -- pacing -----------------------------------------------------------
    def _pace(self, host):
        last = self._last_call.get(host)
        now = time.monotonic()
        if last is not None:
            wait = self.min_interval - (now - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[host] = time.monotonic()

    # -- core -------------------------------------------------------------
    def request(self, url, data=None, headers=None, timeout=90):
        """Perform a request, retrying on 429/503/5xx with Retry-After or
        exponential backoff. Returns decoded bytes. Raises RateLimited if
        the host is still refusing after max_retries, or urllib's own
        errors for anything non-transient (404, 400, DNS failure...)."""
        host = urllib.parse.urlsplit(url).netloc
        hdrs = {
            'User-Agent': self.user_agent,
            'Accept-Encoding': 'gzip',
            'Accept': 'application/json',
        }
        if data is not None:
            # Overpass rejects a bare query body with HTTP 406. It expects
            # a form-encoded `data=` parameter. This is the 406 fix.
            hdrs['Content-Type'] = 'application/x-www-form-urlencoded'
        if headers:
            hdrs.update(headers)

        delay = 2.0
        last_error = None
        for attempt in range(self.max_retries + 1):
            self._pace(host)
            req = urllib.request.Request(url, data=data, headers=hdrs,
                                         method='POST' if data is not None else 'GET')
            try:
                self.stats['requests'] += 1
                with self._opener(req, timeout=timeout) as resp:
                    body = resp.read()
                    if (resp.headers.get('Content-Encoding') or '') == 'gzip':
                        body = gzip.decompress(body)
                    return body
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code in (429, 502, 503, 504):
                    self.stats['throttled'] += 1
                    if attempt == self.max_retries:
                        break
                    retry_after = 0.0
                    try:
                        retry_after = float((e.headers or {}).get('Retry-After') or 0)
                    except (TypeError, ValueError):
                        retry_after = 0.0
                    # Wikimedia's guidance: if no Retry-After, wait at
                    # least 5 seconds, then exponential back-off.
                    wait = max(retry_after, delay, 5.0 if e.code == 429 else delay)
                    self.stats['retries'] += 1
                    self._sleep(wait)
                    delay *= 2
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
                if attempt == self.max_retries:
                    break
                self.stats['retries'] += 1
                self._sleep(delay)
                delay *= 2
        raise RateLimited(f'{host}: gave up after {self.max_retries + 1} attempts ({last_error})')

    def get_json(self, url, params=None, timeout=90):
        if params:
            url = f'{url}?{urllib.parse.urlencode(params)}'
        return json.loads(self.request(url, timeout=timeout))

    def post_json(self, url, payload, timeout=180):
        data = urllib.parse.urlencode(payload).encode('utf-8')
        return json.loads(self.request(url, data=data, timeout=timeout))


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(min(1.0, math.sqrt(a)))


# --------------------------------------------------------------------------
# Overpass
# --------------------------------------------------------------------------

# Restaurants/dining. `nwr` covers nodes, ways and relations in one clause;
# ["name"] is required because an unnamed dining node is useless in a
# listing UI -- it would render as a blank card.
RESTAURANT_SELECTOR = (
    'nwr["amenity"~"^(restaurant|cafe|fast_food|bar|pub|food_court|ice_cream|biergarten)$"]'
    '["name"](around:{radius},{lat},{lon});'
)

# Destinations. Broader than the old tourism-only query, which is the other
# reason coverage was thin: in Nepal an enormous share of the real
# sightseeing targets are tagged historic=* or amenity=place_of_worship
# (temples, stupas, monasteries), not tourism=attraction.
DESTINATION_SELECTORS = (
    'nwr["tourism"~"^(attraction|museum|viewpoint|artwork|gallery|zoo|theme_park|aquarium)$"]'
    '["name"](around:{radius},{lat},{lon});',
    'nwr["historic"]["name"](around:{radius},{lat},{lon});',
    'nwr["amenity"="place_of_worship"]["name"](around:{radius},{lat},{lon});',
    'nwr["natural"~"^(peak|waterfall|cave_entrance|hot_spring|spring|glacier)$"]'
    '["name"](around:{radius},{lat},{lon});',
    'nwr["leisure"~"^(park|nature_reserve|garden)$"]["name"](around:{radius},{lat},{lon});',
    'nwr["boundary"="national_park"]["name"](around:{radius},{lat},{lon});',
    'nwr["waterway"="waterfall"]["name"](around:{radius},{lat},{lon});',
)

KIND_SELECTORS = {
    'restaurants': (RESTAURANT_SELECTOR,),
    'destinations': DESTINATION_SELECTORS,
}


def build_overpass_query(kind, lat, lon, radius_m, timeout=180):
    """Build an `around:` query centred on a town's own coordinate.

    WHY `around:` AND NOT `area["name"="Banepa"]`
    The old sync_osm_data used an area lookup by name. That only works if
    OSM happens to contain an administrative *area* with exactly that name
    -- which is true for Kathmandu and false for plenty of the smaller
    entries in this project's dropdown (Khopasi, Namobuddha, Nagarkot...).
    When it's false the query returns zero results and looks like "OSM has
    no data here", which is wrong. A radius around a coordinate we already
    hold in the Location table always works.
    """
    if kind not in KIND_SELECTORS:
        raise ValueError(f'unknown kind: {kind!r}')
    body = '\n  '.join(
        sel.format(radius=int(radius_m), lat=f'{lat:.6f}', lon=f'{lon:.6f}')
        for sel in KIND_SELECTORS[kind]
    )
    return f'[out:json][timeout:{int(timeout)}];\n(\n  {body}\n);\nout center tags;'


def fetch_overpass(client, query, endpoints=OVERPASS_ENDPOINTS):
    """POST a query, trying each mirror in turn. Returns the `elements`
    list. The payload is form-encoded as `data=<query>` -- posting the raw
    query as the body is what produced HTTP 406."""
    last = None
    for url in endpoints:
        try:
            # The HTTP read timeout must exceed the [out:timeout:N] the
            # query itself carries, or a long-but-successful Overpass run is
            # killed client-side just as it is about to answer.
            payload = client.post_json(url, {'data': query}, timeout=240)
            # Overpass reports server-side failures (timeout, out of memory,
            # bad query) as HTTP 200 with a `remark` field and an empty
            # element list. Treating that as "this town has nothing mapped"
            # is exactly the class of silent-empty-result mistake that made
            # the previous data look finished when it wasn't, so it is
            # raised and the next mirror is tried instead.
            remark = (payload.get('remark') or '').strip()
            if remark:
                last = RuntimeError(f'{url} returned a remark: {remark}')
                continue
            return payload.get('elements') or []
        except (RateLimited, urllib.error.HTTPError, urllib.error.URLError,
                TimeoutError, json.JSONDecodeError) as e:
            last = e
            continue
    raise RateLimited(f'every Overpass endpoint failed; last error: {last}')


def element_key(element):
    """Stable identity for an OSM element, e.g. 'node/12345'. Used to
    de-duplicate across overlapping town radii -- Banepa and Dhulikhel are
    16km apart, so their catchments overlap and would otherwise import the
    same restaurant twice under two different towns."""
    return f"{element.get('type')}/{element.get('id')}"


def element_point(element):
    """(lat, lon) for a node, or the centre of a way/relation. Returns None
    when Overpass gave us neither -- which can happen for a relation with
    broken membership. Such an element is dropped rather than guessed."""
    if element.get('lat') is not None and element.get('lon') is not None:
        return float(element['lat']), float(element['lon'])
    centre = element.get('center') or {}
    if centre.get('lat') is not None and centre.get('lon') is not None:
        return float(centre['lat']), float(centre['lon'])
    return None


# --------------------------------------------------------------------------
# Tag interpretation
# --------------------------------------------------------------------------

CUISINE_FALLBACK = {
    'restaurant': 'Restaurant',
    'cafe': 'Cafe',
    'fast_food': 'Fast food',
    'bar': 'Bar',
    'pub': 'Pub',
    'food_court': 'Food court',
    'ice_cream': 'Ice cream',
    'biergarten': 'Beer garden',
}


def pretty_cuisine(tags):
    """OSM writes cuisine as machine values ('nepalese;indian', 'coffee_shop').
    Turn that into something presentable, falling back to the amenity type
    rather than leaving the column blank."""
    raw = (tags.get('cuisine') or '').strip()
    if raw:
        parts = [p.strip().replace('_', ' ').title() for p in re.split(r'[;,]', raw) if p.strip()]
        if parts:
            joined = ', '.join(parts[:3])
            # PlaceToEat.cuisine is CharField(max_length=100); trim by whole
            # entries rather than mid-word so the value stays readable.
            while len(joined) > MAX_CUISINE and len(parts) > 1:
                parts = parts[:-1]
                joined = ', '.join(parts)
            if len(joined) <= MAX_CUISINE:
                return joined
    return CUISINE_FALLBACK.get(tags.get('amenity', ''), 'Restaurant')


DESTINATION_LABELS = (
    ('tourism', {
        'attraction': 'A visitor attraction',
        'museum': 'A museum',
        'viewpoint': 'A viewpoint',
        'artwork': 'A public artwork',
        'gallery': 'An art gallery',
        'zoo': 'A zoo',
        'theme_park': 'A theme park',
        'aquarium': 'An aquarium',
    }),
    ('historic', {
        'temple': 'A historic temple',
        'monument': 'A monument',
        'memorial': 'A memorial',
        'ruins': 'Historic ruins',
        'archaeological_site': 'An archaeological site',
        'castle': 'A historic fort or palace',
        'building': 'A historic building',
        'city_gate': 'A historic city gate',
        'wayside_shrine': 'A wayside shrine',
    }),
    ('natural', {
        'peak': 'A mountain peak',
        'waterfall': 'A waterfall',
        'cave_entrance': 'A cave',
        'hot_spring': 'A hot spring',
        'spring': 'A natural spring',
        'glacier': 'A glacier',
    }),
    ('leisure', {
        'park': 'A public park',
        'nature_reserve': 'A nature reserve',
        'garden': 'A garden',
    }),
)

RELIGION_LABELS = {
    'hindu': 'A Hindu temple',
    'buddhist': 'A Buddhist temple or monastery',
    'muslim': 'A mosque',
    'christian': 'A church',
    'jain': 'A Jain temple',
    'sikh': 'A gurdwara',
}


def describe_destination(tags, location_name):
    """A short, factual, source-backed description.

    This never invents marketing copy. Every clause comes from a tag that
    is actually on the object, so the description cannot claim something
    OSM doesn't say. `import_data` requires a non-empty description, so a
    generic-but-true sentence is the correct output when the tags are thin.
    """
    label = None
    for key, mapping in DESTINATION_LABELS:
        value = tags.get(key)
        if value and value in mapping:
            label = mapping[value]
            break
        if value and key == 'historic':
            label = 'A historic site'
            break
    if label is None and tags.get('amenity') == 'place_of_worship':
        label = RELIGION_LABELS.get((tags.get('religion') or '').lower(), 'A place of worship')
    if label is None and tags.get('boundary') == 'national_park':
        label = 'A national park'
    if label is None:
        label = 'A point of interest'

    bits = [f'{label} in {location_name}, recorded in OpenStreetMap.']
    if tags.get('ele'):
        bits.append(f"Elevation about {tags['ele']} m.")
    if tags.get('opening_hours'):
        bits.append(f"Opening hours: {tags['opening_hours']}.")
    if tags.get('website'):
        bits.append(f"Website: {tags['website']}.")
    return ' '.join(bits)


# web_interface.views.is_open() parses business_hours with a single
# `HH:MM-HH:MM` split, so anything else it is handed silently returns None
# and the "Open now" filter treats the venue as unknown. Storing a raw OSM
# opening_hours string would therefore look like data while doing nothing.
# Same regex shape as scripts/osm_to_csv.extract_business_hours, kept
# deliberately identical so both import paths behave the same.
TIME_RANGE_RE = re.compile(r'\b([0-2]?\d:[0-5]\d)\s*-\s*([0-2]?\d:[0-5]\d)\b')

# Any of these day prefixes means "this rule applies every day", so the
# time range after it is safe to use as the venue's single range. A rule
# scoped to only some days (e.g. "Mo-Fr 09:00-17:00; Sa 10:00-14:00")
# cannot be represented in one HH:MM-HH:MM field, so it is dropped.
_ALL_DAYS_PREFIX = re.compile(r'^(mo-su|mo-sa|daily|24/7)?\s*$', re.IGNORECASE)


def _pad(t):
    """'9:00' -> '09:00'. is_open() uses strptime('%H:%M'), which accepts
    both, but the field is 20 chars and consistency makes the stored value
    comparable."""
    h, _, m = t.partition(':')
    return f'{int(h):02d}:{m}'


def clean_business_hours(tags, max_len=20):
    """Turn an OSM `opening_hours` value into the one shape this project's
    `is_open()` can read -- `HH:MM-HH:MM` -- or return '' .

    Returning '' is the right answer far more often than it looks: OSM's
    opening_hours is a full grammar (day ranges, seasons, exceptions, public
    holidays) and most real values cannot be reduced to a single range
    without lying about when the place is open. A blank field renders as
    "hours not listed", which is true; a truncated or mis-parsed one makes
    the site tell a visitor a restaurant is open when it is closed.
    """
    value = (tags.get('opening_hours') or '').strip()
    if not value:
        return ''
    if value.lower() in ('24/7', '24/7; ph off'):
        # is_open() compares at minute precision, so 00:00-23:59 is the
        # honest always-open encoding here (see its docstring).
        return '00:00-23:59'
    # More than one rule (';') or an explicit day scope we can't flatten.
    if ';' in value or ',' in value:
        return ''
    match = TIME_RANGE_RE.search(value)
    if not match:
        return ''
    prefix = value[:match.start()].strip()
    if not _ALL_DAYS_PREFIX.match(prefix):
        return ''
    result = f'{_pad(match.group(1))}-{_pad(match.group(2))}'
    return result if len(result) <= max_len else ''


def price_range_from_tags(tags):
    """PlaceToEat.price_range is a required 1-4 choice. OSM has no price
    field, so this maps from amenity type -- a documented, defensible
    default rather than a fabricated price point. average_cost/rating stay
    empty, which import_data already accepts."""
    amenity = tags.get('amenity', '')
    if amenity in ('fast_food', 'ice_cream', 'food_court'):
        return 1
    if amenity in ('cafe', 'pub', 'bar', 'biergarten'):
        return 2
    return 2


# --------------------------------------------------------------------------
# Images: identity-linked only
# --------------------------------------------------------------------------

def commons_file_url(filename, width=1000):
    """Turn a Commons file name into a stable, hot-linkable image URL.

    Special:FilePath is the documented redirect endpoint, so this needs no
    API call and never goes stale when Commons reshuffles its hash
    directories.
    """
    name = (filename or '').strip()
    if not name:
        return ''
    name = re.sub(r'^(File|Image):\s*', '', name, flags=re.IGNORECASE)
    name = name.replace(' ', '_')
    return f'{COMMONS_FILEPATH}{urllib.parse.quote(name)}?width={int(width)}'


def wiki_file_url(api_url, filename, width=1000):
    """Special:FilePath URL on whichever wiki `api_url` belongs to."""
    name = (filename or '').strip()
    if not name:
        return ''
    name = re.sub(r'^(File|Image):\s*', '', name, flags=re.IGNORECASE).replace(' ', '_')
    host = urllib.parse.urlsplit(api_url).netloc or 'en.wikipedia.org'
    return f'https://{host}/wiki/Special:FilePath/{urllib.parse.quote(name)}?width={int(width)}'


def direct_image_from_tags(tags):
    """Image sources that are carried *on the OSM object itself*, i.e. the
    identity link is explicit and no matching/guessing is involved.

    Returns (url, source_label) or ('', '').
    """
    commons = (tags.get('wikimedia_commons') or '').strip()
    if commons.lower().startswith(('file:', 'image:')):
        return commons_file_url(commons), 'osm:wikimedia_commons'

    image = (tags.get('image') or '').strip()
    if image.lower().startswith(('file:', 'image:')):
        return commons_file_url(image), 'osm:image(commons)'
    if image.startswith('https://'):
        # Only https: an http image breaks the padlock on an https site,
        # and browsers block it as mixed content, so the card renders empty.
        return image, 'osm:image'
    return '', ''


def parse_wikipedia_tag(value):
    """OSM's `wikipedia` tag is 'lang:Article Title'. Returns (lang, title)
    or None. Only English is used here because the project's Wikipedia
    lookups target en.wikipedia.org."""
    value = (value or '').strip()
    if ':' not in value:
        return None
    lang, _, title = value.partition(':')
    lang = lang.strip().lower()
    title = title.strip()
    if not lang or not title or len(lang) > 12:
        return None
    return lang, title


# --------------------------------------------------------------------------
# Wikidata
# --------------------------------------------------------------------------

QID_RE = re.compile(r'^Q[1-9]\d*$')


def fetch_wikidata_entities(client, qids):
    """Batch-fetch Wikidata entities (50 per request) and return
    {qid: {'label', 'image', 'lat', 'lon'}}.

    P18 is the entity's own main image and P625 its own coordinate, so
    both are identity-guaranteed: if the OSM object says wikidata=Q123,
    then Q123's photo *is* a photo of that object. This is why the harvest
    path needs no similarity heuristics at all.
    """
    out = {}
    clean = [q for q in dict.fromkeys(qids) if QID_RE.match(q or '')]
    for i in range(0, len(clean), WIKIMEDIA_BATCH):
        chunk = clean[i:i + WIKIMEDIA_BATCH]
        data = client.get_json(WIKIDATA_API, {
            'action': 'wbgetentities',
            'ids': '|'.join(chunk),
            'props': 'labels|claims',
            'languages': 'en',
            'format': 'json',
            'formatversion': '2',
        })
        for qid, entity in (data.get('entities') or {}).items():
            if entity.get('missing') is not None:
                continue
            claims = entity.get('claims') or {}
            record = {'label': '', 'image': '', 'lat': None, 'lon': None}
            labels = entity.get('labels') or {}
            en = labels.get('en')
            record['label'] = (en.get('value') if isinstance(en, dict) else en) or ''

            for claim in claims.get('P18') or []:
                value = (((claim.get('mainsnak') or {}).get('datavalue') or {}).get('value'))
                if isinstance(value, str) and value.strip():
                    record['image'] = commons_file_url(value)
                    break
            for claim in claims.get('P625') or []:
                value = (((claim.get('mainsnak') or {}).get('datavalue') or {}).get('value'))
                if isinstance(value, dict) and value.get('latitude') is not None:
                    record['lat'] = float(value['latitude'])
                    record['lon'] = float(value['longitude'])
                    break
            out[qid] = record
    return out


# --------------------------------------------------------------------------
# Wikipedia: batched, and only ever used with verification
# --------------------------------------------------------------------------

def fetch_wikipedia_pages(client, titles, thumb_size=1000, api=WIKIPEDIA_API):
    """Batch-fetch page images *and page coordinates* for exact titles.

    Two things matter here and both were missing before:

    * `titles=` (exact lookup) instead of `generator=search`. A search
      always returns something; an exact title lookup returns `missing`
      when the article does not exist, which is the honest answer.
    * `prop=pageimages|coordinates` in one call, so the caller can check
      that the article is geographically the same place before trusting
      its photo. That check is the whole defence against the Ncell-logo
      class of error.

    Returns {normalised_or_requested_title: {'thumbnail','lat','lon','title'}}.
    Redirects and title normalisation are resolved back to the requested
    title so the caller's lookup key still works.
    """
    out = {}
    clean = [t for t in dict.fromkeys(titles) if (t or '').strip()]
    for i in range(0, len(clean), WIKIMEDIA_BATCH):
        chunk = clean[i:i + WIKIMEDIA_BATCH]
        data = client.get_json(api, {
            'action': 'query',
            'titles': '|'.join(chunk),
            'prop': 'pageimages|coordinates',
            # 'name' gives the lead image's file name, which lets us build a
            # Special:FilePath URL on the wiki itself. That matters for more
            # than tidiness: every image the OLD, unverified command saved
            # points at upload.wikimedia.org, so keeping verified images on
            # a different host is what makes 'unverified' a decidable
            # property of a stored row rather than a guess.
            'piprop': 'thumbnail|name',
            'pithumbsize': int(thumb_size),
            'redirects': '1',
            'format': 'json',
            'formatversion': '2',
        })
        query = data.get('query') or {}

        # Map whatever title the API ended up on back to what we asked for.
        alias = {}
        for entry in query.get('normalized') or []:
            alias[entry.get('to')] = entry.get('from')
        for entry in query.get('redirects') or []:
            alias[entry.get('to')] = alias.get(entry.get('from'), entry.get('from'))

        for page in query.get('pages') or []:
            if page.get('missing'):
                continue
            title = page.get('title') or ''
            coords = (page.get('coordinates') or [{}])[0]
            pageimage = page.get('pageimage') or ''
            record = {
                'title': title,
                'thumbnail': ((page.get('thumbnail') or {}).get('source') or ''),
                # Special:FilePath on the article's own wiki resolves both
                # locally-uploaded files and Commons-hosted ones, so this is
                # safe even when the lead image is not on Commons.
                'filepath': wiki_file_url(api, pageimage) if pageimage else '',
                'lat': coords.get('lat'),
                'lon': coords.get('lon'),
            }
            out[title] = record
            requested = alias.get(title)
            if requested:
                out[requested] = record
    return out


def verified_wikipedia_image(page, expected_lat, expected_lon,
                             radius_km=VERIFY_RADIUS_KM):
    """Decide whether a Wikipedia page's photo may be attached to a place.

    Returns (url, reason). `url` is empty whenever the identity is not
    proven, and `reason` explains which rule rejected it so the audit
    report can show it.

    The rule is deliberately strict: a geographic article that is really
    about this place will carry coordinates, so *absence* of coordinates
    is treated as failure to prove identity rather than as neutral. That
    single rule is what rejects the Ncell logo, the national emblem and
    the old map that the previous run attached.
    """
    if not page:
        return '', 'no article with that exact title'
    if not (page.get('filepath') or page.get('thumbnail')):
        return '', 'article exists but has no lead image'
    if page.get('lat') is None or page.get('lon') is None:
        return '', 'article has no coordinates, so identity cannot be verified'
    if expected_lat is None or expected_lon is None:
        return '', 'no coordinate on our side to verify against'
    distance = haversine_km(float(expected_lat), float(expected_lon),
                            float(page['lat']), float(page['lon']))
    if distance > radius_km:
        return '', f'article is {distance:.1f}km away -- different place'
    # Prefer the stable Special:FilePath form; fall back to the thumbnail
    # URL only when the API did not report a file name.
    return (page.get('filepath') or page['thumbnail'],
            f'verified ({distance:.1f}km from our coordinate)')


# --------------------------------------------------------------------------
# Placeholder detection (for auditing the existing seed data)
# --------------------------------------------------------------------------

# Names built from generic nouns rather than naming a real business. These
# are the ones Nominatim could never resolve, because there is nothing to
# resolve -- they were invented by the seed scripts.
PLACEHOLDER_PATTERNS = (
    r'\blocal\b', r'\btraditional\b', r'\bhighway\b', r'\btruck stop\b',
    r'\bcity cent(er|re)\b', r'\bdistrict\b', r'^view ?point\b',
    r'\bheritage walk\b', r'\bmeditation cent(er|re)\b', r'\bpilgrimage trail\b',
    r'\bmountain view (tower|point)\b', r'\bold town\b', r'\bbazaar\b$',
    r'\bmarket\b$', r'\bvillages\b', r'\bsites\b', r'\btemples\b$',
)
_PLACEHOLDER_RE = [re.compile(p, re.IGNORECASE) for p in PLACEHOLDER_PATTERNS]


def looks_like_placeholder(name):
    """Heuristic flag for the audit report only -- never used to delete
    anything automatically. A human confirms before any row is retired."""
    name = (name or '').strip()
    if not name:
        return True
    return any(rx.search(name) for rx in _PLACEHOLDER_RE)


# --------------------------------------------------------------------------
# Scoring / selection
# --------------------------------------------------------------------------

def completeness_score(tags):
    """Rank candidates so the best-documented real places win the limited
    slots per town, instead of an arbitrary Overpass ordering.

    An entry with a Wikidata link or a Commons photo will render a much
    better card than a bare name+point, so it is preferred -- but distance
    still breaks ties, so a town never fills up with places on its outskirts.
    """
    score = 0
    if tags.get('wikidata'):
        score += 6
    if tags.get('wikimedia_commons') or tags.get('image'):
        score += 5
    if tags.get('wikipedia'):
        score += 3
    if tags.get('name:en'):
        score += 2
    if tags.get('website') or tags.get('contact:website'):
        score += 1
    if tags.get('opening_hours'):
        score += 1
    if tags.get('cuisine'):
        score += 1
    if tags.get('description'):
        score += 1
    return score


def assign_to_nearest(candidates, locations):
    """Give every harvested OSM element to exactly one town: the nearest.

    Requirement from the brief: no restaurant/destination may appear under
    two dropdown locations. Because catchment radii overlap (Banepa,
    Dhulikhel, Panauti, Namobuddha and Kavre are all within ~20km of each
    other), the same cafe is returned by several town queries. Resolving
    by nearest centroid is deterministic and makes the duplicate
    impossible by construction rather than by a later dedup pass.

    `candidates` maps element_key -> (element, tags, (lat, lon)).
    `locations` is a list of (name, lat, lon).
    Returns {element_key: (location_name, distance_km)}.
    """
    assignment = {}
    for key, (_element, _tags, point) in candidates.items():
        lat, lon = point
        best_name, best_distance = None, None
        for name, loc_lat, loc_lon in locations:
            distance = haversine_km(lat, lon, float(loc_lat), float(loc_lon))
            if best_distance is None or distance < best_distance:
                best_name, best_distance = name, distance
        if best_name is not None:
            assignment[key] = (best_name, best_distance)
    return assignment


# Model field limits (web_interface/models.py). A value that exceeds one of
# these is rejected by the database, and import_data records it as a skipped
# row -- so it is cheaper and clearer to check while staging.
MAX_NAME = 255
MAX_CUISINE = 100
MAX_IMAGE_URL = 500


def fits_model_fields(name, cuisine='', image=''):
    """(ok, reason). Checks a staged row against the model's own column
    widths before it is ever written, so a 300-character OSM name shows up
    in audit.csv with an explanation instead of as a silent skip later."""
    if len(name) > MAX_NAME:
        return False, f'name is {len(name)} chars, over the {MAX_NAME}-char column limit'
    if len(cuisine) > MAX_CUISINE:
        return False, f'cuisine is {len(cuisine)} chars, over the {MAX_CUISINE}-char column limit'
    if len(image) > MAX_IMAGE_URL:
        return False, f'image URL is {len(image)} chars, over the {MAX_IMAGE_URL}-char column limit'
    return True, ''


def normalise_name(name):
    """For duplicate detection against rows already in the database.
    import_data upserts on name__iexact + location, so two rows differing
    only by case or punctuation would collide there anyway -- better to
    notice it while staging."""
    return re.sub(r'[^a-z0-9]+', ' ', (name or '').lower()).strip()
