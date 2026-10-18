from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Destination, PlaceToEat


class StaticViewSitemap(Sitemap):
    """The site's fixed, non-database-backed pages."""
    changefreq = 'monthly'

    def items(self):
        return [
            'web_interface:home',
            'web_interface:search_travel_options',
            'web_interface:places_to_eat',
            'web_interface:terms',
            'web_interface:privacy',
            'web_interface:contact',
        ]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 1.0 if item == 'web_interface:home' else 0.5


class DestinationSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        # Only approved items -- a pending/rejected user submission's
        # detail page now 404s for anyone but its submitter or staff
        # (see travel_detail's access check in views.py), so listing it
        # here would hand search engines a dead link.
        return Destination.objects.filter(status='approved').order_by('id')

    def location(self, obj):
        return reverse('web_interface:travel_detail', args=[obj.id])


class PlaceToEatSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return PlaceToEat.objects.filter(status='approved').order_by('id')

    def location(self, obj):
        return reverse('web_interface:restaurant_detail', args=[obj.id])
