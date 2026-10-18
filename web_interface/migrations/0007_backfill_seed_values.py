from django.db import migrations
from django.db.models import F


def backfill_seed_values(apps, schema_editor):
    """Any restaurant that already had a rating/average_cost before this
    migration (dummy data, or an earlier CSV import) needs that value
    copied into seed_rating/seed_average_cost -- otherwise the first real
    user rating submitted for it would silently discard that existing
    value entirely (recompute_aggregates() only blends in a seed if one is
    set)."""
    PlaceToEat = apps.get_model('web_interface', 'PlaceToEat')
    PlaceToEat.objects.filter(rating__isnull=False, seed_rating__isnull=True).update(
        seed_rating=F('rating')
    )
    PlaceToEat.objects.filter(average_cost__isnull=False, seed_average_cost__isnull=True).update(
        seed_average_cost=F('average_cost')
    )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('web_interface', '0006_placetoeat_seed_average_cost_placetoeat_seed_rating'),
    ]

    operations = [
        migrations.RunPython(backfill_seed_values, reverse_noop),
    ]
