from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.cache import cache
from unittest.mock import patch
from .backends import EmailBackend
from .models import UserPreference, RecentView, SavedItem

User = get_user_model()


class EmailBackendTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='backend@example.com', password='TestPass123!',
            first_name='Back', last_name='End',
        )
        self.backend = EmailBackend()

    def test_authenticate_by_email_case_insensitive(self):
        user = self.backend.authenticate(None, username='BACKEND@example.com', password='TestPass123!')
        self.assertEqual(user, self.user)

    def test_authenticate_wrong_password_returns_none(self):
        user = self.backend.authenticate(None, username='backend@example.com', password='wrong')
        self.assertIsNone(user)

    def test_authenticate_unknown_user_returns_none(self):
        user = self.backend.authenticate(None, username='nobody@example.com', password='whatever')
        self.assertIsNone(user)

    def test_authenticate_with_missing_credentials_returns_none(self):
        self.assertIsNone(self.backend.authenticate(None, username=None, password=None))


class DeactivatedAccountSessionTests(TestCase):
    """Regression tests for a real gap found during audit: EmailBackend.
    get_user() (used on every request to rehydrate request.user from the
    session) had no is_active check at all, unlike Django's own
    ModelBackend.get_user(), which this backend otherwise overrides and is
    meant to extend. A previous fix in this same file closed the
    equivalent gap in authenticate() (a deactivated account could still
    log in fresh) but missed this one: an account deactivated *while
    already logged in* kept full working access for the rest of that
    session, since every subsequent request would still successfully
    rehydrate the (now-deactivated) user."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='deactivated@example.com', password='TestPass123!', first_name='D', last_name='A'
        )

    def test_get_user_returns_none_for_a_deactivated_account(self):
        from accounts.backends import EmailBackend
        backend = EmailBackend()
        self.assertEqual(backend.get_user(self.user.id), self.user)

        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertIsNone(backend.get_user(self.user.id))

    def test_existing_session_is_cut_off_the_moment_the_account_is_deactivated(self):
        """End-to-end version of the above: logs in for real (establishing
        a genuine session, exactly like a real browser), deactivates the
        account server-side (simulating an admin action mid-session), and
        confirms the very next request -- using the same still-existing
        session cookie -- no longer sees them as authenticated, rather
        than continuing to serve a protected page."""
        self.client.force_login(self.user)
        # Confirm the session is genuinely live before deactivating.
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(resp.status_code, 200)

        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(resp.status_code, 302)  # session no longer authenticates -> redirected to login


class LoginFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='login@example.com', password='TestPass123!',
            first_name='Log', last_name='In',
        )

    def test_successful_login_redirects_to_home(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'login@example.com', 'password': 'TestPass123!',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('web_interface:home'))

    def test_invalid_login_shows_error(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'login@example.com', 'password': 'wrongpassword',
        })
        self.assertEqual(resp.status_code, 200)  # re-renders form, no crash

    def test_logout_redirects_to_home(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('accounts:logout'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('web_interface:home'))

    def test_nav_sign_out_is_a_csrf_protected_post_form_not_a_get_link(self):
        """FOUND DURING AUDIT: the nav dropdown's Sign Out control used to
        be a plain <a href> GET link, while profile.html already used a
        POST form with a CSRF token for the exact same action -- a GET
        link isn't covered by Django's CSRF protection, so any third-party
        page could silently log a visitor out (e.g. via an <img> tag)."""
        self.client.force_login(self.user)
        resp = self.client.get(reverse('web_interface:home'))
        html = resp.content.decode()
        logout_url = reverse('accounts:logout')
        self.assertNotIn(f'href="{logout_url}"', html)
        self.assertIn(f'<form method="post" action="{logout_url}"', html)

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(resp.status_code, 302)

    def test_dashboard_loads_when_authenticated(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_login_works_for_any_real_email_including_common_placeholder_ones(self):
        """Regression test: the login page's JS used to special-case the
        exact email 'test@example.com' -- a common placeholder address a
        real person could plausibly use -- by faking a success message and
        never actually submitting the form, so that address could never
        really log in via the browser. Server-side (which is what this
        exercises) was never affected, but confirm nothing about that
        email is treated as special here either."""
        User.objects.create_user(
            email='test@example.com', password='TestPass123!',
            first_name='Test', last_name='User',
        )
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'test@example.com', 'password': 'TestPass123!',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('web_interface:home'))

    def test_remember_me_checked_keeps_persistent_session(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'login@example.com', 'password': 'TestPass123!', 'remember_me': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertGreater(self.client.session.get_expiry_age(), 0)

    def test_remember_me_unchecked_expires_session_at_browser_close(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'login@example.com', 'password': 'TestPass123!',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self.client.session.get_expire_at_browser_close())


class PasswordResetFlowTests(TestCase):
    def test_password_reset_request_does_not_crash(self):
        """Regression test: the email subject template file used to have a
        trailing space in its filename, which didn't match the name Django
        was told to load, and crashed this request with TemplateDoesNotExist."""
        resp = self.client.post(reverse('accounts:password_reset'), {'email': 'anyone@example.com'})
        self.assertEqual(resp.status_code, 302)

    def test_reset_token_lifetime_matches_the_24_hours_promised_in_the_email(self):
        """Regression test: the reset email tells users their link expires
        in 24 hours, but PASSWORD_RESET_TIMEOUT was never overridden from
        Django's own default (3 days) to actually match -- so links stayed
        valid 3x longer than what users were told."""
        from django.conf import settings
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 60 * 60 * 24)


class CaseInsensitiveEmailUniquenessTests(TestCase):
    """Regression tests for a real bug chain found during audit: signup's
    duplicate-email check was case-sensitive while the login backend's
    lookup was case-insensitive. That let someone create a case-variant
    duplicate account through normal signup (e.g. "Name@x.com" after
    "name@x.com" already existed -- SQLite's UNIQUE constraint is
    case-sensitive by default, so the DB allowed it), which then crashed
    login for either account with an uncaught MultipleObjectsReturned."""

    def setUp(self):
        User.objects.filter(email__iexact='dupetest@example.com').delete()
        self.existing_user = User.objects.create_user(
            email='dupetest@example.com', password='ExistingPass123!', first_name='E', last_name='U'
        )

    def test_signup_rejects_case_variant_of_existing_email(self):
        resp = self.client.post(reverse('signup:signup'), {
            'first_name': 'New', 'last_name': 'User', 'email': 'DupeTest@example.com',
            'country_code': '+977', 'password1': 'NewPass123!', 'password2': 'NewPass123!',
            'accept_policy': True,
        })
        self.assertEqual(resp.status_code, 200)  # form re-rendered with an error, not a redirect
        self.assertEqual(User.objects.filter(email__iexact='dupetest@example.com').count(), 1)

    def test_check_email_availability_is_case_insensitive(self):
        resp = self.client.get(reverse('signup:check_email'), {'email': 'DUPETEST@EXAMPLE.COM'})
        self.assertFalse(resp.json()['available'])

    def test_backend_does_not_crash_if_duplicate_already_exists(self):
        """Defense in depth: even if a case-variant duplicate somehow
        exists (this used to be creatable directly, e.g. from before the
        database-level constraint was added -- now that constraint makes
        it impossible to create through the ORM at all, so this test
        simulates the scenario via mocking instead of via real duplicate
        rows), logging in must not 500."""
        from accounts.backends import EmailBackend
        from django.contrib.auth import get_user_model
        backend = EmailBackend()

        with patch.object(get_user_model().objects, 'get', side_effect=get_user_model().MultipleObjectsReturned):
            with patch.object(get_user_model().objects, 'filter') as mock_filter:
                mock_filter.return_value.first.return_value = self.existing_user
                result = backend.authenticate(None, username='dupetest@example.com', password='ExistingPass123!')
        self.assertEqual(result, self.existing_user)


class LoginPageContentTests(TestCase):
    """Regression tests: the login page's 'Continue with Google' button
    was fake (just showed 'coming soon' after a fake spinner) despite
    real, working Google OAuth being available; and a logo-click Easter
    egg always autofilled credentials for a demo account that doesn't
    exist, guaranteeing a failed login for anyone who found it."""

    def test_google_button_targets_real_oauth_url(self):
        resp = self.client.get(reverse('accounts:login'))
        self.assertContains(resp, reverse('google_login'))

    def test_no_dead_demo_account_references(self):
        resp = self.client.get(reverse('accounts:login'))
        self.assertNotContains(resp, 'demo@travelease.com')
        self.assertNotContains(resp, 'demo123456')

    def test_no_fake_demo_login_bypass_in_page(self):
        """Regression test: the login form's own submit handler used to
        special-case the email 'test@example.com' by calling
        preventDefault() and showing a fake 'Demo login successful!'
        message instead of actually submitting the form -- so anyone
        logging in with that address (a common placeholder, and this
        app's own standard test fixture email) was never really
        authenticated even though the page told them they were."""
        resp = self.client.get(reverse('accounts:login'))
        self.assertNotContains(resp, 'Demo login successful')
        self.assertNotContains(resp, "email === 'test@example.com'")


class OpenRedirectTests(TestCase):
    """Regression tests for a real open-redirect vulnerability: login's
    'next' parameter was used in a raw redirect() with no validation that
    it actually pointed somewhere on this site. A crafted link like
    /accounts/login/?next=https://evil.com would redirect someone to an
    attacker's site immediately after a real, successful login on the
    genuine page -- a classic phishing vector."""

    def setUp(self):
        User.objects.filter(email='openredirect@example.com').delete()
        self.user = User.objects.create_user(
            email='openredirect@example.com', password='TestPass123!', first_name='O', last_name='R'
        )

    def test_external_next_url_is_rejected(self):
        client = Client()
        resp = client.post('/accounts/login/?next=https://evil-phishing-site.com/steal',
                            {'username': 'openredirect@example.com', 'password': 'TestPass123!'})
        self.assertNotEqual(resp.url, 'https://evil-phishing-site.com/steal')
        self.assertEqual(resp.url, reverse('web_interface:home'))

    def test_protocol_relative_next_url_is_rejected(self):
        client = Client()
        resp = client.post('/accounts/login/?next=//evil-phishing-site.com/steal',
                            {'username': 'openredirect@example.com', 'password': 'TestPass123!'})
        self.assertFalse(resp.url.startswith('//evil'))

    def test_legitimate_same_site_next_url_still_works(self):
        client = Client()
        resp = client.post('/accounts/login/?next=/accounts/dashboard/',
                            {'username': 'openredirect@example.com', 'password': 'TestPass123!'})
        self.assertEqual(resp.url, '/accounts/dashboard/')


class LoginFormNextParameterRoundTripTests(TestCase):
    """Regression test for a real bug found during audit: login_view
    correctly read and validated request.GET.get('next'), and the
    OpenRedirectTests above prove that logic works -- but they (and every
    other existing test) exercise it by constructing the POST request's
    URL directly with ?next=... already on it, which isn't what a real
    login does. A real login is: GET /accounts/login/?next=X (next lives
    in the query string), then the person fills in the *rendered form*
    and it POSTs -- and that form's action was a hardcoded URL with no
    query string and no hidden `next` field, so the value was silently
    dropped on every real submission. Since @login_required sends people
    here with ?next=<protected-page> for essentially every meaningful
    login, this meant nobody was ever actually returned to the page they
    were trying to reach.

    This test renders the actual login page (GET, with ?next= set),
    extracts the `next` value the real HTML form would submit (proving
    the template carries it forward at all), and posts using exactly
    that -- the same round trip a browser performs, not a hand-built
    URL."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='nexttest@example.com', password='TestPass123!', first_name='N', last_name='T'
        )

    def test_rendered_login_form_carries_next_through_to_a_real_submission(self):
        get_resp = self.client.get('/accounts/login/?next=/accounts/dashboard/')
        content = get_resp.content.decode()

        # The rendered form must actually contain the next value somewhere
        # a submission would pick it up -- proves the template isn't just
        # silently dropping it the way it used to.
        self.assertIn('name="next" value="/accounts/dashboard/"', content)

        # Simulate the real browser flow: submit exactly what that
        # rendered form would send, including its hidden next field.
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'nexttest@example.com', 'password': 'TestPass123!',
            'next': '/accounts/dashboard/',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, '/accounts/dashboard/')

    def test_rendered_login_form_has_no_next_field_when_none_was_requested(self):
        get_resp = self.client.get('/accounts/login/')
        content = get_resp.content.decode()
        self.assertIn('name="next" value=""', content)

        resp = self.client.post(reverse('accounts:login'), {
            'username': 'nexttest@example.com', 'password': 'TestPass123!', 'next': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('web_interface:home'))

    def test_next_survives_a_failed_attempt_in_between(self):
        """Regression test for a bug in the *first* fix for this: that fix
        only carried `next` through when the very first submission
        succeeded. It re-derived the hidden field's value from
        request.GET on every render, which is empty on the POST request
        that redisplays the form after a failed attempt (wrong password,
        or the rate limit) -- silently losing the destination on the very
        next retry. This mistypes the password once, confirms the
        redisplayed form's hidden `next` field still has the real value
        (not silently reset to empty), then retries with the correct
        password and confirms the redirect still lands on the original
        destination rather than falling back to home."""
        get_resp = self.client.get('/accounts/login/?next=/accounts/dashboard/')
        self.assertIn('name="next" value="/accounts/dashboard/"', get_resp.content.decode())

        # First attempt: wrong password, submitted with the real next
        # value exactly as the rendered form's hidden field would send it.
        failed_resp = self.client.post(reverse('accounts:login'), {
            'username': 'nexttest@example.com', 'password': 'wrong-password',
            'next': '/accounts/dashboard/',
        })
        self.assertEqual(failed_resp.status_code, 200)  # re-rendered, not redirected
        # The critical assertion: the redisplayed form must still carry
        # the real next value, not have silently reset it to empty.
        self.assertIn('name="next" value="/accounts/dashboard/"', failed_resp.content.decode())

        # Retry with the correct password, submitting exactly what that
        # redisplayed form's hidden field just proved it still contains.
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'nexttest@example.com', 'password': 'TestPass123!',
            'next': '/accounts/dashboard/',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, '/accounts/dashboard/')

    def test_next_survives_hitting_the_rate_limit_render_path_too(self):
        """The rate-limited early-return render is a separate code path
        from the invalid-credentials one above -- confirms it also
        carries next_value through rather than only the ordinary
        invalid-password path."""
        cache.clear()
        for i in range(5):
            self.client.post(reverse('accounts:login'), {
                'username': 'nexttest@example.com', 'password': f'wrong-{i}',
                'next': '/accounts/dashboard/',
            })
        blocked_resp = self.client.post(reverse('accounts:login'), {
            'username': 'nexttest@example.com', 'password': 'wrong-again',
            'next': '/accounts/dashboard/',
        })
        self.assertContains(blocked_resp, 'Too many failed')
        self.assertIn('name="next" value="/accounts/dashboard/"', blocked_resp.content.decode())
        cache.clear()


