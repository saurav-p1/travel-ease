from django.db import models
from django.conf import settings
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver

class LoginHistory(models.Model):
    """Track user login/logout/signup history"""
    ACTION_CHOICES = [
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('signup', 'Signup'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='accounts_login_history'  # Changed to avoid conflict
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(default=timezone.now)
    user_agent = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name_plural = "Login histories"
    
    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} - {self.action} at {self.timestamp}"

class RecentView(models.Model):
    """Track recently viewed items/pages by users"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='accounts_recent_views'  # Changed to avoid conflict
    )
    item_type = models.CharField(max_length=50)  # Always 'destination' or 'restaurant' in practice -- see web_interface/views.py's RecentView.objects.update_or_create() calls
    item_id = models.PositiveIntegerField()
    item_title = models.CharField(max_length=200, blank=True, default='')
    item_url = models.URLField(blank=True, default='')
    viewed_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        ordering = ['-viewed_at']
        unique_together = ['user', 'item_type', 'item_id']
    
    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} viewed {self.item_title or self.item_type}"

class SavedItem(models.Model):
    """Items saved by users for later reference"""
    # FOUND DURING AUDIT: this used to also list 'hotel', 'activity', and
    # 'itinerary' as choices, but nothing in the app can ever create a
    # SavedItem with any of those -- web_interface.views.toggle_save_item
    # (the only place a SavedItem is ever created) explicitly rejects any
    # item_type other than 'destination'/'restaurant', and there's no
    # Hotel/Activity/Itinerary model anywhere in the codebase for those
    # values to even refer to. Trimmed to the two values the app actually
    # uses; add a real choice back here only alongside the model and
    # save-flow that would make it real.
    ITEM_TYPE_CHOICES = [
        ('destination', 'Destination'),
        ('restaurant', 'Restaurant'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='accounts_saved_items'  # Changed to avoid conflict
    )
    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES)
    item_id = models.PositiveIntegerField()
    item_title = models.CharField(max_length=200, blank=True, default='')
    item_url = models.URLField(blank=True, default='')
    item_image = models.URLField(
        blank=True, null=True, default='',
        help_text="Snapshot of the destination/restaurant's image URL at the "
                   "moment it was saved (same one-time-snapshot pattern as "
                   "item_title/item_url above, not a live link) -- FOUND "
                   "DURING AUDIT: this field existed but web_interface.views."
                   "toggle_save_item never actually wrote to it, so "
                   "saved_items.html had nothing to show and didn't even "
                   "try. Now populated on save; blank on anything saved "
                   "before that fix, same as it would be if the source item "
                   "simply had no photo yet."
    )
    notes = models.TextField(blank=True, default='')
    saved_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        ordering = ['-saved_at']
        unique_together = ['user', 'item_type', 'item_id']
    
    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} saved {self.item_title or self.item_type}"

class UserPreference(models.Model):
    """User preferences and settings"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='accounts_preferences'  # Changed to avoid conflict if needed
    )
    
    # Travel preferences
    preferred_currency = models.CharField(max_length=3, default='USD')
    preferred_language = models.CharField(max_length=10, default='en')
    travel_style = models.CharField(max_length=20, choices=[
        ('budget', 'Budget'),
        ('mid_range', 'Mid-range'),
        ('luxury', 'Luxury'),
        ('backpacker', 'Backpacker'),
        ('family', 'Family'),
        ('business', 'Business'),
    ], default='mid_range')
    
    # Notification preferences
    email_notifications = models.BooleanField(default=True)
    push_notifications = models.BooleanField(default=True)
    marketing_emails = models.BooleanField(default=False)
    
    # Privacy settings
    profile_visibility = models.CharField(max_length=10, choices=[
        ('public', 'Public'),
        ('private', 'Private'),
        ('friends', 'Friends Only'),
    ], default='private')
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email}'s preferences"

# Signal to create UserPreference when a new user is created
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_preference(sender, instance, created, **kwargs):
    """Automatically create UserPreference when a new user is created"""
    if created:
        UserPreference.objects.get_or_create(user=instance)