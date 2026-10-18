from django.db import migrations


class Migration(migrations.Migration):
    """
    Removes CustomUser.travel_preferences (captured at signup, choices like
    'adventure'/'luxury'/'budget'...) because it duplicates
    accounts.UserPreference.travel_style, which is the field that's actually
    read anywhere (accounts/profile.html). travel_preferences was written on
    signup and then never read again by any view or template - captured and
    immediately orphaned, same shape of bug as the web_interface/accounts
    duplicate models fixed in 0002_migrate_duplicate_models_into_accounts.
    UserPreference is auto-created with a sensible default ('mid_range') by
    the post_save signal in accounts/models.py, so no user-facing capability
    is lost by dropping this column.
    """

    dependencies = [
        ('signup', '0005_customuser_unique_lower_email'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='customuser',
            name='travel_preferences',
        ),
    ]
