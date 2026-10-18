"""
Re-point django.contrib.sites' Site record at the current SITE_DOMAIN.

WHY THIS EXISTS

accounts/migrations/0002_fix_site_domain.py reads SITE_DOMAIN from the
environment and writes it into Site id=1, which correctly stops
password-reset emails going out with allauth/Django's default
`example.com` links.

But a migration runs exactly once. Change SITE_DOMAIN in .env afterwards --
which happens every time you move between the local dev server, an ngrok
tunnel and a Render deployment -- and the migration does not re-run, so the
Site record silently keeps the old value. Password-reset and email
confirmation links then point at a host the recipient cannot reach, and
nothing anywhere reports an error: the mail sends successfully, it just
contains a dead link.

This command makes that fixable without faking migrations backwards.

NOTE ON GOOGLE OAUTH

This does NOT affect Google sign-in. django-allauth 65.x builds the OAuth
`redirect_uri` from the incoming request's own host, not from the Site
record, so changing this will not resolve a `redirect_uri_mismatch`. That
error is fixed in the Google Cloud Console -- see docs/OAUTH_SETUP.md.

USAGE

    python manage.py sync_site_domain                  # use SITE_DOMAIN from env
    python manage.py sync_site_domain --domain x.dev   # override explicitly
    python manage.py sync_site_domain --dry-run
"""
import os

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Update the django.contrib.sites Site record to match SITE_DOMAIN."

    def add_arguments(self, parser):
        parser.add_argument(
            '--domain', default=None,
            help='Domain to set (default: the SITE_DOMAIN environment variable).')
        parser.add_argument(
            '--name', default='TravelEase',
            help="Human-readable site name (default: 'TravelEase').")
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Report what would change but don't save.")

    def handle(self, *args, **options):
        domain = options['domain'] or os.environ.get('SITE_DOMAIN')
        if not domain:
            raise CommandError(
                'No domain given and SITE_DOMAIN is not set in the environment. '
                'Pass --domain explicitly or set SITE_DOMAIN in .env.'
            )

        # A scheme here is a common and silent mistake: Django stores a bare
        # host and builds "http://" + domain itself, so "https://example.dev"
        # would produce "http://https://example.dev/..." in every email.
        for prefix in ('http://', 'https://'):
            if domain.startswith(prefix):
                raise CommandError(
                    f"SITE_DOMAIN should be a bare host without a scheme -- "
                    f"got {domain!r}. Use {domain[len(prefix):]!r} instead."
                )
        if domain.endswith('/'):
            raise CommandError(
                f"SITE_DOMAIN should not end with a slash -- got {domain!r}."
            )

        site_id = getattr(settings, 'SITE_ID', 1)
        try:
            site = Site.objects.get(id=site_id)
        except Site.DoesNotExist:
            site = None

        if site is None:
            self.stdout.write(f'No Site with id={site_id} exists; it will be created.')
            old = '(none)'
        else:
            old = site.domain
            if old == domain and site.name == options['name']:
                self.stdout.write(self.style.SUCCESS(
                    f'Site id={site_id} already set to {domain!r} -- nothing to do.'))
                return

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN -- would change Site id={site_id} domain from {old!r} to {domain!r}.'))
            return

        Site.objects.update_or_create(
            id=site_id,
            defaults={'domain': domain, 'name': options['name']},
        )
        self.stdout.write(self.style.SUCCESS(
            f'Site id={site_id} domain updated: {old!r} -> {domain!r}'))
        self.stdout.write(
            'Password-reset and email-confirmation links will now use this host.')
