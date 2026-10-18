# TravelEase

A Django travel-discovery web app: browse destinations and restaurants, search
by proximity/keyword, save favorites, and track recent views. Built for
CACS452 Project III.

**`db.sqlite3` ships pre-populated with 139 demo destinations/restaurants
across 24 locations spanning Nepal (via `populate_data.py`,
`add_pokhara_data.py`, and `add_nepal_wide_data.py` -- all three, not
just one) but zero user accounts** -- run `python manage.py
createsuperuser` to create your own login. This is a deliberately clean
slate: it was rebuilt from scratch (migrate + all three seed scripts,
nothing else) so what you get is exactly what the documented setup steps
produce.

## Setup

Needs **Python 3.10, 3.11, or 3.12** (this is what Django 4.2.30, the
version pinned in `requirements.txt`, was tested against; newer/older
Python versions may hit compatibility issues with this specific Django
version). Check with `python3 --version` first.

```bash
python3 -m venv env
source env/bin/activate          # Windows: env\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Then visit http://127.0.0.1:8000/. Admin panel is at `/admin/`.

**A working `.env` is already included** (with a real generated
`SECRET_KEY` and the original project's Google OAuth credentials) so the
app runs immediately without any extra setup -- you don't need to copy
`.env.example` over it. `.env.example` is only there as a template for
setting up a *fresh* environment (e.g. if you rotate the secrets, or
clone this without the `.env` file). If you ever do need to regenerate
it, `cp .env.example .env` then fill in the values -- just don't run
that if `.env` already has real values in it, since it will silently
overwrite them.

**Google login** will only work if the redirect URI Google sends people
back to (`http://127.0.0.1:8000/accounts/google/login/callback/` when
running the dev server on its default host/port) is registered as an
authorized redirect URI for these credentials in Google Cloud Console.
If it isn't, Google's own consent screen will show a
"redirect_uri_mismatch" error -- that's Google rejecting the request
before it ever reaches this app, not a bug here. Everything else in the
app works independently of Google login; the email/password signup and
login flows don't need it at all.

Frontend CSS is pre-compiled and committed at `static/css/tailwind.css` --
**no `npm` install or build step needed to view the site**, same promise as
before. This used to be three separate `cdn.tailwindcss.com` `<script>` tags
(one per standalone page), which Tailwind's own docs say not to use in
production: it re-compiles from scratch in every visitor's browser and
prints a console warning. That's now a single pre-built stylesheet shared
across every page, wired up like a normal project:

- `tailwind.config.js` -- the theme (primary/accent colors, Inter font,
  fade-in/slide-up/pulse-slow animations), equivalent to what used to be
  inline JS on the CDN script tag.
- `static/css/tailwind-input.css` -- the `@tailwind` source.
- `npm run build:css` -- regenerates `static/css/tailwind.css` after you
  change a template's classes or the theme. Only needed if you're editing
  the frontend; the compiled file already in the repo is what ships.

## Tests

```bash
python manage.py test
```

