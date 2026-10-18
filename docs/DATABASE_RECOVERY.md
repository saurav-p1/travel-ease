# TravelEase — Django admin recovery

## What's actually wrong

Your admin code is fine. `admin/` is wired in `travelease/urls.py`, and both
`Destination` and `PlaceToEat` are registered in `web_interface/admin.py`
with working **"Approve selected"** / **"Reject selected"** actions and a
`status` list filter.

The problem is the database.

`settings.py` sets `AUTH_USER_MODEL = 'signup.CustomUser'`, but in
`db.sqlite3`:

| Check | Result |
|---|---|
| `signup_customuser` table exists | **No** |
| `auth_user` table exists | Yes, 2 rows |
| `django_migrations` lists all 8 `signup` migrations as applied | Yes |
| `accounts_userpreference.user_id` references | `auth_user` |
| `accounts_saveditem.user_id` references | `auth_user` |
| `accounts_recentview.user_id` references | `auth_user` |
| `web_interface_destination.submitted_by_id` references | `auth_user` |
| `web_interface_placetoeat.submitted_by_id` references | `auth_user` |
| `web_interface_restaurantrating.user_id` references | `auth_user` |

Every user foreign key in the database points at `auth_user`. This database
was built entirely against Django's **default** user model. At some point
`AUTH_USER_MODEL` was switched to `signup.CustomUser` and the migrations
were marked applied without actually running (a `migrate --fake`).

So when Django serves `/admin/`, it tries to load the session user from
`signup_customuser`, which does not exist, and the request dies with
`OperationalError: no such table: signup_customuser`.

Because `django_migrations` already claims those migrations are applied,
`python manage.py migrate` reports "No migrations to apply" and changes
nothing. That's why it looks unfixable.

## Why a rebuild, not a patch

Recreating just the missing table would leave every `accounts_*` and
`web_interface_*` foreign key still pointing at `auth_user`. The schema
would stay permanently out of sync with the code and the next migration you
write would fail.

A rebuild is cheap here because almost nothing user-owned exists:

- 2 users, both test accounts (`reject-staff@example.com`,
  `adamsaurav9@gmail.com`)
- 2 user preferences, 1 saved item, 3 recent views
- **0** restaurant ratings
- **0** destinations or restaurants with a `submitted_by` set

What you actually care about — 24 locations, 76 destinations, 63
restaurants — has no user references at all, so it survives intact in
`places_backup.json`.

## Steps

Run from the project root, with your virtualenv active.

```bash
# 1. Back up the broken database. Do not skip this.
cp db.sqlite3 db.sqlite3.broken-backup

# 2. Remove it so Django builds a clean schema.
rm db.sqlite3

# 3. Build the schema properly. This time signup_customuser is really created.
python manage.py migrate

# 4. Restore your 163 locations / destinations / restaurants.
python manage.py loaddata places_backup.json

# 5. Create your admin account.
python manage.py createsuperuser

# 6. Run it.
python manage.py runserver
```

Then open <http://127.0.0.1:8000/admin/> and log in.

## Approving submissions

Once in the admin:

1. Go to **Web_Interface → Destinations** (or **Place to eats** for
   restaurants).
2. In the right-hand **Filter** panel, click **status → Pending review**.
3. Tick the rows you want.
4. In the **Action** dropdown choose **"Approve selected (make publicly
   visible)"** and click **Go**.

Approved items become publicly visible; pending and rejected ones stay
hidden from public listings but remain visible to the submitter and to
staff.

Note: right now there are **no pending rows** in your database — all 139
places are `approved` seed data. New submissions through `/submit-place/`
are saved with `status='pending'` (see `web_interface/views.py`,
`submit_place`), so they'll appear under that filter once people submit
them. You will also need a logged-in account to submit, since the view
sets `place.submitted_by = request.user`.

## Verifying the fix

```bash
python manage.py shell -c "
from django.db import connection
print('signup_customuser' in connection.introspection.table_names())
"
```

Should print `True`.

## Avoiding a repeat

The trigger for this class of breakage is switching `AUTH_USER_MODEL` after
the first `migrate`. Django cannot migrate an existing project onto a custom
user model automatically, and `--fake` hides the failure instead of solving
it.

`db.sqlite3` is already gitignored (`.gitignore` line 18), so this broken
state was local to your machine and hasn't spread to teammates. Worth
committing `places_backup.json` as a fixture, though, so
`migrate && loaddata` becomes the standard way to stand up a fresh dev
environment — that path exercises the real migration chain every time, which
is exactly what would have caught this.
