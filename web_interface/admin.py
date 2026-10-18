from django.contrib import admin
from django.contrib.auth import get_user_model
from .models import Location, Destination, PlaceToEat, RestaurantRating

User = get_user_model()

@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'latitude', 'longitude')
    search_fields = ('name',)
    ordering = ('name',)

@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    list_display = ('name', 'location', 'status', 'submitted_by', 'latitude', 'longitude', 'has_image')
    search_fields = ('name', 'location__name', 'submitted_by__email')
    list_filter = ('status', 'location')
    raw_id_fields = ('location', 'submitted_by')
    ordering = ('name',)
    actions = ['approve_selected', 'reject_selected']

    @admin.display(boolean=True, description='Has image')
    def has_image(self, obj):
        return bool(obj.image)

    @admin.action(description='Approve selected (make publicly visible)')
    def approve_selected(self, request, queryset):
        updated = queryset.update(status='approved')
        self.message_user(request, f'Approved {updated} destination(s).')

    @admin.action(description='Reject selected (keep hidden from public listings)')
    def reject_selected(self, request, queryset):
        updated = queryset.update(status='rejected')
        self.message_user(request, f'Rejected {updated} destination(s).')

@admin.register(PlaceToEat)
class PlaceToEatAdmin(admin.ModelAdmin):
    list_display = ('name', 'location', 'status', 'submitted_by', 'cuisine', 'average_cost', 'rating',
                     'seed_rating', 'seed_average_cost', 'latitude', 'longitude')
    search_fields = ('name', 'cuisine', 'location__name', 'submitted_by__email')
    list_filter = ('status', 'cuisine', 'rating', 'location')
    raw_id_fields = ('location', 'submitted_by')
    ordering = ('name',)
    readonly_fields = ('rating', 'average_cost')
    fields = ('location', 'name', 'cuisine', 'price_range', 'business_hours',
              'latitude', 'longitude', 'image', 'status', 'submitted_by',
              'seed_rating', 'seed_average_cost', 'rating', 'average_cost')
    actions = ['recompute_ratings', 'approve_selected', 'reject_selected']

    @admin.action(description='Approve selected (make publicly visible)')
    def approve_selected(self, request, queryset):
        updated = queryset.update(status='approved')
        self.message_user(request, f'Approved {updated} restaurant(s).')

    @admin.action(description='Reject selected (keep hidden from public listings)')
    def reject_selected(self, request, queryset):
        updated = queryset.update(status='rejected')
        self.message_user(request, f'Rejected {updated} restaurant(s).')

    def save_model(self, request, obj, form, change):
        # seed_rating/seed_average_cost are editable right above the
        # (read-only) rating/average_cost fields on the same form -- without
        # this, saving a seed change here would leave the displayed
        # rating/average_cost silently out of sync until someone remembered
        # to run the "Recompute ratings" action separately.
        super().save_model(request, obj, form, change)
        obj.recompute_aggregates()

    @admin.action(description='Recompute rating/average_cost from seed + user submissions')
    def recompute_ratings(self, request, queryset):
        for restaurant in queryset:
            restaurant.recompute_aggregates()
        self.message_user(request, f'Recomputed ratings for {queryset.count()} restaurant(s).')

@admin.register(RestaurantRating)
class RestaurantRatingAdmin(admin.ModelAdmin):
    list_display = ('place', 'user', 'rating', 'average_cost', 'submitted_at')
    search_fields = ('place__name', 'user__email')
    list_filter = ('rating', 'submitted_at')
    raw_id_fields = ('place', 'user')
    ordering = ('-submitted_at',)

    def save_model(self, request, obj, form, change):
        # The user-facing rating flow (views.submit_restaurant_rating) calls
        # restaurant.recompute_aggregates() itself right after saving a
        # rating; a rating created or edited directly in the admin bypassed
        # that entirely, leaving the restaurant's cached rating/average_cost
        # stale until an admin separately opened the restaurant and ran its
        # "Recompute ratings" action. Deletion is already covered by the
        # post_delete signal on RestaurantRating in models.py -- this is the
        # matching piece for create/update.
        super().save_model(request, obj, form, change)
        obj.place.recompute_aggregates()