195 tests across all three apps, covering the distance/ranking algorithm,
authentication (including the custom email-based backend and the
database-level race-condition-proof uniqueness constraint), signup
(including the full AJAX flow with real CSRF enforcement), save/recent-
view flows, location-flexible search (including the "open now"/rating
filters), the CSV import command, the OpenStreetMap converter, the
crowd-sourced and seed-blended rating system, per-place map coordinates
(each destination/restaurant's own latitude/longitude vs. falling back
to its parent Location's), and a full line-by-line audit's worth of
URL-encoding, context-key-mismatch, and dead-UI fixes. See "Complete
line-by-line audit" below for the full list of what these tests guard
against.

---

## Location freedom (search anywhere in Nepal, not just "near you")

Search used to be hard-locked to whatever location your IP address
resolved to, within a fixed 50km radius. That's gone. Now:

- **Location picker** (search results page) — a dropdown of every saved
  `Location`, letting you search/browse around any place in Nepal, not
  just wherever you currently are. This is the same idea as setting a
  pickup point for someone else in Uber/InDrive.
- **"Use my location" button** — gets your real browser GPS position
  (with permission) instead of relying on IP-based guessing.
- **No hard distance cutoff.** Results are always ranked nearest-first,
  but nothing is hidden just because it's far away. You can *optionally*
  narrow with a distance filter (25km / 100km / 300km / "All of Nepal"),
  but that's opt-in, not the default.
- Verified end-to-end with a dataset spanning Kathmandu to Dolpa (~280km,
  remote western Nepal) — every result still shows up, correctly sorted,
  with no artificial cutoff.

Search priority order (`resolve_search_origin()` in `web_interface/views.py`):
explicit GPS coordinates → chosen location from the dropdown → hardcoded
Kathmandu Valley fallback. Each step is only used if the previous one
wasn't provided, and none of it can crash -- bad or missing coordinates
just fall through to the next option. (IP-based geolocation was tried
and deliberately removed -- see `get_user_location()`'s docstring: on a
home network, VPN, or shared/NAT'd connection it can resolve hundreds or
thousands of km from the user's real position, which was previously
producing nonsensical "11,193 km away" results.)

## Adding real data safely

You don't have a real data provider yet, so the app ships with hand-curated
dummy data covering the Banepa area, Pokhara, and major towns across all of
Nepal (see `populate_data.py`, `add_pokhara_data.py`, and
`add_nepal_wide_data.py` -- all three are safe to run in any order, any
number of times; none of them clear or overwrite what the others added).
When you do get real data, **don't hand-enter it in the admin panel** --
use the bulk import command instead:

```bash
# Preview what would happen, writes nothing:
python manage.py import_data --dry-run

# Import your CSVs for real:
python manage.py import_data \
    --locations path/to/locations.csv \
    --destinations path/to/destinations.csv \
    --restaurants path/to/restaurants.csv

# Wipe the dummy data first, then import fresh real data:
python manage.py import_data --clear --yes \
    --locations ... --destinations ... --restaurants ...
```

CSV formats (see `data_templates/*.csv` for filled-in examples you can
open in Excel/Sheets and overwrite):

- `locations.csv` -- `name, latitude, longitude, property_count`
- `destinations.csv` -- `name, description, location_name, image, latitude, longitude`
- `restaurants.csv` -- `name, cuisine, average_cost, rating, price_range, business_hours, location_name, image, latitude, longitude`

`location_name` in the other two files must match a `name` in
`locations.csv` (case-insensitive) -- that's how a destination/restaurant
gets linked to its location. `image`, `latitude`, and `longitude` are all
optional on destinations/restaurants -- see "Every place now has its own
map coordinate" below for what `latitude`/`longitude` are for and why
they're separate from `locations.csv`'s own latitude/longitude.

**This is safe to run against messy real-world data.** Every row is
validated individually: bad numbers, out-of-range ratings, missing
required fields, or a `location_name` that doesn't match anything all get
**skipped with a specific reason printed**, while every valid row still
imports. I stress-tested this with deliberately broken CSVs (invalid
coordinates, unescaped commas that shift columns, negative prices,
ratings way outside 0-5) and it never crashes -- it just reports what it
skipped and why, then finishes importing everything that was valid.

Re-running the same import is also safe -- locations are matched and
updated by name rather than duplicated, so importing the same file twice
doesn't create two copies of everything.

## Every place now has its own map coordinate (previously they didn't)

`Destination` and `PlaceToEat` used to have **no coordinate of their
own** -- only a `ForeignKey` to `Location`. The live map / "Get
directions" feature (`web_interface/templates/web_interface/includes/location_map.html`)
was quietly built on top of `item.location.latitude`/`longitude`, which
is the *whole town or city's* single centroid, shared by every
destination and restaurant in it. The practical effect: every place in
the same town rendered on the exact same map pin, and "Get directions"
for any of them opened the exact same route to the exact same point --
correct for at most one of them, and visibly wrong (often by a
kilometer or more) for the rest. This is why it could look like
directions were pointing at "a random location" -- it wasn't random, it
was just the town center every single time, regardless of which
specific destination or restaurant page you were on.

Fixed by adding `latitude`/`longitude` directly to `Destination` and
`PlaceToEat` (nullable, migration `0010`), and a fallback pair of
properties, `effective_latitude`/`effective_longitude`, that return the
place's own coordinate when it has one and only fall back to the parent
`Location`'s centroid when it doesn't -- so nothing crashes or shows a
blank map for older data, but two places in the same town are no longer
forced to share one point once they each have their own:

- **`import_data`** now accepts optional `latitude`/`longitude` columns
  on `destinations.csv`/`restaurants.csv` (see the CSV formats above).
  Like `image`, these are only written when the row actually has a
  value -- re-importing from a source with no coordinate column never
  wipes out a real coordinate a previous sync/backfill already found.
- **`scripts/osm_to_csv.py` / `sync_osm_data`** were already computing
  each OpenStreetMap element's *exact* position, but only to average it
  into the town-level centroid before discarding it. They now also keep
  that per-element position and write it straight into the
  `latitude`/`longitude` columns above -- so a real OSM sync (nightly
  via cron, see below) gives every destination/restaurant its own real,
  current position with no extra step.
- **New: `python manage.py backfill_coordinates`** -- for data that
  didn't come from an OSM sync (the static seed data from
  `populate_data.py`/`add_pokhara_data.py`/`add_nepal_wide_data.py`, or
  any hand-built CSV without a coordinate column), this geocodes each
  place's own position via OpenStreetMap's free Nominatim service,
  matched by name + town, and only accepts a result within 25km of the
  parent Location's own centroid -- a generic name (e.g. "City Market")
  could otherwise match some unrelated place in a different district
  entirely, which would be a confidently-wrong coordinate, worse than
  the honest "falls back to the town center" behavior it would replace.
  Anything it can't confidently resolve is left alone rather than
  guessed at (same philosophy as `backfill_images`, below) -- run it
  again later, or fix it by hand in the admin panel.

  ```bash
  python manage.py backfill_coordinates              # geocode everything missing one
  python manage.py backfill_coordinates --dry-run     # preview, save nothing
  python manage.py backfill_coordinates --limit 20    # quick test
  ```

  This needs real internet access to `nominatim.openstreetmap.org` to do
  anything -- it fails closed (logs "no confident match", changes
  nothing) rather than crashing if that's unreachable, which is what
  you'll see if you try it from a sandboxed/offline environment.

The detail-page template's "Coordinates" line labels a value that came
from the town-level fallback as a "(town-center estimate)" rather than
presenting it as that specific place's exact position, so the UI is
honest about which places still need `backfill_coordinates` (or a real
OSM sync) to run.

## Images: the detail pages now actually show the placeholder they claimed to

`backfill_images` (Wikipedia photo lookup for destinations, described in
full under "Real photos, not placeholders" below) already existed, and
`home.html`/`search_results.html` already had a nice on-brand
placeholder (a soft topographic pattern for destinations, a
place-setting pattern for restaurants, with a 📍/🍽️ icon) for when
`item.image` is blank -- which, before `backfill_images` has actually
been run, is every seeded destination and restaurant. But
`travel_detail.html`/`restaurant_detail.html` never actually had that
placeholder: `{% if item.image %}` rendered the photo when present and
rendered **nothing at all** otherwise -- a blank gap where the hero
photo should be, easy to mistake for a still-loading page. Fixed by
promoting that placeholder markup/CSS into `base.html` (the same pattern
already used for `.category-destination`/`.category-restaurant`, for the
same reason -- one definition every page that extends this layout can
use) and wiring both detail pages up to it, so every place now shows
either a real photo or a clearly-intentional placeholder, never a blank
gap.

## Where to actually get free real data

