from django.db import migrations


def backfill_marketing_emails(apps, schema_editor):
    """CustomUser.marketing_emails (about to be removed) was set once at
    signup and never actually honored anywhere -- accounts.UserPreference.
    marketing_emails is the field every real code path reads/writes, but it
    was always auto-created with its own independent default (False),
    completely ignoring what the person picked at signup.

    Backfill is intentionally one-directional: only flip
    UserPreference.marketing_emails to True where the old signup-time field
    was True and the preference is still at its untouched False default.
    A user who *has* since visited the real preferences page already has
    their actual, more recent intent recorded there -- overwriting it with
    stale signup-time data (in either direction) would silently discard a
    real choice. Someone who signed up unchecked and never visited
    preferences is unaffected either way, since both fields already agree
    (False)."""
    CustomUser = apps.get_model('signup', 'CustomUser')
    UserPreference = apps.get_model('accounts', 'UserPreference')

    for user in CustomUser.objects.filter(marketing_emails=True).iterator():
        prefs, _ = UserPreference.objects.get_or_create(user=user)
        if not prefs.marketing_emails:
            prefs.marketing_emails = True
            prefs.save(update_fields=['marketing_emails'])


def reverse_noop(apps, schema_editor):
    # Irreversible on purpose -- the column is being dropped, and the
    # backfill direction (signup-time snapshot -> live preference) has no
    # meaningful inverse once that snapshot no longer exists.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('signup', '0007_alter_customuser_country_code'),
        ('accounts', '0003_alter_saveditem_item_type'),
    ]

    operations = [
        migrations.RunPython(backfill_marketing_emails, reverse_noop),
        migrations.RemoveField(
            model_name='customuser',
            name='marketing_emails',
        ),
    ]
