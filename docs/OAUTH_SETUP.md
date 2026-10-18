# Google sign-in — fixing `Error 400: redirect_uri_mismatch`

## What the error means

Nothing is wrong with your code. This is a configuration mismatch in the
Google Cloud Console.

When you tap "Sign in with Google", django-allauth sends Google a
`redirect_uri` — the address Google should send the user back to after they
approve. allauth 65.x builds that from **the host of the incoming request**.
Google refuses the whole flow unless that exact string is on its allow-list.

- Browsing on your laptop at `http://127.0.0.1:8000` sends
  `http://127.0.0.1:8000/accounts/google/login/callback/`
- Browsing on your phone through ngrok at
  `https://haphazard-passage.ngrok-free.dev` sends
  `https://haphazard-passage.ngrok-free.dev/accounts/google/login/callback/`

Those are different strings. Your Console almost certainly only has the
first one, which is exactly why sign-in works on desktop and fails on
mobile.

Note the scheme is `https` for ngrok, not `http`. ngrok terminates TLS at
its edge and `SECURE_PROXY_SSL_HEADER` in `settings.py` tells Django to
trust the forwarded scheme, so Django correctly reports the request as
secure. Registering the `http://` variant will not work.

## The fix

1. Go to <https://console.cloud.google.com/apis/credentials>.
2. Pick the project holding your OAuth client, then click the OAuth 2.0
   Client ID that matches `GOOGLE_OAUTH_CLIENT_ID` in your `.env`.
3. Under **Authorized JavaScript origins**, make sure both are present:

   ```
   http://127.0.0.1:8000
   https://haphazard-passage.ngrok-free.dev
   ```

4. Under **Authorized redirect URIs**, make sure both are present:

   ```
   http://127.0.0.1:8000/accounts/google/login/callback/
   https://haphazard-passage.ngrok-free.dev/accounts/google/login/callback/
   ```

5. Save, then wait. Google's changes are not instant — allow a few minutes,
   and use a fresh private window so you aren't replaying a cached consent
   screen.

The trailing slash matters. So does `127.0.0.1` versus `localhost` — Google
treats them as different origins, so register whichever you actually type in
the address bar.

## When you deploy to Render

Add the production pair too:

```
https://<your-app>.onrender.com
https://<your-app>.onrender.com/accounts/google/login/callback/
```

## If your ngrok URL keeps changing

A random ngrok subdomain on every restart means re-editing the Console every
time, which is miserable. ngrok's free tier includes one **static domain** —
claim it at <https://dashboard.ngrok.com/domains> and start the tunnel with:

```bash
ngrok http 8000 --url=haphazard-passage.ngrok-free.dev
```

Register that one domain in Google once and you're done.

## Also update SITE_DOMAIN

Separate from OAuth, but it bites in the same situation. `SITE_DOMAIN` in
`.env` feeds the `django.contrib.sites` record used to build password-reset
and email-confirmation links. It is written by a migration that runs **once**
— editing `.env` afterwards does not update it, so those emails keep pointing
at the old host with no error anywhere.

After changing `SITE_DOMAIN`, run:

```bash
python manage.py sync_site_domain
```

This command was added as part of these fixes. It will not affect Google
sign-in — allauth builds the OAuth callback from the request host, not from
the Site record.

## Checklist when sign-in still fails

- The `redirect_uri` Google shows in **error details** — copy it verbatim and
  confirm it is character-for-character in the Console list.
- `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` in `.env` belong to
  the client you just edited.
- Your OAuth consent screen is in **Testing** mode with your Google account
  added under **Test users**, or it is Published.
- `.ngrok-free.dev` is in `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` — both
  already are in this project's `settings.py`.
