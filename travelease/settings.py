from pathlib import Path
import os
from dotenv import load_dotenv

# BASE DIRECTORY
BASE_DIR = Path(__file__).resolve().parent.parent

# Load variables from .env (if present) into os.environ.
load_dotenv(BASE_DIR / '.env')

# Create necessary directories
LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

STATIC_DIR = BASE_DIR / 'static'
STATIC_DIR.mkdir(exist_ok=True)

# SECURITY SETTINGS
# SECRET_KEY and the Google OAuth credentials live in .env (gitignored),
# never in this file. See .env.example for the variables you need to set.
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set. Copy .env.example to .env and fill it in."
    )

DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '0.0.0.0', 'testserver', '.onrender.com', '.ngrok-free.dev', '.ngrok.io']

# Django needs to explicitly trust HTTPS origins behind Render's proxy,
# or every POST (login, signup, search filters) 403s with a CSRF failure --
# Render terminates TLS at its edge, so Django itself sees plain HTTP
# unless told to trust the forwarded scheme (see SECURE_PROXY_SSL_HEADER
# below) and to trust this origin for CSRF specifically.
CSRF_TRUSTED_ORIGINS = ['https://*.onrender.com', 'https://*.ngrok-free.dev', 'https://*.ngrok.io']
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Only enforced when DEBUG=False (i.e. never locally) -- forces HTTP -> HTTPS
# and marks cookies HTTPS-only, which is also required for the Geolocation
# API to work at all for real users (browsers block it on insecure origins).
SECURE_SSL_REDIRECT = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# APPLICATIONS
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.sitemaps',

    # Third party apps
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'widget_tweaks',

    # Custom apps
    'signup',
    'web_interface',
    'accounts',
]

# MIDDLEWARE
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves static files in production (no nginx needed on Render)
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'travelease.urls'
WSGI_APPLICATION = 'travelease.wsgi.application'

# TEMPLATES
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
            BASE_DIR / 'signup' / 'templates',
            BASE_DIR / 'accounts' / 'templates',
            BASE_DIR / 'web_interface' / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'web_interface.context_processors.carto_api_key',
            ],
        },
    },
]

# DATABASE
# Local dev: SQLite (unchanged, still the default with no DATABASE_URL set).
# Production (Render): set DATABASE_URL to Render's free Postgres add-on --
# SQLite's data file lives on Render's ephemeral filesystem and is wiped on
# every deploy/restart, so it's only safe to use it locally.
import dj_database_url

DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}
if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES['default']['OPTIONS'] = {'timeout': 20}  # avoid "database is locked" under light concurrency

# CUSTOM USER MODEL
AUTH_USER_MODEL = 'signup.CustomUser'

# AUTHENTICATION
# EmailBackend (accounts/backends.py) lets users log in with either their
# email or username, case-insensitively. ModelBackend and allauth's backend
# stay in the list as fallbacks (e.g. for createsuperuser / admin / social
# login flows).
AUTHENTICATION_BACKENDS = [
    'accounts.backends.EmailBackend',
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# PASSWORD VALIDATORS
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# INTERNATIONALIZATION
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kathmandu'
USE_I18N = True
USE_TZ = True

# STATIC FILES
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]

# Compressed, cache-busted static files served directly by whitenoise --
# no separate nginx/CDN needed for a Render deploy.
# FOUND DURING AUDIT: unconditionally using whitenoise's manifest storage
# broke every local test/dev run that touches a {% static %} tag --
# CompressedManifestStaticFilesStorage requires a manifest file
# (staticfiles.json) that's only generated by `collectstatic`, which
# build.sh runs in production but nothing runs locally. 65 of 132 tests
# failed with "Missing staticfiles manifest entry" before this was gated
# by DEBUG. Local dev/test now uses Django's plain static storage (no
# manifest needed); production (DEBUG=False, after build.sh's
# collectstatic) gets the hashed/cached, whitenoise-served version.
if DEBUG:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
else:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# MEDIA FILES (user uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Password reset links: the email template tells users the link expires
# in 24 hours, but Django's own default (PASSWORD_RESET_TIMEOUT, 3 days)
# was never overridden to match, so links actually stayed valid for
# 3x longer than promised. Set it explicitly so the token's real
# lifetime matches what the email says -- shorter is also the more
# security-conscious choice for a password-reset link.
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24  # 24 hours, in seconds

