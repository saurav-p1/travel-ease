from django.contrib import admin
from .models import LoginHistory, RecentView, SavedItem, UserPreference

@admin.register(LoginHistory)
class LoginHistoryAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'timestamp', 'ip_address']  # Changed 'login_time' to 'timestamp'
    list_filter = ['action', 'timestamp']  # Changed 'login_time' to 'timestamp'
    search_fields = ['user__username', 'user__email', 'ip_address']
    ordering = ['-timestamp']
    readonly_fields = ['timestamp']

@admin.register(RecentView)
class RecentViewAdmin(admin.ModelAdmin):
    list_display = ['user', 'item_type', 'item_title', 'viewed_at']
    list_filter = ['item_type', 'viewed_at']
    search_fields = ['user__username', 'user__email', 'item_title']
    ordering = ['-viewed_at']

@admin.register(SavedItem)
class SavedItemAdmin(admin.ModelAdmin):
    list_display = ['user', 'item_type', 'item_title', 'saved_at']
    list_filter = ['item_type', 'saved_at']
    search_fields = ['user__username', 'user__email', 'item_title']
    ordering = ['-saved_at']

@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'preferred_currency', 'travel_style', 'created_at']
    list_filter = ['preferred_currency', 'travel_style', 'profile_visibility']
    search_fields = ['user__username', 'user__email']
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Travel Preferences', {
            'fields': ('preferred_currency', 'preferred_language', 'travel_style')
        }),
        ('Notification Settings', {
            'fields': ('email_notifications', 'push_notifications', 'marketing_emails')
        }),
        ('Privacy Settings', {
            'fields': ('profile_visibility',)
        }),
    )
    readonly_fields = ['created_at', 'updated_at']