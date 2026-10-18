from django.db import migrations


def copy_data_forward(apps, schema_editor):
    """
    web_interface.LoginHistory/RecentView/SavedItem were an accidental
    duplicate of accounts.LoginHistory/RecentView/SavedItem. views.py has
    always read/written the accounts.* versions, so real user history was
    silently accumulating in these orphaned web_interface.* tables instead.
    Copy that data over before the duplicate models are removed in the next
    migration, so nothing is lost.
    """
    OldLoginHistory = apps.get_model('web_interface', 'LoginHistory')
    OldRecentView = apps.get_model('web_interface', 'RecentView')
    OldSavedItem = apps.get_model('web_interface', 'SavedItem')

    NewLoginHistory = apps.get_model('accounts', 'LoginHistory')
    NewRecentView = apps.get_model('accounts', 'RecentView')
    NewSavedItem = apps.get_model('accounts', 'SavedItem')

    # item_type vocabulary differs slightly between the two apps.
    ITEM_TYPE_MAP = {
        'destination': 'destination',
        'place_to_eat': 'restaurant',
        'location': 'destination',
    }

    for row in OldLoginHistory.objects.all().iterator():
        NewLoginHistory.objects.get_or_create(
            user_id=row.user_id,
            action='login' if row.action == 'login' else 'signup',
            timestamp=row.login_time,
            defaults={'ip_address': row.ip_address},
        )

    for row in OldRecentView.objects.all().iterator():
        NewRecentView.objects.get_or_create(
            user_id=row.user_id,
            item_type=ITEM_TYPE_MAP.get(row.item_type, row.item_type),
            item_id=row.item_id or 0,
            defaults={'viewed_at': row.viewed_at},
        )

    for row in OldSavedItem.objects.all().iterator():
        NewSavedItem.objects.get_or_create(
            user_id=row.user_id,
            item_type=ITEM_TYPE_MAP.get(row.item_type, row.item_type),
            item_id=row.item_id or 0,
            defaults={'saved_at': row.saved_at},
        )


def copy_data_backward(apps, schema_editor):
    # Irreversible on purpose -- this is a one-way cleanup migration.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('web_interface', '0001_initial'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(copy_data_forward, copy_data_backward),
    ]
