from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_interface', '0008_location_unique_lower_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='destination',
            name='image',
            field=models.URLField(
                blank=True, max_length=500, null=True,
                help_text="Photo URL. Populated from an OpenStreetMap 'image' tag when the "
                          "source data has one, or by 'python manage.py backfill_images' "
                          "(Wikipedia/Wikimedia Commons lookup by name) -- never a generic "
                          "stock photo. Left blank rather than guessed when no real photo "
                          "of this specific place could be found.",
            ),
        ),
        migrations.AddField(
            model_name='placetoeat',
            name='image',
            field=models.URLField(
                blank=True, max_length=500, null=True,
                help_text="Photo URL. Populated from an OpenStreetMap 'image' tag when the "
                          "source data has one -- restaurants are deliberately not run "
                          "through the Wikipedia lookup backfill_images uses for "
                          "destinations, since a small local business is very unlikely to "
                          "have its own Wikipedia article and a name-based search would "
                          "risk attaching an unrelated place's photo.",
            ),
        ),
    ]