class PreferencesSettingsTests(TestCase):
    """Regression tests for a real gap found during audit: UserPreference
    has always been auto-created and displayed read-only on the profile
    page, but there was never anywhere in the product for a user to
    actually change it -- the only way was the Django admin, which real
    users don't have access to."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='prefs@example.com', password='TestPass123!', first_name='Pre', last_name='Fs',
        )

    def test_preferences_requires_login(self):
        resp = self.client.get(reverse('accounts:preferences'))
        self.assertEqual(resp.status_code, 302)

    def test_preferences_page_loads_for_authenticated_user(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('accounts:preferences'))
        self.assertEqual(resp.status_code, 200)

    def test_updating_preferences_actually_persists(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('accounts:preferences'), {
            'travel_style': 'luxury',
            'preferred_currency': 'eur',
            'preferred_language': 'FR',
            'profile_visibility': 'public',
            'marketing_emails': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        prefs = UserPreference.objects.get(user=self.user)
        self.assertEqual(prefs.travel_style, 'luxury')
        self.assertEqual(prefs.preferred_currency, 'EUR')  # normalized to uppercase
        self.assertEqual(prefs.preferred_language, 'fr')  # normalized to lowercase
        self.assertEqual(prefs.profile_visibility, 'public')
        self.assertTrue(prefs.marketing_emails)

    def test_profile_page_reflects_saved_preferences(self):
        self.client.force_login(self.user)
        self.client.post(reverse('accounts:preferences'), {
            'travel_style': 'backpacker', 'preferred_currency': 'USD',
            'preferred_language': 'en', 'profile_visibility': 'private',
        })
        resp = self.client.get(reverse('accounts:profile'))
        self.assertContains(resp, 'Backpacker')

    def test_push_and_email_notification_toggles_are_not_exposed(self):
        """These fields exist on the model but nothing in the app sends
        push or email notifications, so exposing a toggle for them would
        just be another dead 'for show' control."""
        self.client.force_login(self.user)
        resp = self.client.get(reverse('accounts:preferences'))
        self.assertNotContains(resp, 'name="push_notifications"')
        self.assertNotContains(resp, 'name="email_notifications"')


class PublicProfileTests(TestCase):
    """Regression tests for UserPreference.profile_visibility: the field
    existed and was admin-editable, but nothing in the app ever checked
    it -- there was no public profile page for it to gate access to."""

    def setUp(self):
        self.public_user = User.objects.create_user(
            email='public@example.com', password='TestPass123!', first_name='Pub', last_name='Lic',
        )
        UserPreference.objects.filter(user=self.public_user).update(profile_visibility='public')

        self.private_user = User.objects.create_user(
            email='private@example.com', password='TestPass123!', first_name='Priv', last_name='Ate',
        )
        # default profile_visibility is 'private'

    def test_public_profile_is_visible_to_anyone(self):
        resp = self.client.get(reverse('accounts:public_profile', args=[self.public_user.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Pub')
        self.assertNotContains(resp, 'This profile is private')

    def test_private_profile_shows_privacy_message_not_the_data(self):
        resp = self.client.get(reverse('accounts:public_profile', args=[self.private_user.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'This profile is private')

    def test_owner_can_always_see_their_own_profile_even_if_private(self):
        self.client.force_login(self.private_user)
        resp = self.client.get(reverse('accounts:public_profile', args=[self.private_user.id]))
        self.assertNotContains(resp, 'This profile is private')

    def test_unknown_user_id_returns_404(self):
        resp = self.client.get(reverse('accounts:public_profile', args=[999999]))
        self.assertEqual(resp.status_code, 404)


class RecentViewsSaveButtonStateTests(TestCase):
    """Regression test for a real bug found during audit: the Recent
    Views page's save/heart button always rendered as "not saved yet"
    regardless of whether the item was actually already saved -- but
    clicking it posted to the same toggle endpoint used everywhere else,
    so clicking it on an already-saved item would silently un-save it
    with no warning, because the button never reflected the true
    state."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='recentviews@example.com', password='TestPass123!', first_name='R', last_name='V',
        )
        self.client.force_login(self.user)
        RecentView.objects.create(
            user=self.user, item_type='destination', item_id=42,
            item_title='Everest Base Camp', item_url='/travel/42/',
        )

    def test_unsaved_item_shows_save_action(self):
        resp = self.client.get(reverse('web_interface:recent_views'))
        self.assertContains(resp, 'title="Save item"')
        self.assertNotContains(resp, 'title="Saved -- click to remove"')

    def test_already_saved_item_shows_unsave_action_not_save(self):
        SavedItem.objects.create(
            user=self.user, item_type='destination', item_id=42,
            item_title='Everest Base Camp', item_url='/travel/42/',
        )
        resp = self.client.get(reverse('web_interface:recent_views'))
        self.assertContains(resp, 'title="Saved -- click to remove"')
        self.assertNotContains(resp, 'title="Save item"')


