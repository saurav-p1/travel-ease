from django import forms
from django.contrib.auth import get_user_model
from .models import UserPreference

_INPUT_CLASS = (
    'w-full px-4 py-3 border border-gray-300 rounded-xl focus:outline-none '
    'focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all duration-200'
)

# Same country list signup/forms.py's SignupForm offers, kept in sync so a
# user editing their number later sees the same options they had at signup.
_COUNTRY_CODE_CHOICES = [
    ('+1', '🇺🇸 +1'), ('+44', '🇬🇧 +44'), ('+33', '🇫🇷 +33'),
    ('+49', '🇩🇪 +49'), ('+81', '🇯🇵 +81'), ('+86', '🇨🇳 +86'),
    ('+91', '🇮🇳 +91'), ('+61', '🇦🇺 +61'), ('+977', '🇳🇵 +977'),
]


class ContactInfoForm(forms.ModelForm):
    """
    Lets a user edit their own name/phone number.

    Found during audit: CustomUser.phone_number/country_code (plus the
    full_phone_number property built on top of them) were captured at
    signup, validated, admin-visible -- and then never displayed or
    editable anywhere in the actual product, not even on the user's own
    profile page. Same "collects real data, nothing reads it back to
    you" shape as the login-history and travel-preferences gaps fixed
    elsewhere. This closes it for everyone, not just people who signed up
    through the regular form -- including Google sign-ins, who never had
    anywhere to add a phone number at all until now.
    """

    country_code = forms.ChoiceField(choices=_COUNTRY_CODE_CHOICES, required=False,
                                      widget=forms.Select(attrs={'class': _INPUT_CLASS}))

    class Meta:
        model = get_user_model()
        fields = ['first_name', 'last_name', 'country_code', 'phone_number']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': _INPUT_CLASS}),
            'last_name': forms.TextInput(attrs={'class': _INPUT_CLASS}),
            'phone_number': forms.TextInput(attrs={'class': _INPUT_CLASS, 'placeholder': '98XXXXXXXX'}),
        }


class UserPreferenceForm(forms.ModelForm):
    """Lets a user edit their own UserPreference row.

    UserPreference has always been auto-created (see the post_save signal
    in accounts/models.py) and displayed on the profile page -- but there
    was never anywhere in the product for a user to actually change it.
    The only way to edit it was the Django admin, which real users don't
    have access to, so every account was permanently stuck with the
    signal's defaults ('mid_range' / 'USD' / 'en').

    Deliberately NOT included here: `push_notifications` and
    `email_notifications`. Both fields exist on the model and are
    editable in the admin, but there is no notification-sending or
    push-delivery system anywhere in this codebase for either of them to
    control -- exposing a toggle for a feature that doesn't exist would
    just recreate the exact "collects a preference, nothing reads it"
    bug this form exists to fix. Wire them up here once (a) there's a
    real event worth emailing someone about, and (b) there's a real push
    subscription/service-worker pipeline.
    """

    class Meta:
        model = UserPreference
        fields = ['travel_style', 'preferred_currency', 'preferred_language',
                  'marketing_emails', 'profile_visibility']
        widgets = {
            'travel_style': forms.Select(attrs={'class': _INPUT_CLASS}),
            'preferred_currency': forms.TextInput(attrs={'class': _INPUT_CLASS, 'maxlength': 3, 'placeholder': 'USD'}),
            'preferred_language': forms.TextInput(attrs={'class': _INPUT_CLASS, 'maxlength': 10, 'placeholder': 'en'}),
            'marketing_emails': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500'}),
            'profile_visibility': forms.Select(attrs={'class': _INPUT_CLASS}),
        }
        help_texts = {
            'preferred_currency': "Note: prices are currently always shown in Nepali Rupees (Rs.) -- this app doesn't yet convert between currencies, so changing this only saves your preference for later.",
            'preferred_language': "Note: this app is currently English-only -- changing this only saves your preference for later.",
            'profile_visibility': 'Controls who can view your public traveler profile page.',
        }

    def clean_preferred_currency(self):
        value = self.cleaned_data.get('preferred_currency', '')
        return value.strip().upper()

    def clean_preferred_language(self):
        value = self.cleaned_data.get('preferred_language', '')
        return value.strip().lower()
