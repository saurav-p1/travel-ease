import re

from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator
from .models import Location, Destination, PlaceToEat

# views.is_open() parses business_hours with a single strptime('%H:%M') on
# each side of a '-'. Anything else comes back None, which the UI shows as
# "hours unknown" and the "Open now" filter silently excludes.
#
# FOUND DURING AUDIT: this form's own placeholder and help text told users
# to type "9 AM - 9 PM", which is exactly the shape is_open() cannot read.
# So every restaurant a user submitted was permanently invisible to the
# "Open now" filter and never showed an open/closed badge -- the form was
# instructing people to enter unusable data. Validating here means the
# field either holds something the site can act on, or is empty.
BUSINESS_HOURS_RE = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$')
BUSINESS_HOURS_HELP = (
    "Optional. 24-hour format only, e.g. '09:00-21:00'. Use '00:00-23:59' for "
    "always open. Leave blank if the hours vary by day."
)

# Same input styling as every other form on the site (accounts/forms.py's
# _INPUT_CLASS) -- kept as an identical copy rather than a cross-app import
# so this form module doesn't take on a dependency on the accounts app for
# something this small; if the site's input styling changes, both copies
# are easy to grep for and update together.
_INPUT_CLASS = (
    'w-full px-4 py-3 border border-gray-300 rounded-xl focus:outline-none '
    'focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all duration-200'
)


class DestinationSubmissionForm(forms.ModelForm):
    """User-facing "add a destination" form. Deliberately excludes
    `submitted_by`/`status` -- those are set by the view (views.submit_place),
    never by user input, so a submitter can't set their own item straight
    to 'approved' or attribute it to someone else."""

    latitude = forms.DecimalField(
        max_digits=9, decimal_places=6, required=False,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        widget=forms.NumberInput(attrs={'class': _INPUT_CLASS, 'step': 'any'}),
        help_text="Optional. Leave blank to use the town/city's general area "
                   "instead of this specific spot.",
    )
    longitude = forms.DecimalField(
        max_digits=9, decimal_places=6, required=False,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
        widget=forms.NumberInput(attrs={'class': _INPUT_CLASS, 'step': 'any'}),
    )

    class Meta:
        model = Destination
        fields = ['location', 'name', 'description', 'latitude', 'longitude', 'image']
        widgets = {
            'location': forms.Select(attrs={'class': _INPUT_CLASS}),
            'name': forms.TextInput(attrs={'class': _INPUT_CLASS}),
            'description': forms.Textarea(attrs={'class': _INPUT_CLASS, 'rows': 4}),
            'image': forms.URLInput(attrs={'class': _INPUT_CLASS, 'placeholder': 'https://...'}),
        }
        labels = {'image': 'Photo URL'}
        help_texts = {
            'image': 'Optional. A direct link to a photo of this place (e.g. ending in .jpg or .png).',
        }

    def clean(self):
        cleaned = super().clean()
        lat, lng = cleaned.get('latitude'), cleaned.get('longitude')
        # Either both coordinates are given (a real point) or neither is
        # (falls back to the town's centroid via effective_latitude/
        # effective_longitude) -- one without the other is neither, and
        # would silently pass an incomplete/wrong point to every map and
        # distance calculation downstream.
        if (lat is None) != (lng is None):
            raise forms.ValidationError(
                "Provide both latitude and longitude, or leave both blank."
            )
        return cleaned


class PlaceToEatSubmissionForm(forms.ModelForm):
    """User-facing "add a restaurant" form. Same submitted_by/status
    exclusion rationale as DestinationSubmissionForm above. Also excludes
    rating/average_cost/seed_rating/seed_average_cost -- a brand new
    submission has no ratings yet; those only ever come from
    RestaurantRating via recompute_aggregates(), same as any other
    restaurant (see views.submit_restaurant_rating)."""

    latitude = forms.DecimalField(
        max_digits=9, decimal_places=6, required=False,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        widget=forms.NumberInput(attrs={'class': _INPUT_CLASS, 'step': 'any'}),
        help_text="Optional. Leave blank to use the town/city's general area "
                   "instead of this specific spot.",
    )
    longitude = forms.DecimalField(
        max_digits=9, decimal_places=6, required=False,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
        widget=forms.NumberInput(attrs={'class': _INPUT_CLASS, 'step': 'any'}),
    )

    class Meta:
        model = PlaceToEat
        fields = ['location', 'name', 'cuisine', 'price_range', 'business_hours',
                  'latitude', 'longitude', 'image']
        widgets = {
            'location': forms.Select(attrs={'class': _INPUT_CLASS}),
            'name': forms.TextInput(attrs={'class': _INPUT_CLASS}),
            'cuisine': forms.TextInput(attrs={'class': _INPUT_CLASS, 'placeholder': 'e.g. Nepali, Newari, Continental'}),
            'price_range': forms.Select(attrs={'class': _INPUT_CLASS}),
            'business_hours': forms.TextInput(attrs={'class': _INPUT_CLASS, 'placeholder': '09:00-21:00'}),
            'image': forms.URLInput(attrs={'class': _INPUT_CLASS, 'placeholder': 'https://...'}),
        }
        labels = {'image': 'Photo URL'}
        help_texts = {
            'business_hours': BUSINESS_HOURS_HELP,
            'image': 'Optional. A direct link to a photo of this place (e.g. ending in .jpg or .png).',
        }

    def clean_business_hours(self):
        value = (self.cleaned_data.get('business_hours') or '').strip()
        if not value:
            return ''
        if not BUSINESS_HOURS_RE.match(value):
            raise forms.ValidationError(
                "Please use 24-hour HH:MM-HH:MM, e.g. '09:00-21:00'. "
                "Leave it blank if the hours vary by day -- the site can't "
                "show an accurate open/closed badge from anything else."
            )
        return value

    def clean(self):
        cleaned = super().clean()
        lat, lng = cleaned.get('latitude'), cleaned.get('longitude')
        if (lat is None) != (lng is None):
            raise forms.ValidationError(
                "Provide both latitude and longitude, or leave both blank."
            )
        return cleaned
