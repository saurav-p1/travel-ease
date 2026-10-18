from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models.functions import Lower
from django.db.models.signals import post_delete
from django.dispatch import receiver

# Shared by Destination and PlaceToEat below. 'approved' is the default so
# every pre-existing row (all seed/import data, which has no submitter)
# keeps showing up everywhere exactly as before -- only a row explicitly
# created through the user-submission flow (views.submit_place) is ever
# given 'pending'. Search results and homepage listings only ever show
# 'approved' rows (see search_travel_options/home in views.py); a
# 'pending'/'rejected' item's own detail page is still reachable, but only
# by its submitter or staff (see travel_detail/restaurant_detail) -- never
# publicly listed, but not a dead link for the person who submitted it
# either, so they can see what they submitted and its current status.
SUBMISSION_STATUS_CHOICES = [
    ('approved', 'Approved'),
    ('pending', 'Pending review'),
    ('rejected', 'Rejected'),
]

# Location Model
class Location(models.Model):
    """
    Represents a geographical location with coordinates and property count.
    """
    name = models.CharField(max_length=255, help_text="Name of the location")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, help_text="Latitude coordinate")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, help_text="Longitude coordinate")
    property_count = models.IntegerField(default=0, help_text="Number of properties in this location")

    class Meta:
        constraints = [
            # Every seed/import code path (populate_data.py, add_pokhara_
            # data.py, add_nepal_wide_data.py, and import_data's use of
            # name__iexact) is individually careful to look up an existing
            # Location case-insensitively before creating a new one -- but
            # nothing was actually enforcing that at the database level.
            # Nothing stopped a second "pokhara" from being created
            # alongside an existing "Pokhara" if any path were ever less
            # careful (or run concurrently). This closes that gap the same
            # way CustomUser.email's case-insensitive uniqueness already
            # is (signup.models.CustomUser.Meta.constraints).
            models.UniqueConstraint(
                Lower('name'), name='unique_lower_location_name',
                violation_error_message='A location with this name already exists.',
            ),
        ]

    def __str__(self):
        return self.name

# Destination Model
class Destination(models.Model):
    """
    Represents a travel destination within a location.
    """
    location = models.ForeignKey(Location, on_delete=models.CASCADE, help_text="The location this destination belongs to")
    name = models.CharField(max_length=255, help_text="Name of the destination")
    description = models.TextField(help_text="Description of the destination")
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="This specific destination's own coordinate -- e.g. the exact "
                   "OSM node/way position, or a geocoded point from "
                   "'python manage.py backfill_coordinates'. Left blank on data "
                   "that predates this field; effective_latitude falls back to "
                   "the parent Location's centroid in that case so nothing 404s "
                   "or crashes, but every destination in the same town/city no "
                   "longer has to share one identical point on the map."
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="This specific destination's own coordinate. See 'latitude'."
    )
    image = models.URLField(
        max_length=500, blank=True, null=True,
        help_text="Photo URL. Populated from an OpenStreetMap 'image' tag when the "
                   "source data has one, or by 'python manage.py backfill_images' "
                   "(Wikipedia/Wikimedia Commons lookup by name) -- never a generic "
                   "stock photo. Left blank rather than guessed when no real photo "
                   "of this specific place could be found."
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='submitted_destinations',
        help_text="Null for seed/imported data. Set only for destinations created "
                   "through the user-facing submission form (views.submit_place)."
    )
    status = models.CharField(
        max_length=10, choices=SUBMISSION_STATUS_CHOICES, default='approved',
        help_text="'approved' is the default so all pre-existing data is unaffected. "
                   "User submissions are created as 'pending' and only ever appear "
                   "in search/homepage listings once a staff member approves them."
    )

    def __str__(self):
        return self.name

    @property
    def effective_latitude(self):
        """This destination's own coordinate if it has one, otherwise the
        parent Location's centroid. Use this (never `.latitude` directly)
        anywhere a coordinate is needed for display/directions -- see the
        docstring on `latitude` above for why the fallback exists."""
        return self.latitude if self.latitude is not None else self.location.latitude

    @property
    def effective_longitude(self):
        return self.longitude if self.longitude is not None else self.location.longitude

