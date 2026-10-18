from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.cache import cache
from .models import LoginHistory, RecentView, SavedItem, UserPreference
from .forms import UserPreferenceForm, ContactInfoForm
import logging

logger = logging.getLogger(__name__)

# Matches the policy already declared in settings.ACCOUNT_RATE_LIMITS
# ("login_failed": "5/5m") -- that setting only protects allauth's own
# login views, not this custom one, so it was silently not actually
# protecting the login endpoint the site uses. This enforces the same
# policy directly. Keyed on IP+email together: keying on email alone
# would let one attacker's IP get blocked from targeting a specific
# account, but keying only on IP risks locking out many unrelated users
# behind a shared/NAT'd IP from a single attacker targeting someone else.
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 5 * 60


def _login_rate_limit_key(request, username):
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    return f'login_failed:{ip}:{(username or "").strip().lower()}'


def login_view(request):
    """
    Custom login view - handles login only.
    """
    if request.user.is_authenticated:
        return redirect('web_interface:home')

    # The next-page target the form should carry through to whichever
    # render path this request ends up on.
    #
    # FIXED (found during audit, second pass): the first fix here only
    # checked request.GET when rendering, which is correct for the
    # initial GET that first displays this page -- but every re-render
    # after a failed login attempt (wrong password, or hitting the rate
    # limit) happens on a POST to a bare URL with no query string, so
    # request.GET.next is empty on those renders regardless of what was
    # actually submitted. That silently reset the hidden `next` field to
    # empty after any single failed attempt, so a person who mistyped
    # their password once and then logged in correctly on the retry still
    # landed on the homepage instead of back where they came from -- the
    # same underlying bug, just one retry away from where the first fix
    # left off. Computing this once, from whichever of POST/GET actually
    # has it, and passing it explicitly into every render's context (never
    # falling back to the template silently re-deriving it from
    # request.GET) is what actually closes this for every path.
    next_value = request.POST.get('next') or request.GET.get('next', '')

    if request.method == 'POST':
        submitted_username = request.POST.get('username', '')
        rate_limit_key = _login_rate_limit_key(request, submitted_username)
        recent_failures = cache.get(rate_limit_key, 0)

        if recent_failures >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
            messages.error(
                request,
                "Too many failed login attempts. Please wait a few minutes and try again."
            )
            form = AuthenticationForm(request)
            return render(request, 'accounts/login.html', {'form': form, 'next_value': next_value})

        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            # AuthenticationForm.clean() already called authenticate() and
            # would have raised a ValidationError (routing us to the `else`
            # branch below) if it had failed -- form.is_valid() being True
            # guarantees get_user() returns a real, active, already-
            # authenticated user. A second manual authenticate() call here
            # was previously redundant (a second password hash on every
            # single login) and, in the vanishingly rare case it returned
            # None instead of the same user, silently fell through with no
            # error message shown and no rate-limit increment recorded.
            user = form.get_user()
            cache.delete(rate_limit_key)
            login(request, user)

            # "Remember me" checkbox: checked keeps the normal
            # persistent session (SESSION_COOKIE_AGE, ~2 weeks);
            # unchecked expires the session cookie when the browser
            # closes instead. The checkbox was previously rendered but
            # never actually read here, so it had no effect either way.
            if request.POST.get('remember_me') == 'on':
                request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            else:
                request.session.set_expiry(0)

            # Create login history entry
            try:
                LoginHistory.objects.create(
                    user=user,
                    ip_address=request.META.get('REMOTE_ADDR', 'Unknown'),
                    action='login',
                    user_agent=request.META.get('HTTP_USER_AGENT', '')
                )
            except Exception as e:
                logger.warning(f"Could not create login history: {str(e)}")
            
            messages.success(request, f'Welcome back, {user.get_full_name() or user.email}!')

            # Redirect to next parameter (a path) or the homepage.
            # Must validate it's actually same-site first -- otherwise
            # a crafted link like /accounts/login/?next=https://evil.com
            # would redirect someone to an attacker's site immediately
            # after they successfully log in on the real page.
            if next_value and url_has_allowed_host_and_scheme(
                url=next_value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_value)
            return redirect('web_interface:home')
        else:
            # Atomic increment, not a read-then-write of the value read at
            # the top of this request: under real concurrent attempts (the
            # exact scenario this limiter exists to stop), two requests
            # reading the same stale count and each writing back count+1
            # would silently lose an increment, letting more than
            # LOGIN_RATE_LIMIT_MAX_ATTEMPTS attempts through. cache.add()
            # only initializes the key if absent (itself atomic), then
            # cache.incr() atomically bumps it -- LocMemCache implements
            # both under its internal lock, so this is race-free within a
            # process. (LocMemCache itself doesn't share state across
            # separate worker processes; a shared backend like Redis would
            # be needed for that -- a separate, pre-existing limitation of
            # this cache backend choice, not something introduced here.)
            cache.add(rate_limit_key, 0, LOGIN_RATE_LIMIT_WINDOW_SECONDS)
            cache.incr(rate_limit_key)
            messages.error(request, "Invalid username or password. Please try again.")
    else:
        form = AuthenticationForm()

    return render(request, 'accounts/login.html', {'form': form, 'next_value': next_value})

