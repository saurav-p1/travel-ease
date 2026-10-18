# TravelEase data pipeline — runbook

## 0. Your source list: is it the practical answer?

Mostly yes, with three corrections.

**Correct as written.** OSM/Overpass for coordinates and venue discovery;
Wikidata (P625 coordinate, P18 image) for major destinations; Wikimedia
Commons for destination photos when identity and licence match; real OSM
venues only, no invented "Local Kitchen" replacements. That ordering is
right and this pipeline implements exactly it.

**Correction 1 — Nominatim.** You wrote it off as "not separate data". True,
but the sharper point is that it answers a different question. Nominatim
searches *by name*; it cannot find a place whose name was invented. That is
the entire reason 104 of 139 rows came back "no match". Overpass asks "what
is mapped near this point", which is the question you actually have.

**Correction 2 — Geonames.** It is populated places and physical features,
not businesses or POIs. It adds nothing here that OSM does not already have,
and its coordinates for a town centre are the ones you already hold in
`Location`. Skip it.

**Correction 3 — restaurant photos.** This is the one place your list is
optimistic. There is no free source that reliably ties a photo to a specific
small restaurant. OSM's `image=`/`wikimedia_commons=` tags exist on only a
small minority of venues; Commons has almost no coverage of individual
Nepali eateries; Mapillary/Panoramax are street-level imagery where the
venue is usually incidental and per-image licences must be checked one at a
time; Flickr/Unsplash/Pexels cannot prove a photo is of a particular
business. Google Places photos are not redistributable and require billing.

So: **"20 restaurants per town, each with a photo of that restaurant" is not
achievable from open data.** Coordinates, names, cuisine and hours are.
Photos will come back for a minority, and `summary.txt` reports the exact
number. The three honest ways to raise it are: tag the photo on the venue in
OSM yourself, let owners/users upload through the existing submit-a-place
form, or leave it blank. Attaching a stock or name-matched photo is what put
an Ncell logo on "Banepa Market".

## 1. The root causes

**"no match" on 104 of 139 places.** `backfill_coordinates` asked Nominatim
to find a place by name. "Local Bhatti", "Traditional Villages", "Truck Stop
Cafe" name nothing in any map database. The failures were correct behaviour
on impossible input.

**"HTTP 429 from Wikipedia" on nearly every line.** The User-Agent was
`TravelEase/1.0 (educational project; contact via project README)` — no URL,
no email. Wikimedia deployed global API rate limits in 2026 and sorts
non-compliant User-Agents into a restrictive anonymous-scraping tier. On top
of that the command made one request per destination when the Action API
accepts 50 titles per request. Every 429 was then logged as "left blank",
which reads as "Wikipedia has no photo of Boudhanath Stupa". It has one.

**Wrong photos.** `generator=search&gsrlimit=1` takes the top full-text hit,
which always exists. Nothing checked the article was about the place.

## 2. Every change, and why

| File | Change |
|---|---|
| `scripts/open_data.py` | **new** — paced, identified HTTP client with Retry-After/exponential backoff; Overpass, Wikidata, Wikipedia helpers; all verification rules |
| `.../commands/build_open_dataset.py` | **new** — harvests real places from OSM around each town, enriches from Wikidata/Commons, writes staging CSVs. Never writes to the database |
| `.../commands/audit_places.py` | **new** — read-only grade of every row: placeholder name / town-centre coordinate / verified vs unverified image |
| `.../commands/backfill_images.py` | rewritten — batched 50/request, backs off, accepts a photo only when the article's own coordinates prove identity. Adds `--clear-unverified` |
| `.../commands/backfill_coordinates.py` | patched — compliant User-Agent; name-similarity check so a nearby-but-unrelated result is no longer accepted (this is what gave "Highway Restaurant" a real coordinate) |
| `.../commands/sync_osm_data.py` | patched — form-encodes the Overpass body (the HTTP 406) and identifies the client |
| `web_interface/forms.py` | patched — the submit form told users to type "9 AM - 9 PM", which `views.is_open()` cannot parse, so every user-submitted restaurant was invisible to the "Open now" filter. Now validates `HH:MM-HH:MM` |
| `web_interface/tests.py` | one existing test updated (it posted the unparseable format) + 2 regression tests |
| `web_interface/tests_open_data.py` | **new** — 64 tests, one per failure mode, no DB or network |
| `.env.example` | documents `TRAVELEASE_CONTACT` |

No model change. No migration. Nothing in `views.py`, `urls.py`, `admin.py`,
`sitemaps.py` or any template was touched.

