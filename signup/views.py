import logging
from django.shortcuts import render
from django.contrib.auth import login
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from django.views.generic import CreateView
from django.urls import reverse_lazy
from django.middleware.csrf import get_token
from django.db import IntegrityError
from django.core.cache import cache
from .forms import SignupForm, signup_dob_cutoff
from .models import CustomUser
import json

logger = logging.getLogger(__name__)

# Prevents automated mass account creation. Confirmed this was needed:
# 10 rapid signup attempts from one source all succeeded with zero
# throttling before this was added. Keyed on IP alone (unlike login's
# limiter) since each signup attempt uses a different email by
# definition -- a spam bot rotates emails, so email-keying wouldn't
# limit anything. Every attempt counts, not just successful ones, so
# rapid-fire validation-probing is throttled too, not just completed
# signups.
SIGNUP_RATE_LIMIT_MAX_ATTEMPTS = 5
SIGNUP_RATE_LIMIT_WINDOW_SECONDS = 60 * 60


def _signup_rate_limited(request):
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    key = f'signup_attempts:{ip}'
    attempts = cache.get(key, 0)
    if attempts >= SIGNUP_RATE_LIMIT_MAX_ATTEMPTS:
        return True
    # Atomic increment, not a read-then-write of the `attempts` value read
    # above: under real concurrent signup attempts (the exact scenario
    # this limiter exists to stop -- e.g. a scripted signup-spam run),
    # two requests reading the same stale count and each writing back
    # count+1 would silently lose an increment, letting more than
    # SIGNUP_RATE_LIMIT_MAX_ATTEMPTS attempts through per window. Same
    # fix as accounts.views.login_view's rate limiter: cache.add() only
    # initializes the key if it's not already present (itself atomic),
    # then cache.incr() atomically bumps it.
    cache.add(key, 0, SIGNUP_RATE_LIMIT_WINDOW_SECONDS)
    cache.incr(key)
    return False


SIGNUP_RATE_LIMIT_MESSAGE = 'Too many signup attempts from this connection. Please try again in a while.'


class SignupView(CreateView):
    form_class = SignupForm
    template_name = 'signup/signup.html'
    success_url = reverse_lazy('signup:success')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['dob_max'] = signup_dob_cutoff().isoformat()
        return context

    def post(self, request, *args, **kwargs):
        if _signup_rate_limited(request):
            self.object = None
            return self.render_to_response(
                self.get_context_data(form=self.get_form(), server_error_message=SIGNUP_RATE_LIMIT_MESSAGE)
            )
        return super().post(request, *args, **kwargs)
    
    def form_valid(self, form):
        # The form's own clean_email() already rejects duplicate emails
        # (case-insensitively) in the normal case, but that's a
        # check-then-save pattern -- two near-simultaneous signups could
        # both pass validation before either commits. The database's own
        # case-insensitive unique constraint (signup.CustomUser.Meta)
        # closes that race atomically, but it means .save() can still
        # raise here in that rare case. Handle it the same way a normal
        # duplicate-email validation error would be shown, rather than
        # crashing with an uncaught 500.
        try:
            response = super().form_valid(form)
        except IntegrityError:
            form.add_error('email', 'A user with this email already exists.')
            return self.form_invalid(form)
        # Log the user in after successful signup. Must specify which
        # backend authenticated them since AUTHENTICATION_BACKENDS has
        # more than one entry -- login() can't guess which one to use.
        login(self.request, self.object, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(self.request, '🎉 Welcome to TravelEase! Your adventure begins now!')
        return response
    
    def form_invalid(self, form):
        # Handle AJAX requests
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            errors = {}
            for field, error_list in form.errors.items():
                errors[field] = error_list[0]  # Get first error for each field
            return JsonResponse({'success': False, 'errors': errors})
        return super().form_invalid(form)

@csrf_protect
@require_http_methods(["POST"])
def ajax_signup(request):
    """Handle AJAX signup requests"""
    if _signup_rate_limited(request):
        return JsonResponse({'success': False, 'errors': {'general': SIGNUP_RATE_LIMIT_MESSAGE}})

    try:
        # Handle FormData from AJAX request
        data = request.POST.dict()
        logger.debug("Signup AJAX request data: %r", data)

        # Create form with the data
        form = SignupForm(data)
        
        if form.is_valid():
            try:
                user = form.save()
            except IntegrityError:
                # Same race-condition case handled in SignupView.form_valid --
                # the database's case-insensitive unique constraint caught a
                # duplicate that slipped past form validation's own check.
                return JsonResponse({'success': False, 'errors': {'email': 'A user with this email already exists.'}})
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return JsonResponse({
                'success': True, 
                'message': '🎉 Welcome to TravelEase! Your adventure begins now!',
                'redirect_url': str(reverse_lazy('signup:success'))
            })
        else:
            errors = {}
            for field, error_list in form.errors.items():
                if field == '__all__':
                    errors['general'] = error_list[0]
                else:
                    errors[field] = error_list[0]
            return JsonResponse({'success': False, 'errors': errors})
            
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'errors': {'general': 'Invalid data format'}})
    except Exception as e:
        logger.exception("Unexpected error during AJAX signup: %s", e)
        return JsonResponse({'success': False, 'errors': {'general': 'Something went wrong. Please try again.'}})

def signup_success(request):
    """Success page after signup"""
    return render(request, 'signup/success.html')

EMAIL_CHECK_RATE_LIMIT_MAX_ATTEMPTS = 30
EMAIL_CHECK_RATE_LIMIT_WINDOW_SECONDS = 5 * 60


def _email_check_rate_limited(request):
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    key = f'email_check_attempts:{ip}'
    attempts = cache.get(key, 0)
    if attempts >= EMAIL_CHECK_RATE_LIMIT_MAX_ATTEMPTS:
        return True
    cache.add(key, 0, EMAIL_CHECK_RATE_LIMIT_WINDOW_SECONDS)
    cache.incr(key)
    return False


def check_email_availability(request):
    """AJAX endpoint to check if email is available.

    FOUND DURING AUDIT: unlike every other user-input endpoint in this
    app (login, signup itself), this had no rate limiting at all --
    anyone could sweep a list of email addresses through this endpoint
    and use the 'available' flag to find out which ones already have
    TravelEase accounts.
    """
    if _email_check_rate_limited(request):
        return JsonResponse({'available': False, 'error': 'Too many requests. Please slow down.'}, status=429)
    email = request.GET.get('email')
    if email:
        exists = CustomUser.objects.filter(email__iexact=email).exists()
        return JsonResponse({'available': not exists})
    return JsonResponse({'available': False})

def get_csrf_token(request):
    """Get CSRF token for AJAX requests"""
    return JsonResponse({'csrfToken': get_token(request)})