@login_required
def profile_view(request):
    """
    Profile view for logged-in users.
    Displays user info, recent browsing history and saved items.
    """
    try:
        recent_browsing = RecentView.objects.filter(user=request.user).order_by('-viewed_at')[:5]
    except Exception:
        recent_browsing = []
    
    try:
        saved_items = SavedItem.objects.filter(user=request.user).order_by('-saved_at')
    except Exception:
        saved_items = []
    
    try:
        user_preferences = UserPreference.objects.get(user=request.user)
    except UserPreference.DoesNotExist:
        user_preferences = None
    
    try:
        login_history = LoginHistory.objects.filter(user=request.user).order_by('-timestamp')[:10]
    except Exception:
        login_history = []
    
    context = {
        'user': request.user,
        'recent_browsing': recent_browsing,
        'saved_items': saved_items,
        'user_preferences': user_preferences,
        'login_history': login_history,
    }
    
    return render(request, 'accounts/profile.html', context)

def logout_view(request):
    """
    Logs out the user and redirects to home.
    """
    # Log the logout action if user is authenticated
    if request.user.is_authenticated:
        try:
            LoginHistory.objects.create(
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR', 'Unknown'),
                action='logout',
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except Exception as e:
            logger.warning(f"Could not create logout history: {str(e)}")
    
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect('web_interface:home')

@login_required
def preferences_view(request):
    """
    Settings page -- lets a user actually edit the UserPreference row
    that's been displayed read-only on their profile page all along, and
    their own contact info (name/phone), which was never editable -- or
    even visible -- anywhere in the product before this.
    Auto-created by the post_save signal in models.py, so get_or_create
    here is just defense in depth (e.g. for accounts that existed before
    that signal was added).
    """
    prefs, _ = UserPreference.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        if request.POST.get('form_name') == 'contact_info':
            contact_form = ContactInfoForm(request.POST, instance=request.user)
            prefs_form = UserPreferenceForm(instance=prefs)
            if contact_form.is_valid():
                contact_form.save()
                messages.success(request, 'Your contact info has been updated.')
                return redirect('accounts:preferences')
        else:
            prefs_form = UserPreferenceForm(request.POST, instance=prefs)
            contact_form = ContactInfoForm(instance=request.user)
            if prefs_form.is_valid():
                prefs_form.save()
                messages.success(request, 'Your preferences have been updated.')
                return redirect('accounts:preferences')
    else:
        prefs_form = UserPreferenceForm(instance=prefs)
        contact_form = ContactInfoForm(instance=request.user)

    return render(request, 'accounts/preferences.html', {
        'form': prefs_form,
        'contact_form': contact_form,
    })


def public_profile_view(request, user_id):
    """
    A traveler's public profile -- the real feature UserPreference.
    profile_visibility was always meant to control, but which never
    existed anywhere for it to gate access to.

    'friends' is treated the same as 'private' for now: there's no
    friends/connections model anywhere in this codebase, so there's no
    way to check "is this viewer a friend of this profile's owner" yet.
    Once a friends feature exists, this is the one spot that needs to
    change -- add a real friends check here instead of falling through
    to the same branch as 'private'.
    """
    User = get_user_model()
    profile_user = get_object_or_404(User, id=user_id)
    prefs, _ = UserPreference.objects.get_or_create(user=profile_user)

    is_owner = request.user.is_authenticated and request.user.id == profile_user.id
    is_visible = is_owner or prefs.profile_visibility == 'public'

    return render(request, 'accounts/public_profile.html', {
        'profile_user': profile_user,
        'preferences': prefs,
        'is_visible': is_visible,
        'is_owner': is_owner,
    })


@login_required
def dashboard_view(request):
    """
    User dashboard with quick stats and recent activity.
    """
    try:
        recent_views_count = RecentView.objects.filter(user=request.user).count()
        saved_items_count = SavedItem.objects.filter(user=request.user).count()
        recent_activity = LoginHistory.objects.filter(user=request.user).order_by('-timestamp')[:5]
    except Exception:
        recent_views_count = 0
        saved_items_count = 0
        recent_activity = []
    
    context = {
        'recent_views_count': recent_views_count,
        'saved_items_count': saved_items_count,
        'recent_activity': recent_activity,
    }
    
    return render(request, 'accounts/dashboard.html', context)