# signup/urls.py
from django.urls import path
from . import views

app_name = 'signup'

urlpatterns = [
    path('', views.SignupView.as_view(), name='signup'),
    path('ajax/', views.ajax_signup, name='ajax_signup'),
    path('success/', views.signup_success, name='success'),
    path('check-email/', views.check_email_availability, name='check_email'),
    path('csrf-token/', views.get_csrf_token, name='csrf_token'),
]