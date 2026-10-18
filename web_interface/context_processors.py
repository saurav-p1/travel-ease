from django.conf import settings


def carto_api_key(request):
    """Makes settings.CARTO_API_KEY available to every template as
    `carto_api_key`, so the live-location-map partial
    (includes/location_map.html) always has it without every view that
    happens to include that partial needing to remember to pass it
    through its own context individually. Empty string if unset --
    the map partial already treats that as "no key" and falls back to
    watermarked (but working) tiles rather than breaking."""
    return {'carto_api_key': settings.CARTO_API_KEY}
