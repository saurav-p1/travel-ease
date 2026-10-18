import os
from django.db import migrations


def set_site_domain(apps, schema_editor):
    """
    django.contrib.sites ships a default Site record with domain
    'example.com'. PasswordResetView (accounts/urls.py) uses this domain
    to build the reset link in the email it sends -- left unfixed, every
    password-reset email would contain a dead http://example.com/... link.

    Reads SITE_DOMAIN from the environment (see .env.example) so this
    works for both local dev and a real deployment; defaults to the local
    dev server address.
    """
    Site = apps.get_model('sites', 'Site')
    domain = os.environ.get('SITE_DOMAIN', '127.0.0.1:8000')
    Site.objects.update_or_create(
        id=1,
        defaults={'domain': domain, 'name': 'TravelEase'},
    )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sites', '0002_alter_domain_unique'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(set_site_domain, reverse_noop),
    ]
