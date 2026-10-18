from django.urls import path
from . import views

app_name = 'web_interface'

urlpatterns = [
    path('', views.home, name='home'),
    path('places-to-eat/', views.places_to_eat, name='places_to_eat'),
    path('search-travel-options/', views.search_travel_options, name='search_travel_options'),
    path('saved-items/', views.saved_items, name='saved_items'),
    path('recent-views/', views.recent_views, name='recent_views'),
    path('travel-detail/<int:id>/', views.travel_detail, name='travel_detail'),
    path('restaurant-detail/<int:id>/', views.restaurant_detail, name='restaurant_detail'),
    path('restaurant-detail/<int:id>/rate/', views.submit_restaurant_rating, name='submit_restaurant_rating'),
    path('submit-place/', views.submit_place, name='submit_place'),
    path('toggle-save-item/', views.toggle_save_item, name='toggle_save_item'),
    path('remove-recent-view/', views.remove_recent_view, name='remove_recent_view'),
    path('clear-recent-views/', views.clear_recent_views, name='clear_recent_views'),
    path('terms/', views.terms_of_service, name='terms'),
    path('privacy/', views.privacy_policy, name='privacy'),
    path('contact/', views.contact, name='contact'),
    path('newsletter-subscribe/', views.newsletter_subscribe, name='newsletter_subscribe'),
]
