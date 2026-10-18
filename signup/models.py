from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower
from django.core.validators import RegexValidator


class CustomUserManager(BaseUserManager):
    """
    Custom manager for CustomUser, needed because USERNAME_FIELD is 'email'.
    Django's default UserManager.create_user()/create_superuser() require
    'username' as their first positional argument regardless of
    USERNAME_FIELD, which broke `manage.py createsuperuser` and any other
    code creating users without an explicit username.
    """

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('first_name', extra_fields.get('first_name', 'Admin'))
        extra_fields.setdefault('last_name', extra_fields.get('last_name', 'User'))

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)

class CustomUser(AbstractUser):
    """
    Custom User model that extends Django's AbstractUser
    This should be the ONLY user model in your project
    """
    
    # Make username optional for email-based authentication
    username = models.CharField(
        max_length=150,
        unique=True,
        blank=True,
        null=True,
        help_text='Optional. 150 characters or fewer. Letters, digits and @/./+/-/_ only.',
        validators=[AbstractUser.username_validator],
        error_messages={
            'unique': "A user with that username already exists.",
        },
    )
    
    # Basic profile fields
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=30)
    last_name = models.CharField(max_length=30)
    
    # Contact information
    country_code = models.CharField(
        max_length=5,
        default='+977',  # Nepal -- matches SignupForm.country_code's own
                          # initial='+977' ("Default to Nepal since you're
                          # in Kathmandu"). This model-level default is what
                          # actually applies for accounts created without
                          # going through that form at all -- e.g. Google
                          # OAuth sign-in via allauth, which only ever sets
                          # email/first_name/last_name and leaves every
                          # other field to its model default. It was '+1'
                          # here, silently giving every Google sign-up a
                          # USA country code on an app whose entire dataset
                          # is Nepal-specific.
        validators=[RegexValidator(r'^\+\d{1,4}$', 'Enter a valid country code')]
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        validators=[RegexValidator(r'^\+?1?\d{9,15}$', 'Enter a valid phone number')]
    )
    
    # Personal information
    date_of_birth = models.DateField(blank=True, null=True)

    # NOTE: marketing-email consent lives ONLY on accounts.UserPreference.
    # marketing_emails used to also be a field here, set once at signup and
    # never read again by anything (accounts.UserPreference.marketing_emails,
    # auto-created with its own independent default, was the field every
    # real code path -- the preferences page, the newsletter-subscribe
    # endpoint -- actually checked/updated). Net effect: checking the box at
    # signup did nothing at all; the account was never actually subscribed.
    # Removed here (see migration 0008) rather than kept in sync, so there's
    # one field, one meaning, and no way for the two to drift apart again.
    # SignupForm.save() now writes the signup checkbox straight into
    # UserPreference.marketing_emails instead.

    # Account metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_email_verified = models.BooleanField(default=False)
    
    # Use email as the username field
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    objects = CustomUserManager()

    # Fix the reverse accessor conflicts by adding related_name
    groups = models.ManyToManyField(
        'auth.Group',
        verbose_name='groups',
        blank=True,
        help_text='The groups this user belongs to.',
        related_name='customuser_set',  # This fixes the conflict
        related_query_name='customuser',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name='user permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        related_name='customuser_set',  # This fixes the conflict
        related_query_name='customuser',
    )
    
    class Meta:
        db_table = 'auth_user'  # Use the default auth_user table
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        constraints = [
            # The form-level duplicate-email check (SignupForm.clean_email)
            # prevents case-variant duplicates in the normal sequential
            # case, but that's a check-then-create pattern -- two nearly
            # simultaneous signups with case-variant emails could both pass
            # the check before either commits. This enforces it atomically
            # at the database level regardless of timing, so it can never
            # be raced. Combined with EmailBackend's defensive handling of
            # MultipleObjectsReturned, this closes the gap completely.
            models.UniqueConstraint(
                Lower('email'), name='unique_lower_email',
                violation_error_message='A user with this email already exists.',
            ),
        ]
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"
    
    def get_full_name(self):
        """Return the first_name plus the last_name, with a space in between."""
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_short_name(self):
        """Return the short name for the user."""
        return self.first_name
    
    @property
    def full_phone_number(self):
        """Return the complete phone number with country code."""
        if self.phone_number:
            return f"{self.country_code}{self.phone_number}"
        return None