# EMAIL CONFIGURATION
# FOUND DURING AUDIT: this was unconditionally the console backend, which
# only ever prints emails to the server's own log -- fine for local dev,
# but it meant password-reset/allauth confirmation emails would silently
# never reach a real user in production. They'd click "reset password",
# be told to check their inbox, and nothing would ever arrive -- with no
# error anywhere, since printing to a log "succeeds" from Django's
# perspective.
#
# Uses real SMTP once EMAIL_HOST is configured (via env vars, production);
# falls back to the console backend otherwise (local dev, or a production
# deploy that hasn't set up email yet) -- same "degrade gracefully rather
# than crash, document what still needs configuring" pattern already used
# for CARTO_API_KEY above. A misconfigured-but-present EMAIL_HOST will
# still surface a real SMTP error at send time, which is the honest
# behavior once you've told it to actually try.
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
if EMAIL_HOST:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
    EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'TravelEase <noreply@travelease.example>')

# SESSION SETTINGS
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 1209600
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True

# DJANGO-ALLAUTH CONFIGURATION
SITE_ID = 1

# django-allauth 65.x: ACCOUNT_EMAIL_REQUIRED / ACCOUNT_USERNAME_REQUIRED /
# ACCOUNT_AUTHENTICATION_METHOD are deprecated in favor of these two
# settings. Equivalent to the old config: sign up with email + password
# only (no username field), and log in by email.
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_EMAIL_VERIFICATION = 'optional'
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_USER_MODEL_EMAIL_FIELD = 'email'
ACCOUNT_ADAPTER = 'web_interface.adapters.CustomAccountAdapter'

ACCOUNT_RATE_LIMITS = {
    "login_failed": "5/5m",
}

# Redirect URLs
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/accounts/dashboard/'
LOGOUT_REDIRECT_URL = '/'
ACCOUNT_LOGOUT_ON_GET = True

# Social account settings. Credentials come from .env -- see .env.example.
# Skip allauth's own unstyled intermediate "Sign In Via Google / Continue"
# confirmation page and go straight to Google when the user clicks the
# "Sign in with Google" button -- that extra page previously had none of
# this site's styling (it's allauth's bare default template) and just
# added a redundant click.
SOCIALACCOUNT_LOGIN_ON_GET = True

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
        'APP': {
            'client_id': os.environ.get('GOOGLE_OAUTH_CLIENT_ID', ''),
            'secret': os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', ''),
        }
    }
}

# CARTO basemap tiles (used by the live location map on destination/
# restaurant pages -- see web_interface/templates/.../includes/
# location_map.html). Free, no credit card: https://carto.com/basemaps/apikey/
# As of Aug 2026 CARTO watermarks raster tile requests made *without* a
# key with a large "API KEY REQUIRED" stamp -- the map still works, but
# looks broken. Optional here on purpose: leaving this unset still
# renders a working (just watermarked) map rather than breaking the page,
# since a missing/invalid env var should never be able to take the map
# down entirely.
CARTO_API_KEY = os.environ.get('CARTO_API_KEY', '')

# Message tags (Bootstrap/Tailwind-style alert classes)
from django.contrib.messages import constants as messages
MESSAGE_TAGS = {
    messages.DEBUG: 'debug',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'danger',
}

# Cache configuration
if DEBUG:
    # Local dev/testing is always single-process (manage.py runserver and
    # manage.py test never run multiple workers), so LocMemCache's
    # per-process nature is a non-issue here -- and it avoids a real
    # problem DatabaseCache has specifically on SQLite: "database is
    # locked" errors under the test suite's write pattern, from SQLite's
    # strict single-writer locking (this doesn't happen on Postgres,
    # which production actually uses).
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'unique-snowflake',
        }
    }
else:
    CACHES = {
        'default': {
            # FOUND DURING AUDIT, then live-proven with a real 4-worker
            # gunicorn boot: LocMemCache is per-process. Every rate
            # limiter in this project (login, signup, the submission
            # feature) reads/increments a counter that only exists in
            # whichever single worker process happens to handle that
            # request -- with N workers, an attacker effectively gets up
            # to N times the intended attempt budget, spread
            # inconsistently across workers (confirmed live: 4 workers
            # took 14 attempts instead of 5 to first block, then let a
            # later attempt through again afterward). DatabaseCache is
            # backed by an actual table in the same Postgres database
            # every worker already shares, so the counter is genuinely
            # global regardless of worker count -- re-tested with the
            # exact same live attack afterward: correctly blocks at
            # attempt 6 every time, no flip-flopping. Table is created by
            # `python manage.py createcachetable` (see build.sh, which
            # runs it on every deploy).
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': 'travelease_cache_table',
        }
    }

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
