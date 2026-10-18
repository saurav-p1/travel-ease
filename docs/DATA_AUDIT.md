# TravelEase — coordinates & images audit

## The short version

Nothing needs *verifying*. All 76 destinations and all 63 restaurants have
`latitude`, `longitude` and `image` set to `NULL`. There is no inaccurate
data to correct — there is no data at all.

You also already own the tooling to fix this. It's better than anything I
could do by hand, and it has never been run.

## Why it's all empty

`web_interface/management/commands/` already contains:

- `backfill_coordinates.py` — geocodes every place via OpenStreetMap
  Nominatim, with a 25 km sanity check against the parent town's centroid
  so a generic name can't get attached to a same-named place in another
  district.
- `backfill_images.py` — fills `Destination.image` from Wikipedia's
  `pageimages` API.
- `sync_osm_data.py` and `scripts/osm_to_csv.py` — a full OSM import path
  that populates coordinates and OSM-tagged photos directly.

These are well-built and correct. They have never successfully run, because
every one of them starts by importing `web_interface.models`, which loads
Django, which resolves `AUTH_USER_MODEL = 'signup.CustomUser'` — and that
table does not exist in your database. Same root cause as the admin problem
from before.

**So: fix the database first, then run the commands.** Order matters.

```bash
cp db.sqlite3 db.sqlite3.broken-backup
rm db.sqlite3
python manage.py migrate
python manage.py loaddata places_backup.json
python manage.py createsuperuser

python manage.py backfill_coordinates --dry-run    # look first
python manage.py backfill_coordinates
python manage.py backfill_images
```

`--dry-run` reports what it *would* save without writing. Use it.

I did not hand-enter coordinates for you. Doing 139 lookups manually would
be slower, unversioned, unrepeatable, and would skip the sanity check your
own command applies. Running `backfill_coordinates` is strictly the better
move.

## The problem the tooling can't solve

A geocoder can only find places that exist. A large share of your seed data
is invented.

I checked a sample against Google Places:

| Query | Result |
|---|---|
| Boudhanath Stupa, Kathmandu | Real — 27.721469, 85.362035 |
| Phewa Lake, Pokhara | Real — 28.215384, 83.945317 |
| Janaki Mandir, Janakpur | Real — 26.730386, 85.925559 |
| "Local Bhatti", Banepa | **No such business.** Nearest real match: Athiti Restaurant and Thakali Kitchen |
| "District Kitchen", Kavre | **No such business.** Top match was District Kitchen + Cocktails in *Austin, Texas* |
| "Truck Stop Cafe", Khopasi | **No such business** |
| "Juju Dhau House", Bhaktapur | **No such business.** Real shops exist (Sainju Juju Dhau Bhandar, "Famous juju dhau") but not under this name |

### All 63 restaurants are fictional

The naming pattern gives it away — "Local Dine", "Highway Dhaba", "Local
Chulo", "Pilgrims Rest", "View Point Restaurant", "District Kitchen". These
are placeholder strings, not businesses. `backfill_coordinates` will
correctly report "no match" for essentially all of them and leave them
falling back to the town centroid.

That fallback is exactly what breaks your "Get directions" feature: every
restaurant in a town routes to the same shared point.

### 24 destinations are also placeholders

These have no real-world referent and will not geocode:

Banepa Market · Old Banepa Town · Banepa Heritage Walk · Dhulikhel
Viewpoint · Dhulikhel Old Town · Mountain View Tower · Old Panauti ·
Mountain View Point · Meditation Center · Pilgrimage Trail · Khopasi
Market · Khopasi Viewpoint · Local Temples · Kavre District Center ·
Local Heritage Sites · Traditional Villages · Old Pokhara Bazaar · Tharu
Village · Tindhare Viewpoint · Rani Mirchaiya Wildlife Reserve area ·
Biratnagar Bazaar · Tinau Riverside · Butwal Bazaar · Jumla Bazaar

The remaining **51 destinations are real named landmarks** and should
geocode cleanly.

### One factual error

Destination id **74, "Chandannath Temple, Surkhet"** — Chandannath Temple is
in **Jumla**, not Surkhet. You already have it correctly as id 77
("Chandannath Temple, Jumla"). Verified coordinates: 29.274203, 82.183579.
Delete id 74.

## Images

For **destinations**, `backfill_images` is the answer. Wikipedia/Wikimedia
Commons images are freely licensed and the API is keyless. Expect it to fill
most of the 51 real landmarks and none of the 24 placeholders.

For **restaurants**, I have to give you an answer you won't like: **Google
Maps photos are not usable here.** Google's Places terms require photos to
be fetched through their Photo API with attribution, and prohibit storing or
hotlinking the resulting URLs independently. Your `image` field is a plain
`URLField` holding a bare URL, which is precisely the pattern the terms
disallow. Using them would put a licence violation into your project.

Your model's own docstring already reached the right conclusion:

> restaurants are deliberately not run through the Wikipedia lookup
> backfill_images uses for destinations, since a small local business is
> very unlikely to have its own Wikipedia article and a name-based search
> would risk attaching an unrelated place's photo

Realistic options for restaurant photos, best first:

1. **Replace the fictional restaurants with real ones** and let
   `sync_osm_data` pick up OpenStreetMap `image` tags. Real coverage in
   Nepal is thin, but what you get is genuine and correctly licensed.
2. **Let submitters supply the photo.** You already have `/submit-place/`
   and an approval queue. Real user-contributed photos are the normal
   solution for this exact problem, and the moderation path already exists.
3. **Ship without restaurant photos.** Your templates already have
   placeholder CSS for this case. An honest empty state beats a wrong photo.

## What I'd do next

The fictional data is the real blocker, and no script fixes it. Concretely:

1. Rebuild the database and run both backfill commands. That gets you 51
   real destinations with coordinates and most with photos.
2. Delete destination id 74.
3. Decide about the placeholders — either delete the 24 fake destinations
   and 63 fake restaurants, or replace them with real places pulled per town
   from OSM via your existing `sync_osm_data` pipeline.

I'd suggest replacement over deletion for the restaurants, since an empty
restaurants section is worse for a demo than a smaller real one.

## One thing I want to be straight about

You asked me to make sure the whole project is "free from all the gaps and
errors". I can't certify that, and I'd be misleading you if I said I had. I
audited the data layer and the two problems you named. I have not reviewed
the views, templates, auth flows, or the OSM import path for correctness.

What I can tell you is that the errors you're hitting share one cause — a
database that doesn't match the code — and that the seed data was never
real. Both are fixable, and neither means your project is broadly buggy.
