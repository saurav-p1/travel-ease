import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_interface', '0007_backfill_seed_values'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='location',
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower('name'),
                name='unique_lower_location_name',
                violation_error_message='A location with this name already exists.',
            ),
        ),
    ]