# PlaceToEat Model
class PlaceToEat(models.Model):
    """
    Represents a dining location within a location.
    """
    location = models.ForeignKey(Location, on_delete=models.CASCADE, help_text="The location this place to eat belongs to")
    name = models.CharField(max_length=255, help_text="Name of the place to eat")
    cuisine = models.CharField(max_length=100, help_text="Type of cuisine offered")
    average_cost = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        help_text="Average cost per person (may be unknown for real-world data sources)"
    )
    rating = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True,
        help_text="Rating out of 5 (may be unknown for real-world data sources)"
    )
    price_range = models.IntegerField(
        choices=[(1, '$'), (2, '$$'), (3, '$$$'), (4, '$$$$')],
        default=1,
        help_text="Price range category"
    )
    business_hours = models.CharField(max_length=20, blank=True, help_text="Operating hours, e.g., '9 AM - 9 PM'")
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="This specific restaurant's own coordinate -- see "
                   "Destination.latitude's help text for why this exists and "
                   "how it's filled in."
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="This specific restaurant's own coordinate. See 'latitude'."
    )
    image = models.URLField(
        max_length=500, blank=True, null=True,
        help_text="Photo URL. Populated from an OpenStreetMap 'image' tag when the "
                   "source data has one -- restaurants are deliberately not run "
                   "through the Wikipedia lookup backfill_images uses for "
                   "destinations, since a small local business is very unlikely to "
                   "have its own Wikipedia article and a name-based search would "
                   "risk attaching an unrelated place's photo."
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='submitted_restaurants',
        help_text="Null for seed/imported data. Set only for restaurants created "
                   "through the user-facing submission form (views.submit_place)."
    )
    status = models.CharField(
        max_length=10, choices=SUBMISSION_STATUS_CHOICES, default='approved',
        help_text="See Destination.status's help text -- same purpose, same default."
    )

    # A starting rating/cost from somewhere other than TravelEase's own
    # users -- e.g. set via `import_data` from a CSV column, or typed in
    # directly by an admin. Kept separate from `rating`/`average_cost` (the
    # displayed, computed values) so it survives real user submissions
    # instead of being wiped out by the first one. See
    # recompute_aggregates() -- the seed counts as one permanent vote
    # alongside real users' ratings, rather than being discarded the
    # moment a real rating comes in.
    seed_rating = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="Optional starting rating from an external source (e.g. imported data), blended into the average alongside real user ratings"
    )
    seed_average_cost = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="Optional starting cost estimate from an external source"
    )

    def __str__(self):
        return self.name

    @property
    def effective_latitude(self):
        """See Destination.effective_latitude -- same fallback pattern."""
        return self.latitude if self.latitude is not None else self.location.latitude

    @property
    def effective_longitude(self):
        return self.longitude if self.longitude is not None else self.location.longitude

    def recompute_aggregates(self):
        """Recalculate rating/average_cost as the mean of the seed value
        (if any) plus every real user submission (RestaurantRating), and
        save the result. Called whenever a submission is added, changed,
        or removed.

        The seed counts as exactly one vote, permanently -- it isn't
        overwritten or diluted away by later real submissions, it's
        averaged in alongside them every time. This means a good starting
        rating (e.g. seeded from a CSV import) doesn't get wiped out by a
        single early review, while still letting real users' opinions
        genuinely move the number over time as more of them rate it.

        If there's no seed and no submissions yet, rating/average_cost
        stay None (shown as "Not yet rated" / "Unknown" in the UI).

        Values are coerced to float before averaging -- ratings/costs
        don't need exact Decimal precision for an average, and this keeps
        this method safe regardless of whether seed_rating/seed_cost
        happen to be Decimal (the normal case) or a plain float/int (e.g.
        if ever set directly in code rather than through a form/CSV)."""
        rating_votes = [float(v) for v in self.user_ratings.values_list('rating', flat=True)]
        if self.seed_rating is not None:
            rating_votes.append(float(self.seed_rating))
        self.rating = round(sum(rating_votes) / len(rating_votes), 1) if rating_votes else None

        cost_votes = [float(v) for v in
                      self.user_ratings.exclude(average_cost=None).values_list('average_cost', flat=True)]
        if self.seed_average_cost is not None:
            cost_votes.append(float(self.seed_average_cost))
        self.average_cost = round(sum(cost_votes) / len(cost_votes), 2) if cost_votes else None

        self.save(update_fields=['rating', 'average_cost'])


class RestaurantRating(models.Model):
    """
    A single user's rating (and optional cost estimate) for a restaurant.
    PlaceToEat.rating/average_cost are kept as a cached average of these --
    see PlaceToEat.recompute_aggregates(). This is how TravelEase can
    still show ratings/prices for restaurants sourced from map data (like
    OpenStreetMap) that doesn't include that commercial information.
    """
    place = models.ForeignKey(PlaceToEat, on_delete=models.CASCADE, related_name='user_ratings')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='restaurant_ratings')
    rating = models.DecimalField(
        max_digits=3, decimal_places=1,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="Your rating out of 5"
    )
    average_cost = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="Optional -- what you'd estimate the average cost per person to be"
    )
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One rating per user per restaurant -- submitting again updates it
        # rather than creating a second one.
        unique_together = ('place', 'user')
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} rated {self.place.name}: {self.rating}"


@receiver(post_delete, sender=RestaurantRating)
def _recompute_place_after_rating_deleted(sender, instance, **kwargs):
    """Keep PlaceToEat.rating/average_cost in sync when a rating goes away
    -- not just when one is added. This matters even when nobody directly
    deletes a rating: deleting a user account cascades to delete their
    RestaurantRating rows too, and without this signal the restaurant's
    cached rating/cost would keep reflecting a vote that no longer exists.
    Connecting a signal here also disables Django's "fast delete"
    optimization for this model, guaranteeing this fires even on cascaded
    bulk deletes."""
    try:
        instance.place.recompute_aggregates()
    except PlaceToEat.DoesNotExist:
        # The restaurant itself is also being deleted in the same cascade
        # (e.g. its Location was deleted) -- nothing left to update.
        pass

# NOTE: LoginHistory / RecentView / SavedItem used to be defined here too,
# duplicating accounts.models. Every view in this app actually reads and
# writes the accounts.* versions, so these were dead, unused tables that
# silently accumulated orphaned data. They were removed in migration 0003
# after migration 0002 copied any existing rows into the accounts app.
# See accounts/models.py for the real versions.