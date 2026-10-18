from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.views.generic import TemplateView
from web_interface.sitemaps import StaticViewSitemap, DestinationSitemap, PlaceToEatSitemap

sitemaps = {
    'static': StaticViewSitemap,
    'destinations': DestinationSitemap,
    'restaurants': PlaceToEatSitemap,
}

urlpatterns = [
    path('admin/', admin.site.urls),

    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain'), name='robots_txt'),

    # Main web interface
    path('', include('web_interface.urls')),
    
    # Custom signup and accounts
    path('signup/', include('signup.urls')),
    path('accounts/', include('accounts.urls')),
    
    # Allauth URLs - FIXED: Changed back to 'accounts/' to match OAuth callback
    path('accounts/', include('allauth.urls')),
]

# Serve static files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)