### Integration details that were easy to get wrong

* `business_hours` is `CharField(max_length=20)` and `is_open()` parses one
  `HH:MM-HH:MM`. Raw OSM `opening_hours` is a grammar. The harvester emits
  only values `is_open()` can read, converts `24/7` to `00:00-23:59`,
  flattens an all-week prefix, and drops multi-rule strings rather than
  flattening them — keeping `Mo-Fr 09:00-17:00` out of a string that also
  said `Sa 10:00-14:00` would tell a Saturday visitor the place is closed.
* Row values are checked against the model's column widths (name 255,
  cuisine 100, image URL 500) while staging, so an over-long OSM name shows
  up in `audit.csv` with a reason instead of as a silent skip at import.
* Overpass reports timeouts as HTTP 200 with a `remark` and zero elements.
  That is now raised and the next mirror tried, not read as "nothing mapped
  here".
* Verified images are stored as `Special:FilePath` URLs; every image the old
  command saved is on `upload.wikimedia.org`. Different hosts, so
  `audit_places` can actually decide whether a stored image was verified.
* The catchment-widening decision counts places mapped near *that* town, not
  places new to the shared pool — towns are harvested in order and their
  radii overlap.

## 3. Before you start

```bash
echo 'TRAVELEASE_CONTACT=https://github.com/<you>/travelease' >> .env

python manage.py dumpdata web_interface --indent 2 > backup_$(date +%F).json
cp db.sqlite3 db.sqlite3.bak

python manage.py test          # full suite, including the new files
```

## 4. Run order

```bash
# A. Record the current state.
python manage.py audit_places --csv audit_before.csv

# B. One town, writes nothing.
python manage.py build_open_dataset --dry-run --areas Pokhara

# C. Full harvest. Still no database writes. ~25-35 min.
python manage.py build_open_dataset --target 25

#    Read data_templates/open_dataset/summary.txt, then spot-check ten rows
#    in audit.csv against their OSM links.

# D. Preview the import.
python manage.py import_data \
  --locations    data_templates/open_dataset/locations.csv \
  --destinations data_templates/open_dataset/destinations.csv \
  --restaurants  data_templates/open_dataset/restaurants.csv \
  --dry-run

# E. Import. NEVER pass --clear.
python manage.py import_data \
  --locations    data_templates/open_dataset/locations.csv \
  --destinations data_templates/open_dataset/destinations.csv \
  --restaurants  data_templates/open_dataset/restaurants.csv

# F. Remove the wrong photos the old command saved.
python manage.py backfill_images --clear-unverified --dry-run
python manage.py backfill_images --clear-unverified

# G. Refill, verified this time.
python manage.py backfill_images --dry-run
python manage.py backfill_images

# H. Compare and re-test.
python manage.py audit_places --csv audit_after.csv
python manage.py test
```

`--clear` on `import_data` deletes every location and place including user
submissions and ratings. It is never part of this pipeline. The upsert is
keyed on `(name__iexact, location)`, so re-running preserves each row's id,
ratings, saved items, `submitted_by` and URLs.

## 5. Retiring the placeholders

The old seed rows survive the import — new data is added alongside, not on
top.

```bash
python manage.py audit_places --only-problems
```

Set `status='rejected'` on the flagged rows in the admin, or delete them.
**Check the `user_submitted` column first** — any row with a submitter is a
real contribution.

`add_nepal_wide_data.py` and `add_pokhara_data.py` can go once the new
dataset is imported and the placeholders are retired, not before.

## 6. What is verified, and what you must verify

Verified here, by test: every rule that decides whether a coordinate or a
photo is accepted, the batching, the 429/Retry-After behaviour, the Overpass
request shape, the nearest-town assignment, the `business_hours` contract
with `is_open()`, and the column-width guards. 64 tests, no network, no DB.
The staged rows were also run end-to-end through a copy of `import_data`'s
own validators.

**Not verifiable without running it:** the live harvest. No network reaches
the sandbox this was written in, so the actual counts per town, the real
Overpass responses, and how many restaurants come back with a photo are
things only your run can tell you. That is what `summary.txt` and
`audit.csv` exist for — read them before step E, not after.

## 7. Licensing

OSM data is ODbL: "© OpenStreetMap contributors" must appear wherever it is
shown. Your footer and terms page already carry it. Commons images each
carry their own licence; `audit.csv` records the exact file per image so
attribution can be generated from it. No Google Maps or Places data is used.
