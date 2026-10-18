# Changes applied

Everything fixed across this conversation, and what remains open.

## 1. Database schema did not match the code — *blocking everything*

`AUTH_USER_MODEL = 'signup.CustomUser'`, but `db.sqlite3` had no
`signup_customuser` table. It had `auth_user` instead, and every user
foreign key in the database (`accounts_userpreference`,
`accounts_saveditem`, `accounts_recentview`,
`web_interface_destination.submitted_by_id`,
`web_interface_placetoeat.submitted_by_id`,
`web_interface_restaurantrating.user_id`) pointed at `auth_user`.

The database was built against Django's default user model, then
`AUTH_USER_MODEL` was switched and the migrations were marked applied
without running. `django_migrations` lists all 8 `signup` migrations as
applied, so `manage.py migrate` reports "No migrations to apply" and
changes nothing.

This caused **both** reported symptoms: `/admin/` was unreachable, and every
management command (`backfill_coordinates`, `backfill_images`,
`sync_osm_data`) died on import.

Fix: rebuild the database. `places_backup.json` (included, 162 records)
preserves all locations, destinations and restaurants. Full procedure in
`docs/DATABASE_RECOVERY.md`.

**This is the one thing you must do by hand.** Nothing else works until it's
done.

## 2. Mobile header replaced by a stray "Settings" row

`web_interface/templates/web_interface/base.html`

The mobile nav panel was `top-20` + `-translate-y-full`. `translateY(-100%)`
shifts an element by its own height, so the panel's bottom edge always landed
back at the 80px `top-20` offset regardless of how tall it was — meaning its
last item sat exactly on top of the header. At `z-40` against the header's
`z-30`, it painted over it. The last item is "Settings", which is what you saw.

Fixed by dropping the panel to `z-20` and adding `invisible` to the closed
state, toggled in JS around the transition so the slide animation still plays.

## 3. "Add a place" missing from the mobile bottom nav

`base.html`

The desktop sidebar had it; the five-item mobile bottom bar did not, leaving
`/submit-place/` reachable on mobile only through the hamburger menu. Added as
a sixth item between Food and Saved, with item padding tightened from `px-3`
to `px-2` so six fit on a 360px screen.

## 4. Bottom ~70px of every mobile page hidden behind the nav

`base.html`

The bottom nav is `fixed`, so it reserves no space in normal flow, and
nothing compensated. The homepage's "Use My Location" button was clipped in
half — visible in the screenshot. Added a `max-width: 767.98px` rule giving
`body` a `padding-bottom` of `4.5rem` plus the device safe-area inset,
dropped at `md` where the bottom nav is replaced by the sidebar.

## 5. Rating slider overlapping "Open now" on desktop

`search_results.html`, `home.html`

Tailwind's `grid-cols-N` compiles to `minmax(0, 1fr)`, so a track can shrink
below its content and the overflow spills over the neighbouring cell. The
range input's intrinsic width plus the `whitespace-nowrap` label exceeded the
track, so the rating box overlapped the "Open now" box.

Fixed by adding `min-w-0` to both filter containers and to the range input —
`flex-1` alone can't shrink a flex child past its intrinsic minimum — plus
`shrink-0` on the rating value readout and the checkbox so they stay intact.

## 6. Google sign-in fails on mobile (`Error 400: redirect_uri_mismatch`)

**Not a code bug.** allauth builds the OAuth `redirect_uri` from the incoming
request's host. Desktop sends `http://127.0.0.1:8000/...`; your phone via
ngrok sends `https://haphazard-passage.ngrok-free.dev/...`. Only the first is
registered in Google Cloud Console, so Google rejects the second.

Fixed in the Console, not in this repo — exact values and steps in
`docs/OAUTH_SETUP.md`. `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` already
cover `.ngrok-free.dev`, so no settings change was needed.

## 7. `SITE_DOMAIN` could not be changed after first migrate — *latent*

Found while investigating the OAuth issue; you hadn't reported it.

`accounts/migrations/0002_fix_site_domain.py` reads `SITE_DOMAIN` from the
environment into the Site record, which correctly stops password-reset emails
containing `example.com` links. But a migration runs once. Editing `.env`
afterwards — which happens every time you move between localhost, ngrok and
Render — leaves the Site record stale, and reset emails then carry a dead
link with no error reported anywhere.

Added `web_interface/management/commands/sync_site_domain.py`. Run
`python manage.py sync_site_domain` after changing `SITE_DOMAIN`. It rejects
a domain with a scheme or trailing slash, both of which corrupt generated
links silently.

## 8. Chandannath Temple listed under the wrong district

Destination id 74, "Chandannath Temple, Surkhet". The temple is in **Jumla**
(verified: 29.274203, 82.183579), and id 77 already has it correctly. Id 74
has been removed from `places_backup.json`, so a rebuild simply won't
recreate it.

---

# Still open — these need your decision

## Coordinates and images are empty, and the tooling to fill them already exists

All 76 destinations and 63 restaurants have `latitude`, `longitude` and
`image` set to `NULL`. Your repo already contains `backfill_coordinates.py`
(OSM Nominatim, with a 25km sanity radius), `backfill_images.py` (Wikipedia
pageimages) and `sync_osm_data.py`. They are well built and have never run,
for the reason in item 1.

After rebuilding the database:

```bash
python manage.py backfill_coordinates --dry-run
python manage.py backfill_coordinates
python manage.py backfill_images
```

## Much of the seed data is fictional — a geocoder cannot fix this

Verified against Google Places: "District Kitchen" in Kavre matched a
restaurant in Austin, Texas. "Local Bhatti" in Banepa, "Truck Stop Cafe" in
Khopasi and "Juju Dhau House" in Bhaktapur do not exist.

- **All 63 restaurants** are placeholder names and will not geocode. This is
  why "Get directions" routes every restaurant in a town to the same pin.
- **24 of 76 destinations** are placeholders ("Local Temples", "Traditional
  Villages", "Old Pokhara Bazaar" …). Full list in `docs/DATA_AUDIT.md`.
- **51 destinations are real** and should geocode cleanly.

Either delete the placeholders or replace them with real places pulled per
town through `sync_osm_data`. For restaurants I'd suggest replacing rather
than deleting — an empty Food section is worse for a demo than a smaller real
one.

## Restaurant photos

Google Maps photos are not a legal option: Google's terms require them to be
fetched through the Places Photo API with attribution and forbid storing or
hotlinking the URLs, which is exactly what a bare `URLField` does. Your
model's own docstring already reached this conclusion.

Realistic paths: OSM `image` tags via `sync_osm_data`, or user-submitted
photos through the `/submit-place/` queue you already built.