class LoginRateLimitTests(TestCase):
    """Regression tests for a real security gap: settings.ACCOUNT_RATE_LIMITS
    declares a login-failure rate limit, but that only protects allauth's
    own login views -- this project uses a custom login_view that had zero
    brute-force protection. Confirmed 10 rapid failed attempts all went
    through unthrottled before this fix. Added a real rate limiter using
    Django's cache framework, matching the same '5 attempts / 5 minutes'
    policy already declared (but previously inert) in settings."""

    def setUp(self):
        cache.clear()
        User.objects.filter(email__iexact='ratelimit@example.com').delete()
        self.user = User.objects.create_user(
            email='ratelimit@example.com', password='CorrectPass123!', first_name='R', last_name='L'
        )

    def tearDown(self):
        cache.clear()

    def test_clean_first_try_login_is_never_blocked(self):
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'ratelimit@example.com', 'password': 'CorrectPass123!',
        })
        self.assertEqual(resp.status_code, 302)

    def test_blocks_after_five_failed_attempts(self):
        for i in range(5):
            self.client.post(reverse('accounts:login'), {
                'username': 'ratelimit@example.com', 'password': f'wrong-{i}',
            })
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'ratelimit@example.com', 'password': 'CorrectPass123!',
        })
        self.assertContains(resp, 'Too many failed')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_successful_login_resets_the_counter(self):
        self.client.post(reverse('accounts:login'), {'username': 'ratelimit@example.com', 'password': 'wrong1'})
        self.client.post(reverse('accounts:login'), {'username': 'ratelimit@example.com', 'password': 'wrong2'})
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'ratelimit@example.com', 'password': 'CorrectPass123!',
        })
        self.assertEqual(resp.status_code, 302)

    def test_rate_limit_is_isolated_per_account_not_just_per_ip(self):
        """A same-IP attacker hammering one account must not lock out a
        different, unrelated user logging in from that same IP."""
        User.objects.filter(email__iexact='ratelimit-victim@example.com').delete()
        victim = User.objects.create_user(
            email='ratelimit-victim@example.com', password='VictimPass123!', first_name='V', last_name='I'
        )
        for i in range(5):
            self.client.post(reverse('accounts:login'), {
                'username': 'ratelimit@example.com', 'password': f'wrong-{i}',
            })
        resp = self.client.post(reverse('accounts:login'), {
            'username': 'ratelimit-victim@example.com', 'password': 'VictimPass123!',
        })
        self.assertEqual(resp.status_code, 302)
        victim.delete()

    def test_concurrent_failed_attempts_dont_lose_increments(self):
        """Regression test for a real race condition found during audit:
        the failed-attempt counter used to be incremented by reading the
        count (cache.get) at the top of the view and writing it back
        (cache.set(value + 1)) later using that now-possibly-stale value.
        Under genuine concurrent attempts -- the exact scenario a
        brute-force limiter exists to stop -- two threads reading the
        same count and each writing back count+1 lose an increment,
        letting more attempts through than intended.

        This exercises the exact cache.add()+cache.incr() pattern the
        view now uses (see accounts/views.py's `else` branch on invalid
        credentials) directly under real thread concurrency, rather than
        through the full view -- going through the view would also hit
        its "already blocked" gate once the count passes
        LOGIN_RATE_LIMIT_MAX_ATTEMPTS, which is a separate, expected
        behavior and would make this specific assertion non-deterministic
        for values of num_attempts above that threshold."""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from accounts.views import LOGIN_RATE_LIMIT_WINDOW_SECONDS

        key = 'concurrency-test-counter'
        cache.delete(key)
        num_attempts = 25
        start_barrier = threading.Barrier(num_attempts)

        def atomic_increment():
            start_barrier.wait()  # maximize actual overlap between threads
            cache.add(key, 0, LOGIN_RATE_LIMIT_WINDOW_SECONDS)
            cache.incr(key)

        with ThreadPoolExecutor(max_workers=num_attempts) as executor:
            list(executor.map(lambda _: atomic_increment(), range(num_attempts)))

        self.assertEqual(cache.get(key), num_attempts)