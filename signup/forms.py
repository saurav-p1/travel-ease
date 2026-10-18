from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import CustomUser
import re

MINIMUM_SIGNUP_AGE = 18


def signup_dob_cutoff():
    """The latest date of birth that satisfies MINIMUM_SIGNUP_AGE, as of
    today -- computed fresh (not hardcoded) so it never goes stale, and
    shared by both SignupForm's widget attrs and SignupView's context so
    the raw-HTML fallback in signup.html (which can't render {{ form.
    date_of_birth }} directly -- see the note on that template) and the
    Django-rendered widget always agree."""
    today = date.today()
    try:
        return today.replace(year=today.year - MINIMUM_SIGNUP_AGE)
    except ValueError:
        # today is Feb 29 on a leap year and (today.year - 18) isn't one
        return today.replace(month=2, day=28, year=today.year - MINIMUM_SIGNUP_AGE)

# NOTE on Google sign-in and phone_number/marketing_emails: this file used
# to also define an `AllauthSignupForm(allauth.account.forms.SignupForm)`,
# intended to collect phone_number/marketing_emails during Google signup.
# It was dead code -- never referenced by SOCIALACCOUNT_FORMS or anywhere
# else -- and removed here after confirming (by actually instantiating it
# against the installed django-allauth 65.19.2) that it was also broken:
# it subclassed the wrong allauth base (`allauth.account.forms.SignupForm`,
# for *local* signup) instead of `allauth.socialaccount.forms.SignupForm`
# (what SOCIALACCOUNT_FORMS['signup'] actually requires). Wiring it in as-is
# would have thrown `TypeError: BaseForm.__init__() got an unexpected
# keyword argument 'sociallogin'` on every Google sign-in attempt, since
# allauth's social signup flow always calls the form with that kwarg.
#
# Net effect today: SOCIALACCOUNT_AUTO_SIGNUP defaults to True with no
# override in settings.py, so Google sign-in auto-creates the account with
# phone_number='' / marketing_emails=False and no form step ever asks for
# them -- a safe default, just not a deliberate product decision. Building
# this properly means a form that extends
# `allauth.socialaccount.forms.SignupForm`, matches its
# `__init__(self, *args, sociallogin, **kwargs)` / `save(self, request)`
# contract, gets wired via `SOCIALACCOUNT_FORMS = {'signup': '...'}` with
# `SOCIALACCOUNT_AUTO_SIGNUP = False`, and is tested against a real Google
# OAuth handshake (not just unit-instantiated) before shipping -- this
# sandbox has valid-looking OAuth credentials in .env but no way to drive
# an actual browser through Google's consent screen, so that last step
# needs a real dev/staging pass, not a guess.

class SignupForm(UserCreationForm):
    """Standard Django signup form"""
    first_name = forms.CharField(
        max_length=30,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-3 pr-10 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200',
            'placeholder': 'John'
        })
    )
    
    last_name = forms.CharField(
        max_length=30,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-3 pr-10 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200',
            'placeholder': 'Doe'
        })
    )
    
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'w-full px-4 py-3 pr-10 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200',
            'placeholder': 'john.doe@example.com'
        })
    )
    
    country_code = forms.ChoiceField(
        choices=[
            ('+1', '🇺🇸 +1'),
            ('+44', '🇬🇧 +44'),
            ('+33', '🇫🇷 +33'),
            ('+49', '🇩🇪 +49'),
            ('+81', '🇯🇵 +81'),
            ('+86', '🇨🇳 +86'),
            ('+91', '🇮🇳 +91'),
            ('+61', '🇦🇺 +61'),
            ('+977', '🇳🇵 +977'),
        ],
        initial='+977',  # Default to Nepal since you're in Kathmandu
        widget=forms.Select(attrs={
            'class': 'country-select px-3 py-3 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200'
        })
    )
    
    phone_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'flex-1 px-4 py-3 pr-10 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200',
            'placeholder': '98XXXXXXXX'
        })
    )
    
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'w-full px-4 py-3 pr-10 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200',
        })
    )
    
    # Form-only fields
    accept_policy = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must accept the terms and conditions'}
    )
    
    marketing_emails = forms.BooleanField(
        required=False,
        initial=False
    )
    
    class Meta:
        model = CustomUser
        fields = (
            'first_name', 'last_name', 'email', 'country_code', 
            'phone_number', 'date_of_birth',
        )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Customize password fields with proper styling
        self.fields['password1'].widget.attrs.update({
            'class': 'w-full px-4 py-3 pr-10 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200',
            'placeholder': 'Enter your password'
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'w-full px-4 py-3 pr-10 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200',
            'placeholder': 'Confirm your password'
        })
        # Computed fresh on every render instead of a hardcoded date, so the
        # client-side date-picker cap keeps tracking "18 years ago" correctly
        # as time passes, rather than permanently pinning to one birth year.
        self.fields['date_of_birth'].widget.attrs['max'] = signup_dob_cutoff().isoformat()
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and CustomUser.objects.filter(email__iexact=email).exists():
            raise ValidationError("A user with this email already exists.")
        return email
    
    def clean_phone_number(self):
        phone = self.cleaned_data.get('phone_number')
        if phone:
            digits_only = re.sub(r'\D', '', phone)
            if len(digits_only) < 10 or len(digits_only) > 15:
                raise ValidationError("Phone number must be between 10 and 15 digits.")
            
            country_code = self.cleaned_data.get('country_code', '+977')
            if country_code == '+1' and len(digits_only) != 10:
                raise ValidationError("For US numbers, please enter 10 digits.")
            elif country_code == '+977' and len(digits_only) != 10:
                raise ValidationError("For Nepal numbers, please enter 10 digits.")
        return phone

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get('date_of_birth')
        if dob:
            today = date.today()
            if dob > today:
                raise ValidationError("Date of birth cannot be in the future.")
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if age < MINIMUM_SIGNUP_AGE:
                raise ValidationError(f"You must be at least {MINIMUM_SIGNUP_AGE} years old to create an account.")
        return dob
    
    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        if password1:
            try:
                validate_password(password1)
            except ValidationError as e:
                raise ValidationError(e.messages)
            
            if len(password1) < 8:
                raise ValidationError("Password must be at least 8 characters long.")
            if not re.search(r'[A-Z]', password1):
                raise ValidationError("Password must contain at least one uppercase letter.")
            if not re.search(r'[a-z]', password1):
                raise ValidationError("Password must contain at least one lowercase letter.")
            if not re.search(r'\d', password1):
                raise ValidationError("Password must contain at least one number.")
            if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password1):
                raise ValidationError("Password must contain at least one special character.")
        return password1
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email']
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.country_code = self.cleaned_data['country_code']
        user.phone_number = self.cleaned_data['phone_number']
        user.date_of_birth = self.cleaned_data['date_of_birth']

        if commit:
            user.save()
            # accounts.models has a post_save signal on CustomUser that
            # get_or_creates a UserPreference row the moment user.save()
            # runs above, always with marketing_emails at its own default
            # (False) -- it has no way to know what this form's checkbox
            # said. Apply the signup-time choice to that row right here, so
            # there's exactly one place ("did they check the box?") that
            # decides the one field ("are they subscribed?") the rest of the
            # app actually reads.
            from accounts.models import UserPreference
            prefs, _ = UserPreference.objects.get_or_create(user=user)
            marketing_emails = self.cleaned_data.get('marketing_emails', False)
            if prefs.marketing_emails != marketing_emails:
                prefs.marketing_emails = marketing_emails
                prefs.save(update_fields=['marketing_emails'])
        return user
