# Start here

This zip contains the fixes from our chat. **One manual step is required
before anything runs** — the database in the original zip did not match the
code, which is what broke the admin and every management command.

```bash
python manage.py migrate
python manage.py loaddata places_backup.json
python manage.py createsuperuser
python manage.py runserver
```

The old `db.sqlite3` has been removed from this zip on purpose; `migrate`
builds a correct one. Your 162 locations/destinations/restaurants are
preserved in `places_backup.json`.

Then read, in order:

| Doc | What it covers |
|---|---|
| `docs/CHANGES.md` | Every fix applied, and what's still open |
| `docs/DATABASE_RECOVERY.md` | Why the database was broken, in detail |
| `docs/OAUTH_SETUP.md` | Fixing Google sign-in on mobile |
| `docs/DATA_AUDIT.md` | Which seed records are real vs fictional |

Note: `.env` is included unchanged and contains your live Google OAuth
secret. Your own comment in that file says it was committed to source
control once before — rotate it when you get a chance.