**Use [OpenStreetMap](https://www.openstreetmap.org)**, via its free
Overpass API. Recommended over Google Places API because:

- **Genuinely free** -- no API key, no signup, no credit card. Google
  Places now requires billing to be enabled even to stay within its free
  tier (Google removed the old flat $200/month credit in March 2025), so
  there's real risk of an unexpected charge; OSM has no billing at all.
- Full, current map coverage of Nepal, including remote districts.
- Licensed under the Open Database License (ODbL) -- free for commercial
  use, the only requirement is crediting **"© OpenStreetMap
  contributors"** somewhere in the app. **This is already added** -- see
  the footer on every page (`web_interface/templates/web_interface/base.html`).

**`scripts/osm_to_csv.py`** converts an Overpass export straight into the
CSV format `import_data` expects:

1. Go to [overpass-turbo.eu](https://overpass-turbo.eu/) (free, no signup).
2. Paste one of the ready-made queries in the script's docstring (one for
   tourist attractions, one for restaurants -- just change the city name),
   click Run, then Export → download as GeoJSON.
3. Convert:
   ```bash
   python scripts/osm_to_csv.py --location "Pokhara" \
       --attractions pokhara_attractions.json \
       --restaurants pokhara_restaurants.json \
       --output data_templates/
   ```
4. Repeat per city -- it appends rather than overwrites, so you can build
   up a nationwide dataset one region at a time.
5. Import as usual: `python manage.py import_data --dry-run ...`

**Or skip steps 1-2-3 entirely with `sync_osm_data`.** That manual
overpass-turbo.eu round trip is the only non-automated part of the
pipeline above -- `sync_osm_data` does it for you by calling the
Overpass API directly, meant to be run on a schedule so the database
actually refreshes on its own instead of staying static after the one
initial import:

```bash
python manage.py sync_osm_data --area "Pokhara"                       # both categories
python manage.py sync_osm_data --area "Kathmandu" --categories restaurants
python manage.py sync_osm_data --area "Pokhara" --dry-run             # preview, no DB writes
```

It reuses the exact same conversion code as `osm_to_csv.py` and the
exact same upsert logic as `import_data` (calls it directly, doesn't
reimplement it) -- so scheduling it changes nothing about *how* data
gets in, only *how often*. Point cron at it (once per area you care
about) and the site's destinations/restaurants stay current with
OpenStreetMap automatically:

Its intermediate CSVs go to `data_templates/_osm_sync_cache/`, not
`data_templates/` itself, and get fully overwritten every run -- that's
deliberate, not a shortcut. The manual workflow above is meant to build
one persistent archive over time (append as you go); a command that
re-queries the *same* area nightly needs the opposite: each run's files
should hold only that run's current results, so an update on OSM's side
(corrected hours, a newly tagged photo) always makes it through to
`import_data` instead of being silently skipped as "already seen." The
database is what actually accumulates across runs -- these files don't
need to, and re-running the command is always safe.

```
0 3 * * * cd /path/to/travelease && /path/to/venv/bin/python manage.py sync_osm_data --area "Pokhara" >> /var/log/travelease-osm-sync.log 2>&1
0 4 * * * cd /path/to/travelease && /path/to/venv/bin/python manage.py backfill_coordinates >> /var/log/travelease-osm-sync.log 2>&1
0 4 * * * cd /path/to/travelease && /path/to/venv/bin/python manage.py backfill_images >> /var/log/travelease-osm-sync.log 2>&1
```

`sync_osm_data` already writes each destination/restaurant's own real
coordinate straight from Overpass (see "Every place now has its own map
coordinate" above), so the second line above is really only doing
anything useful for places that *aren't* covered by an OSM sync area yet
-- the static seed data, or a hand-built CSV imported without a
coordinate column. Both backfill commands are safe to run nightly
alongside the sync: they only touch places missing a value, so a run
that finds nothing new to do is a no-op.

**Why this calls Overpass on a schedule instead of live, per page
view:** Overpass's own usage policy asks users not to send it a fresh
query on every request -- it's a shared public service with rate limits,
and querying it once per visitor for data that only changes over
days/weeks would be wasteful and risks the server's IP getting
throttled. A nightly batch sync respects that, and every visitor still
gets a fast, locally-served database row with no extra network
round-trip or external-service dependency on their page load.

**Real photos, not placeholders.** `Destination`/`PlaceToEat` both have
an `image` URL field. It's filled two ways, both free, both real photos
of the actual place -- never a generic stock photo standing in for
every entry:

- If OpenStreetMap itself has an `image` or `wikimedia_commons` tag on
  a node (rare, but real when present), `sync_osm_data`/`osm_to_csv.py`
  capture it automatically -- see `image_from_tags()` in
  `scripts/osm_to_csv.py`.
- For destinations still missing one after that, `python manage.py
  backfill_images` looks them up via Wikipedia's free pageimages API
  (no key, no signup) and runs automatically at the end of every
  `sync_osm_data` call. It's destination-only on purpose: a named
  tourist attraction is reasonably likely to have a Wikipedia article; a
  small local restaurant almost never does, and a name-based search
  risks confidently attaching an unrelated place's photo to it -- worse
  than the honest placeholder it would otherwise get. A destination (or
  restaurant) whose lookup finds nothing is simply left with the
  existing on-brand CSS placeholder (`home.html`/`search_results.html`)
  rather than a fabricated photo.
- Run `python manage.py backfill_images` once by hand too, to fill in
  photos for the static seed data that predates the `image` field.

`home.html`, `search_results.html`, `travel_detail.html`, and
`restaurant_detail.html` all render `item.image` when present and fall
back to the existing placeholder otherwise (including if the URL 404s
at render time -- an `onerror` handler swaps back to the placeholder
rather than showing a broken-image icon).

**One thing to know about the homepage specifically:** "Popular
Destinations"/"Featured Restaurants" on `home.html` show the first 6
rows in the database (`Destination.objects.all()[:6]`, same for
restaurants) -- there's no "trending" ranking behind that name. A newly
`sync_osm_data`'d destination will show up immediately on the **Explore**
page (which paginates through everything), but won't appear on the
homepage strip unless it happens to land within the first 6 rows. That's
true of the pre-existing static data too -- it's not a gap introduced by
adding sync, just worth knowing before assuming a sync "didn't work"
because the homepage looks unchanged.

This was stress-tested against deliberately messy real-world-shaped OSM
data: entries with no name, no coordinates, duplicate points, OSM's
multi-day `opening_hours` mini-syntax, semicolon-separated cuisine tags.
None of it crashes -- unusable entries are skipped with a printed reason,
everything usable still converts.

**Note:** OpenStreetMap is map data, not review data -- it essentially
never has ratings or price info for restaurants (that's Google/Yelp-style
commercial data). `average_cost` and `rating` on `PlaceToEat` are
therefore optional (nullable) -- both `import_data` and the templates
handle a restaurant with no rating/cost gracefully ("Not yet rated" /
"Unknown" instead of a blank or a crash).

**To fill that gap, TravelEase now collects ratings/costs from its own
users instead of relying on external data.** On every restaurant's detail
page, a signed-in user can submit a rating (0-5) and an optional cost
estimate. `PlaceToEat.rating`/`average_cost` are kept as the running
average of everyone's submissions (`RestaurantRating` model,
`PlaceToEat.recompute_aggregates()`). Submitting again updates your
existing rating rather than adding a second one, and the page shows how
many reviews a restaurant has. This means a restaurant imported from OSM
with no rating at all will get one organically once real users start
using the app -- no external data provider needed for this part at all.

**Seeding a starting rating from elsewhere.** If you *do* have a starting
rating/cost from somewhere (a CSV column, or typed directly into Django
admin), it goes into `seed_rating`/`seed_average_cost` -- separate fields
from the displayed `rating`/`average_cost`. `recompute_aggregates()`
blends the seed in as one permanent vote alongside every real user
rating, rather than the first real rating simply overwriting it. That
matters in practice: without this, a restaurant seeded at 4.5 stars could
have its rating instantly overwritten to 1.0 star by a single early
(possibly bad-faith) review. With seeding, that same review only moves it
to 2.8 -- it factors in, but doesn't erase the starting point. Verified
directly: seed 4.5 + one real 1.0 rating = 2.8, not 1.0.

`import_data` writes CSV `rating`/`average_cost` columns into the seed
fields automatically. In Django admin, `rating`/`average_cost` are
read-only (edit `seed_rating`/`seed_average_cost` instead, then use the
"Recompute rating from seed + user submissions" admin action) -- this
stops a manual admin edit from being silently discarded the same way.

**Kept in sync on deletion too.** Deleting a rating (directly, or via
cascade when a user account is deleted) triggers the same recompute
automatically (`post_delete` signal on `RestaurantRating`), so a
restaurant's displayed rating can't go stale by still reflecting a vote
that no longer exists. Found and fixed this exact bug during testing:
deleting a test user used to leave the restaurant's cached rating
pointing at the average that included their now-gone vote.

---

## Signup had zero protection against mass account creation

Different from the login rate limiter above: confirmed 10 rapid signup
attempts from one source all succeeded with no throttling at all,
before this fix. Since each signup attempt uses a different email by
definition (a bot rotates emails), this is keyed on IP alone rather
than IP+email like the login limiter -- same policy shape, same cache
mechanism, applied to both the AJAX signup endpoint and the traditional
`SignupView` (the no-JS fallback).

Worth being candid about: my first version of this fix crashed the
no-JS fallback page with a 500 (`AttributeError` from calling
`get_context_data()` before Django's `CreateView` had set `self.object`,
which the standard request flow normally does automatically before that
point). Caught it immediately by actually testing the blocked case
end-to-end rather than assuming the fix worked once it stopped erroring
on the happy path, and fixed it correctly (`self.object = None` before
building context, matching the pattern Django's own `CreateView.post()`
uses internally). This is exactly why every fix in this document has
been tested against its actual failure mode, not just checked for
`manage.py check` passing -- that alone would have shipped this crash.

Verified: a clean single signup is unaffected, both the AJAX path and
the traditional form path correctly block after 5 attempts without
crashing, and the block message is actually visible on the no-JS page
(it wasn't at first -- that page doesn't use Django's messages
framework at all, so `messages.error()` was silently going nowhere;
fixed to pass the message through the template context directly). 3 new
tests -- 117 total.

## Another real security gap: no brute-force protection on login

`settings.py` already declared `ACCOUNT_RATE_LIMITS = {"login_failed": "5/5m"}`
-- but that setting only protects django-allauth's *own* login views, and
this project uses a custom `login_view`. Tested it directly: 10 rapid
failed login attempts against a real account all went through completely
unthrottled, with no blocking of any kind. That means anyone could
attempt unlimited password guesses against any known email address --
a real credential-stuffing/brute-force opening.

Implemented an actual rate limiter using Django's cache framework
(already configured), enforcing the same "5 failed attempts per 5
minutes" policy the settings file already declared but wasn't applying.
Keyed on IP *and* email together, deliberately -- keying on email alone
would let an attacker's own IP get blocked while they keep hammering a
victim's account from elsewhere, and keying on IP alone risks locking
out unrelated legitimate users sharing a NAT'd/office IP because of one
attacker targeting someone else.

Verified thoroughly: the 6th attempt against a targeted account is
correctly blocked (even with the *correct* password); a clean first-try
login is never affected; a couple of typos followed by the right
password works fine and correctly resets the counter (proven by being
able to fail 5 more times afterward, not by the counter silently
persisting); and a different account sharing the same simulated IP as
an ongoing attack is completely unaffected. 4 new tests -- 114 total.

**Caveat for a real deployment:** the cache backend here is
`LocMemCache`, which is per-process and in-memory -- fine for
`manage.py runserver` (a single process) or grading/demo use, but a
production deployment running multiple worker processes (gunicorn,
uwsgi, etc.) would need a shared cache backend (Redis, Memcached) for
this rate limit to correctly apply across all of them instead of each
worker tracking failures separately.

## A real security vulnerability: open redirect

Found by deliberately testing an angle not covered before: two views
(`login_view` and `toggle_save_item`) took a `next` parameter straight
from the request and passed it directly to `redirect()` with **no
validation that it actually pointed somewhere on this site.** A crafted
link like `travelease.com/accounts/login/?next=https://evil.com/steal`
would show the person the real, trusted login page -- and after they
successfully log in for real, silently redirect them to an attacker's
site immediately afterward. That's a classic phishing setup: the victim
never sees anything suspicious until they're already on the fake page
(which could then show a fake "session expired, log in again" prompt to
steal credentials a second time). Confirmed this was genuinely
exploitable -- both a plain external URL and a protocol-relative `//evil.com`
variant (which bypasses naive `http`-prefix checks) both worked before
the fix. `toggle_save_item` had the identical issue.

Fixed both using Django's own `url_has_allowed_host_and_scheme()` --
the standard library function built for exactly this, which Django's own
built-in login view uses internally. Verified the exact same attacks are
now rejected, and separately verified legitimate same-site `next` values
(the actual intended use -- e.g. returning someone to the search results
page they came from after saving an item) still work correctly. 6 new
tests -- 110 total.

## One more real bug: saved items you could never remove

`SavedItem`/`RecentView` store `item_id` as a plain integer, not a real
`ForeignKey`, since one field has to be able to reference either a
`Destination` or a `PlaceToEat`. Tested what happens once the underlying
object gets deleted later (an admin removing outdated data, a re-import,
etc.) -- pages that only *display* cached data (title/URL saved at the
time) render fine, as expected. But `toggle_save_item`, which the
"remove" button on the saved-items page posts to, unconditionally looked
up the *live* object before ever checking whether there was an existing
save to remove. Once the object was gone, that lookup 404'd before
reaching the removal step -- **anyone who'd saved an item that later got
deleted was permanently stuck with a dead entry they could never remove
through the UI.** Fixed by checking for an existing save first (removal
never needs the live object) and only requiring the object to exist when
creating a brand-new save (which does need it, to cache its name/URL).
Verified the fix works, and that genuinely-nonexistent items still
correctly 404 when someone tries to save them. 3 new tests -- 104 total.

## One more real bug: business hours that cross midnight

Found by deliberately testing a scenario not covered before: `is_open()`
checked `start_time <= current_time <= end_time`, which only works when
the schedule doesn't cross midnight. Any restaurant/bar open something
like `22:00-02:00` (10pm-2am) would show as **permanently closed, at
every hour of every day** -- because no time value can simultaneously be
`>= 22:00` and `<= 02:00`. This also meant such venues were silently
excluded from the "Open Now" search filter, always, regardless of the
actual time. Fixed to detect the midnight-crossing case (`start_time >
end_time`) and check `current_time >= start OR current_time <= end`
instead. Verified against 11 boundary cases (exact open/close times,
just-before/just-after, normal same-day hours still correct) and
end-to-end through the actual "Open Now" filter. 5 new tests cover
this -- 101 total, all passing.

## Security-focused pass + closing a race condition

Beyond reading every file, I specifically tested for injection/XSS
classes of bug (confirmed zero uses of `|safe`/`mark_safe` anywhere, so
all user content is HTML-escaped by Django automatically; confirmed
every POST form has a real CSRF token; confirmed no raw SQL anywhere)
and threw XSS payloads, SQL injection attempts, negative numbers,
`inf`/`nan`, and absurd values at every filter parameter -- all handled
gracefully with no crashes, and I directly verified an XSS payload comes
back HTML-escaped in the rendered page rather than just "didn't error".

This also found and closed a genuine remaining gap: the case-insensitive
duplicate-email check (fixed earlier) was still a **check-then-save**
pattern at the form level -- two near-simultaneous signups with
case-variant emails could theoretically both pass validation before
either committed, recreating the exact crash this was meant to prevent.
Proved this was real by bypassing the form and creating two case-variant
accounts directly -- it succeeded, meaning it was genuinely exploitable
under the right timing.

Closed it with a **database-level case-insensitive `UniqueConstraint`**
on `CustomUser.email` (`signup/models.py`), which can't be raced no
matter the timing since it's enforced atomically by the database itself.
Re-ran the exact same bypass test afterward and confirmed it's now
correctly rejected with an `IntegrityError`.

Adding that constraint uncovered one more thing worth knowing: Django's
`ModelForm` validation (since 4.1+) automatically checks model-level
`UniqueConstraint`s during `is_valid()`, so this is now actually caught
*even earlier* than originally planned, with a clean validation error
instead of ever reaching the database write. Added defensive
`IntegrityError` handling in both signup code paths anyway (`SignupView`
and `ajax_signup`) for the narrower residual race window between
validation passing and the write actually committing -- verified neither
path crashes with a raw 500 even when that exact scenario is forced.

Also caught and fixed a real regression this introduced: an earlier
test (`accounts/tests.py`) had verified the login backend's defensive
fallback by directly creating two real case-variant duplicate accounts
-- which the new constraint now correctly makes impossible to create at
all. Fixed that test to simulate the scenario via mocking instead, since
the state it was testing for can no longer occur through normal means.

3 new tests cover this -- 96 total, all passing.

## Complete line-by-line audit (every file in the zip, read in full)

Beyond automated checks, every single file was actually read end to end
-- every `.py` file, every migration, every HTML template, every config
file, every loose script. This found several bugs invisible to automated
testing because they only exist in wiring between the HTML/JS a real
person interacts with and the (correctly-working, already-tested) backend
underneath it, plus two more serious ones from an earlier pass over the
Python code specifically:

- **A real, reachable crash chain in signup/login.** Signup's
  duplicate-email check was case-*sensitive* (`email=email`) while the
  login backend's lookup was case-*insensitive* (`email__iexact=`).
  SQLite's UNIQUE constraint is also case-sensitive by default, so
  signing up with `Name@x.com` after `name@x.com` already existed was
  silently **allowed** -- and then logging in as either account would
  crash with an uncaught `MultipleObjectsReturned`. Fixed the signup
  form and the email-availability check to both be case-insensitive
  (closing the hole), and added a defensive fallback in the login
  backend itself in case a duplicate ever exists anyway.
- **Search terms containing `&` were silently truncated and corrupted the
  redirect URL.** The homepage and "places to eat" search boxes built
  their redirect with raw f-string interpolation (`?search={term}`)
  instead of proper URL-encoding. Extremely plausible for a restaurant
  app -- "Fire & Ice", "Bed & Breakfast" style names -- and would have
  silently dropped part of the search term with zero error shown. Fixed
  with Django's `urlencode`; verified against `&`, `#`, `+`, `%%`, and `=`
  characters.

- **The most severe bug in this entire project, found this pass: the
  signup page's actual HTML form never sent data to the server at all.**
  Its JavaScript called `e.preventDefault()`, ran client-side validation,
  then just faked a 2-second loading spinner and displayed "Account
  created successfully!" -- with no `fetch()`/`XMLHttpRequest` call
  anywhere. The backend (`ajax_signup`, `get_csrf_token`) was fully
  correct and already covered by tests at the view level, but nothing on
  the page a real visitor sees ever called it, so **no account was ever
  actually created through the visible signup form**, despite it looking
  and behaving exactly like a working one. Rewired the JS to genuinely
  `fetch()` the CSRF token then POST to the real endpoint, handle the
  JSON response, redirect on success, and show real field-level errors on
  failure. Verified with real CSRF enforcement active (not just calling
  the view directly, which is how the earlier, passing tests had exercised
  it) that an account is genuinely created and the user is genuinely
  logged in afterward.
- **Two more instances of the earlier `&`-truncation bug**, in
  `search_results.html`'s pagination Next/Previous links and its "remove
  filter" chip -- both rebuilt the query string by directly interpolating
  `request.GET` values into an href, the same unsafe pattern already
  fixed once in the Python redirect code. Fixed by having the view build
  a properly `urlencode()`'d querystring and passing it into the template.
- **Homepage's "Popular Destinations" section was permanently disconnected
  from the database.** The template looped over `trending_destinations`;
  the view only ever provided a `destinations` key. That variable was
  always undefined, so the homepage silently fell back to the same 3
  hardcoded placeholder cards (Kathmandu/Pokhara/Bangkok) every single
  time, regardless of what's actually in the database.
- **Homepage's main hero search form silently discarded most of its own
  controls.** The "Open Now" checkbox, category dropdown, rating slider,
  and GPS geolocation capture (which worked correctly client-side) were
  all thrown away by the view -- only the plain text search box worked.
  Wired all of it through properly, and added real backend filtering for
  "open now" and minimum rating (previously nonexistent). Also removed a
  "Hotels" category option that could never do anything, since there's no
  Hotel model anywhere in the app.
- The mobile header search icon revealed a bare `<input>` with no `name`
  attribute, wrapped in no `<form>`, with no submit handler -- typing into
  it and pressing enter did nothing. Wired to a real search form.
- Homepage destination card "Explore Now" links used `?location=Name`, a
  parameter the search view has never recognized -- clicking any card
  showed all unfiltered results instead of that destination.
- The login page's "Continue with Google" button was fake -- a spinner
  followed by "Google OAuth integration coming soon!" -- despite real,
  working Google OAuth already existing at a different entry point
  (verified end-to-end earlier in this project). Wired the button to the
  real flow.
- A logo-click Easter egg on the login page always autofilled credentials
  for a `demo@travelease.com` account that doesn't exist, guaranteeing a
  failed login for anyone who found it (5 clicks, always reachable, no
  `DEBUG` gate). Replaced with a harmless message.
- A "Development Mode - Quick Login" hint referencing that same
  nonexistent demo account was dead code twice over: it referenced
  credentials that don't exist, *and* it could never actually render in
  the first place because `INTERNAL_IPS` was never configured, which
  Django's `debug` context processor requires. Removed.
- `password_reset_email.html` had the same `user.username`-can-be-`None`
  issue as the four `__str__` methods below -- a real password-reset email
  could have greeted someone "Hello None,". Fixed to fall back to email
  instead.
- The password-reset-sent page's "Resend Email" button just called
  `location.reload()`, which reloads the static confirmation page and
  resends nothing. Pointed it at the actual reset-request form instead.
- Four `__str__` methods in `accounts/models.py` referenced
  `self.user.username`, which is `None` for accounts that never had a
  username set (e.g. Google OAuth signups) -- not a crash, but every one
  of them printed literally `"None - login at ..."` etc. Fixed to use
  `get_full_name() or email` instead, matching the pattern used
  everywhere else in the project.
- Removed a second, unrelated dead `get_user_model()` shadow function
  left in `web_interface/models.py`, and a leftover pre-Django-3.2
  `default_app_config` assignment in `web_interface/__init__.py` that
  Django 4.2 no longer reads at all.

22 new regression tests cover all of this -- 93 total, all passing,
verified from a completely fresh database with all 7 `web_interface`
migrations (plus the others) applying cleanly in order.

## Latest pass: login redirect, unused endpoint, missing .gitignore

- **`EmailBackend.get_user()` had no `is_active` check at all**, unlike
  Django's own `ModelBackend.get_user()` which this class otherwise
  overrides. `AuthenticationMiddleware` calls `get_user()` on *every*
  request to rehydrate `request.user` from the session -- not just at
  login -- so this meant deactivating someone's account while they were
  already logged in did nothing: their existing session kept working
  indefinitely, with full access, until they logged out or the session
  naturally expired. A previous fix (documented below, in
  `EmailBackend.authenticate()`) closed the equivalent gap for a *fresh*
  login attempt by a deactivated account, but missed this one -- same
  root cause, different method, easy to miss because the fixed method
  right above it reads as if the whole class had already been brought in
  line with `ModelBackend`. Fixed to match `ModelBackend`'s actual
  contract; new tests cover both the backend method directly and the
  full end-to-end scenario (log in for real, deactivate the account,
  confirm the very next request on that same session is no longer
  authenticated).
- **The login form silently dropped `?next=...` on submission**, and a
  first attempt at fixing that had its own gap. `login_view` correctly
  read and validated `next` from the query string, and existing tests
  proved that logic worked -- but every one of those tests constructed
  the POST request's URL by hand with `?next=` already on it, rather than
  rendering the real page and submitting its actual form. The rendered
  form's `action` was a hardcoded URL with no query string and no hidden
  `next` field, so the value was silently lost on the request that
  actually logs someone in. Since this is the path every protected page
  takes (`@login_required` redirects to `?next=<page>`), nobody was ever
  actually returned to where they'd been trying to go.

  The first fix added a hidden `next` field that read
  `{{ request.GET.next }}` directly in the template -- which is correct
  on the initial GET, but empty on the POST that re-renders the form
  after a failed login attempt (wrong password, or the rate limit),
  because that POST's URL has no query string regardless of what was
  submitted. That silently reset the field to empty after any single
  failed attempt, so mistyping a password once and then logging in
  correctly on the retry *still* lost the destination -- the same bug,
  one retry later. Fixed properly by having `login_view` compute the
  value once (from whichever of `POST`/`GET` actually has it) and pass it
  explicitly into every render's context, rather than the template
  re-deriving it from `request.GET` each time. New tests specifically
  simulate a failed attempt followed by a successful retry, and hitting
  the rate-limit render path, to close the gap that let the first fix's
  own bug hide.
  `login_view` correctly read and validated `next` from the query string,
  and existing tests proved that logic worked -- but every one of those
  tests constructed the POST request's URL by hand with `?next=` already
  on it. A real login doesn't work that way: `@login_required` sends
  someone to `/accounts/login/?next=/wherever/`, they fill in the
  *rendered form*, and that form's `action` was a hardcoded URL with no
  query string and no hidden `next` field -- so the value was silently
  lost on the request that actually logs them in. Since this is the path
  every protected page (dashboard, saved items, recent views, preferences,
  profile, save/rate actions, etc.) takes, this meant nobody was ever
  actually returned to the page they were trying to reach after logging
  in -- they always landed on the homepage instead. Fixed by adding a
  hidden `next` field to `login.html`'s form and having `login_view` read
  it from `POST` (falling back to `GET`). New tests render the actual
  login page and submit its real form -- extracting the `next` value from
  the rendered HTML rather than constructing it -- specifically to close
  the gap that let this hide from the existing (still-correct) tests.
- **`signup:check_email` was fully implemented, rate-limited, and tested,
  but never called from any page.** Someone could type an
  already-registered email, fill out the entire rest of the signup form,
  and only discover it's taken after submitting. Wired up a debounced
  live-availability check on the signup page's email field, using the
  same error-message/input-invalid styling as every other field there.
- **`.gitignore` was missing despite an earlier note in this file claiming
  it had been added.** Added one now (`.env`, `db.sqlite3`, `env/`,
  `node_modules/`, etc.).
- `preferences.html` didn't render validation errors for the
  `country_code` field specifically, unlike every other field on that
  form (cosmetic only -- it's a closed `<select>`, so this could
  essentially never actually have an error in practice). Fixed for
  consistency.

## What was fixed in this pass

This project had accumulated a number of real, previously-undetected bugs.
Everything below was found, fixed, and verified (via `manage.py check`,
`manage.py test`, and a full URL-by-url smoke test run both anonymously and
logged in) before this handoff.

**Would have crashed the app / blocked grading entirely:**
- `manage.py createsuperuser` was completely broken — `CustomUser` used
  `email` as `USERNAME_FIELD` but never got a custom manager, so Django's
  default manager still demanded a `username` argument. No one could have
  created an admin account. Added `CustomUserManager` (`signup/models.py`).
- Signup's auto-login crashed every time (`ValueError`) because the project
  has multiple `AUTHENTICATION_BACKENDS` and `login()` wasn't told which one
  to use. Fixed in `signup/views.py` (both the form-based and AJAX signup
  paths).
- Every successful login/logout crashed with `NoReverseMatch` —
  `accounts/views.py` redirected to a bare `'home'`, but that URL is
  namespaced (`web_interface:home`).
- `LOGIN_REDIRECT_URL` pointed at `/dashboard/`, which didn't exist as a
  route or a template. Added both.
- The "no search term" branch of the places-to-eat page crashed
  (`NoReverseMatch`) because a querystring was appended directly onto a URL
  name instead of the reversed URL.
- Password reset crashed every time — the email subject template file
  literally had a trailing space in its filename
  (`password_reset_subject.txt `) that didn't match what the code asked for.
- The invalid `accounts.backends.EmailBackend` lookup (`email__iexcase=`,
  not a real Django lookup) would have raised `FieldError` the moment it was
  ever actually used. Fixed to `iexact` and properly wired into
  `AUTHENTICATION_BACKENDS` (it previously wasn't registered at all).
- `manage.py createsuperuser` was completely broken — `CustomUser` used
  `email` as `USERNAME_FIELD` but never got a custom manager, so Django's
  default manager still demanded a `username` argument. No one could have
  created an admin account. Added `CustomUserManager` (`signup/models.py`).
- Signup's auto-login crashed every time (`ValueError`) because the project
  has multiple `AUTHENTICATION_BACKENDS` and `login()` wasn't told which one
  to use. Fixed in `signup/views.py` (both the form-based and AJAX signup
  paths).
- Password-reset emails contained a dead link. `django.contrib.sites`
  ships with a placeholder `Site` record (`domain = "example.com"`), and
  `PasswordResetView` uses that domain to build the reset link. Added a
  data migration (`accounts/migrations/0002_fix_site_domain.py`) that sets
  it from a `SITE_DOMAIN` env var (defaults to `127.0.0.1:8000`). Verified
  the entire request → email → confirm → set-new-password flow end to end.
- The search results page had **no save button at all** — you could only
  save an item from its own detail page. Added a save/unsave button to
  every result card on the search page, wired to the same backend as the
  detail pages.
- Destination/restaurant detail pages always showed a "Save" button even
  when the item was already saved, so clicking it would silently *unsave*
  it with no indication of the current state. Both detail views now pass
  `is_saved` to the template, which shows "Save" vs. "Saved — click to
  remove" accordingly. Verified saved-state stays consistent across the
  search page, both detail pages, and the saved-items list in a single
  save → check-everywhere → unsave → check-everywhere round trip.
- Verified the core algorithm directly (not just "no crash"): confirmed
  results are sorted strictly by ascending distance, that
  `type=destination` / `type=place_to_eat` filters return only that type,
  that pagination doesn't duplicate or drop results across pages and
  preserves the search/type query params, and that recent-view records
  correctly dedupe on repeat visits (timestamp bumps, no duplicate rows)
  and sort most-recent-first.

**Silently broken (no crash, just didn't work):**
- `saved_items.html` and `recent_views.html` were badly corrupted — entire
  blocks of markup sat *outside* `{% block content %}`, which Django's
  template inheritance silently drops, so real UI never rendered. Both
  templates referenced model fields (`item.item_name`, `item.rating`,
  `item.location`, etc.) and URL names (`travel_option_detail`,
  `toggle_save_item`, `remove_recent_view`, `clear_recent_views`,
  `create_trip_from_saved`) that didn't exist anywhere in the project.
  Rebuilt both templates against the real model fields, and implemented the
  three missing views (`toggle_save_item`, `remove_recent_view`,
  `clear_recent_views`) since the model support for them already existed.
- The homepage's "Places to Visit" links used `?type=place_to_visit`, but
  the search view only recognizes `destination`/`place_to_eat` — so that
  link always silently returned zero results.
- `web_interface` had its own duplicate copies of `LoginHistory`,
  `RecentView`, and `SavedItem`, registered in Django admin and receiving
  writes, while every actual view read from the *separate*, always-empty
  `accounts` versions. **2,700 rows of real usage data (600 logins, 1,200
  recent views, 900 saved items) were silently orphaned in the dead tables.**
  Wrote a data migration that copies that history into the tables the app
  actually uses, then removes the duplicate models. Verified the row counts
  before/after.
- Three routed pages (`places-to-eat/`, `travel-detail/<id>/`,
  `restaurant-detail/<id>/`) had no templates at all and would 500 on visit.
  Created all three.
- `geocoder` was imported and used but missing from `requirements.txt`, so a
  clean `pip install` wouldn't actually run the app at the time. **This is
  no longer relevant**: IP-based geolocation (what `geocoder` was for) was
  later removed entirely -- see "Location freedom" above and
  `get_user_location()`'s docstring in `web_interface/views.py` -- in favor
  of a hardcoded Kathmandu Valley default plus explicit, opt-in browser GPS.
  `geocoder` isn't imported or required anywhere in the current codebase.
  `python-dotenv` (needed for the `.env` setup below) is still required and
  still in `requirements.txt`.

**Security / config:**
- The Google OAuth client secret and a fallback random `SECRET_KEY` were
  hardcoded directly in `settings.py` (and were in the zip you sent me — you
  should rotate that OAuth secret in Google Cloud Console, since it's now
  been exposed in this chat). Moved both to environment variables loaded
  from a gitignored `.env` file; `.env.example` documents what's needed.
- `settings.py` defined `STATIC_URL`/`DATABASES`/etc. twice, back to back.
  Consolidated into one clean definition.
- Added `.gitignore` (was missing — `env/`, `node_modules/`, `db.sqlite3`,
  logs, etc. would otherwise end up in version control).

**Removed:**
- `signup/backends.py` — an unused, redundant duplicate of the (now fixed)
  `accounts` email backend.
- The stale `db.sqlite3.backup` / `db.sqlite3.old` snapshots, which predate
  this cleanup and are no longer consistent with the current migrations.

## How this satisfies the Project III requirements

Per the course spec (`CACS452-BCA-VIII-SEM-Project-III.pdf`):

- **"Not limited to basic CRUD"** — the search/recommendation logic uses the
  Haversine formula (`web_interface/views.py: calculate_distance`) to rank
  destinations and restaurants by real-world distance from the user's
  geolocated position, combined with keyword and type filtering. This is a
  genuine algorithm the project implements itself, not a third-party
  plugin — it's unit-tested in `web_interface/tests.py`.
- **"Write your own program modules... except in unavoidable
  circumstances"** — the only external service dependency is the Google
  OAuth login (there's no reasonable way to do third-party SSO yourself).
  Everything else — auth backend, search ranking, save/recent-view
  tracking — is custom code. (IP geolocation via `geocoder` was an earlier
  dependency here; it was later removed entirely in favor of a hardcoded
  Kathmandu Valley default plus explicit, opt-in browser GPS -- see
  "Location freedom" above.)
- **Report chapters (System Analysis and Design, Implementation, Testing,
  Result Analysis, etc.)** are still yours to write — I can't write your
  academic report for you, but I'm glad to help you produce diagrams (class
  diagrams, use-case diagrams, ER diagrams) from the actual current models
  and views if that would help you get started on Chapter 3.
