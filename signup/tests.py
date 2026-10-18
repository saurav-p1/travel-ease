from django.test import TestCase, Client
from django.urls import reverse
from django.db import IntegrityError, transaction
from django.core.cache import cache
from unittest.mock import patch
from .models import CustomUser
from .forms import SignupForm


class SignupFormTests(TestCase):
    def valid_data(self, **overrides):
        data = {
            'first_name': 'Jane',
            'last_name': 'Doe',
            'email': 'jane@example.com',
            'country_code': '+977',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'accept_policy': True,
        }
        data.update(overrides)
        return data

    def test_valid_form_creates_user_with_email_as_username(self):
        form = SignupForm(data=self.valid_data())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.email, 'jane@example.com')
        self.assertTrue(user.check_password('StrongPass123!'))

    def test_duplicate_email_rejected(self):
        CustomUser.objects.create_user(email='jane@example.com', password='x', first_name='A', last_name='B')
        form = SignupForm(data=self.valid_data())
        self.assertFalse(form.is_valid())

    def test_mismatched_passwords_rejected(self):
        form = SignupForm(data=self.valid_data(password2='SomethingElse123!'))
        self.assertFalse(form.is_valid())


class SignupViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_signup_page_loads(self):
        resp = self.client.get(reverse('signup:signup'))
        self.assertEqual(resp.status_code, 200)

    def test_successful_signup_logs_user_in_and_redirects(self):
        resp = self.client.post(reverse('signup:signup'), {
            'first_name': 'Jane', 'last_name': 'Doe', 'email': 'jane2@example.com',
            'country_code': '+977',
            'password1': 'StrongPass123!', 'password2': 'StrongPass123!',
            'accept_policy': True,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(CustomUser.objects.filter(email='jane2@example.com').exists())

    def test_check_email_availability(self):
        CustomUser.objects.create_user(email='taken@example.com', password='x', first_name='A', last_name='B')
        resp = self.client.get(reverse('signup:check_email'), {'email': 'taken@example.com'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['available'])

        resp = self.client.get(reverse('signup:check_email'), {'email': 'free@example.com'})
        self.assertTrue(resp.json()['available'])


class AjaxSignupEndToEndTests(TestCase):
    """Regression tests for the most severe bug found in this audit: the
    signup page's actual HTML form never sent its data to the server at
    all -- the JS called e.preventDefault(), ran client-side validation,
    then just faked a loading spinner and a fake "Account created
    successfully!" message via setTimeout(). No fetch()/XHR call existed.
    The backend (ajax_signup, get_csrf_token) was fully correct and
    already tested at the view level, but nothing on the actual page a
    real user interacts with ever called it. These tests simulate the
    exact flow the (now fixed) JS performs, with real CSRF enforcement,
    to prove signup genuinely creates an account end to end."""

    def setUp(self):
        CustomUser.objects.filter(email__iexact='ajaxflow@example.com').delete()

    def _signup_payload(self, **overrides):
        payload = {
            'first_name': 'Ajax', 'last_name': 'Flow', 'email': 'ajaxflow@example.com',
            'country_code': '+977', 'phone_number': '', 'date_of_birth': '',
            'password1': 'StrongPass123!', 'password2': 'StrongPass123!',
            'accept_policy': 'on', 'marketing_emails': '',
        }
        payload.update(overrides)
        return payload

    def test_full_ajax_signup_flow_with_real_csrf_enforcement(self):
        client = Client(enforce_csrf_checks=True)
        csrf_resp = client.get(reverse('signup:csrf_token'))
        self.assertEqual(csrf_resp.status_code, 200)
        token = csrf_resp.json()['csrfToken']

        resp = client.post(reverse('signup:ajax_signup'), self._signup_payload(), HTTP_X_CSRFTOKEN=token)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['redirect_url'], reverse('signup:success'))

        self.assertTrue(CustomUser.objects.filter(email='ajaxflow@example.com').exists())

        # Logged in immediately after signup, as the JS relies on
        dashboard_resp = client.get(reverse('accounts:dashboard'))
        self.assertEqual(dashboard_resp.status_code, 200)

    def test_ajax_signup_without_csrf_token_is_rejected(self):
        """Confirms CSRF is genuinely being enforced, not silently bypassed."""
        client = Client(enforce_csrf_checks=True)
        resp = client.post(reverse('signup:ajax_signup'), self._signup_payload())
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(CustomUser.objects.filter(email='ajaxflow@example.com').exists())

    def test_ajax_signup_duplicate_email_returns_field_error(self):
        CustomUser.objects.create_user(email='ajaxflow@example.com', password='x', first_name='A', last_name='B')
        client = Client(enforce_csrf_checks=True)
        token = client.get(reverse('signup:csrf_token')).json()['csrfToken']
        resp = client.post(reverse('signup:ajax_signup'), self._signup_payload(), HTTP_X_CSRFTOKEN=token)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertIn('email', data['errors'])


class DatabaseLevelEmailUniquenessTests(TestCase):
    """Regression tests for a race-condition gap: the form-level
    duplicate-email check (case-insensitive) alone is a check-then-save
    pattern, so two near-simultaneous signups with case-variant emails
    could theoretically both pass validation before either commits.
    Added a database-level case-insensitive UniqueConstraint to close
    this atomically, plus defensive IntegrityError handling in both
    signup views so that even the narrowest remaining race window (after
    validation passes but before .save() commits) degrades gracefully
    instead of crashing with an uncaught 500."""

    def setUp(self):
        CustomUser.objects.filter(email__iexact='dbrace@example.com').delete()

    def test_database_constraint_blocks_case_variant_duplicates_directly(self):
        """Bypassing the form entirely (simulating the DB being the last
        line of defense), the constraint itself must still block this."""
        CustomUser.objects.create_user(email='DbRace@example.com', password='x', first_name='A', last_name='B')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CustomUser.objects.create_user(email='dbrace@example.com', password='y', first_name='C', last_name='D')

    def test_signup_view_handles_constraint_violation_gracefully(self):
        """Simulates a genuine race: the form's own check passes (patched
        to a no-op, as a real race would look identical to the check
        succeeding), but the database rejects it. Must not 500."""
        CustomUser.objects.create_user(email='dbrace@example.com', password='x', first_name='A', last_name='B')

        def fake_clean_email(self):
            return self.cleaned_data.get('email')

        payload = {
            'first_name': 'Race', 'last_name': 'View', 'email': 'DbRace@example.com',
            'country_code': '+977', 'password1': 'StrongPass123!', 'password2': 'StrongPass123!',
            'accept_policy': True,
        }
        with patch('signup.forms.SignupForm.clean_email', fake_clean_email):
            resp = self.client.post(reverse('signup:signup'), payload)
        self.assertEqual(resp.status_code, 200)  # re-rendered with an error, not a 500
        self.assertEqual(CustomUser.objects.filter(email__iexact='dbrace@example.com').count(), 1)

    def test_ajax_signup_handles_constraint_violation_gracefully(self):
        CustomUser.objects.create_user(email='dbrace@example.com', password='x', first_name='A', last_name='B')

        def fake_clean_email(self):
            return self.cleaned_data.get('email')

        payload = {
            'first_name': 'Race', 'last_name': 'View', 'email': 'DbRace@example.com',
            'country_code': '+977', 'phone_number': '', 'date_of_birth': '',
            'password1': 'StrongPass123!', 'password2': 'StrongPass123!',
            'accept_policy': 'on', 'marketing_emails': '',
        }
        client = Client(enforce_csrf_checks=True)
        token = client.get(reverse('signup:csrf_token')).json()['csrfToken']
        with patch('signup.forms.SignupForm.clean_email', fake_clean_email):
            resp = client.post(reverse('signup:ajax_signup'), payload, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(resp.status_code, 200)  # graceful JSON error, not a 500
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertEqual(CustomUser.objects.filter(email__iexact='dbrace@example.com').count(), 1)


class SignupRateLimitTests(TestCase):
    """Regression tests: signup had zero protection against automated
    mass account creation. Confirmed 10 rapid signups from one source all
    succeeded with no throttling before this fix. Keyed on IP alone
    (unlike login's limiter, which is keyed on IP+email) since each
    signup attempt uses a different email by definition."""

    def setUp(self):
        cache.clear()
        CustomUser.objects.filter(email__startswith='ratelimitsignup').delete()

    def tearDown(self):
        cache.clear()

    def _payload(self, n):
        return {
            'first_name': 'Test', 'last_name': f'User{n}', 'email': f'ratelimitsignup{n}@example.com',
            'country_code': '+977', 'password1': 'TestPass123!', 'password2': 'TestPass123!',
            'accept_policy': True,
        }

    def test_clean_single_signup_unaffected(self):
        resp = self.client.post(reverse('signup:signup'), self._payload(0))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(CustomUser.objects.filter(email='ratelimitsignup0@example.com').exists())

    def test_signup_view_blocks_after_five_attempts_without_crashing(self):
        for i in range(5):
            self.client.post(reverse('signup:signup'), self._payload(i))
        resp = self.client.post(reverse('signup:signup'), self._payload(5))
        self.assertEqual(resp.status_code, 200)  # re-rendered with a message, not a 500
        self.assertContains(resp, 'Too many signup attempts')
        self.assertFalse(CustomUser.objects.filter(email='ratelimitsignup5@example.com').exists())
        self.assertEqual(CustomUser.objects.filter(email__startswith='ratelimitsignup').count(), 5)

    def test_ajax_signup_blocks_after_five_attempts(self):
        client = Client(enforce_csrf_checks=True)
        for i in range(5):
            token = client.get(reverse('signup:csrf_token')).json()['csrfToken']
            client.post(reverse('signup:ajax_signup'), self._payload(i), HTTP_X_CSRFTOKEN=token)
        token = client.get(reverse('signup:csrf_token')).json()['csrfToken']
        resp = client.post(reverse('signup:ajax_signup'), self._payload(5), HTTP_X_CSRFTOKEN=token)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertEqual(CustomUser.objects.filter(email__startswith='ratelimitsignup').count(), 5)

    def test_concurrent_signup_attempts_dont_lose_increments(self):
        """Regression test for a real race condition found during audit:
        _signup_rate_limited used to read the attempt count (cache.get)
        and write it back (cache.set(value + 1)) using that
        now-possibly-stale value -- the same race already fixed in
        accounts.views.login_view's limiter. Under genuine concurrent
        signup attempts (e.g. a scripted signup-spam run -- exactly what
        this limiter exists to stop), two requests reading the same
        stale count and each writing back count+1 lose an increment,
        letting more attempts through per window than intended.

        Exercises the exact cache.add()+cache.incr() pattern
        _signup_rate_limited now uses directly under real thread
        concurrency, rather than through the full view -- going through
        the view would also hit the "already blocked" early-return once
        the count passes SIGNUP_RATE_LIMIT_MAX_ATTEMPTS, which would make
        a view-level assertion non-deterministic above that threshold."""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from signup.views import SIGNUP_RATE_LIMIT_WINDOW_SECONDS

        key = 'signup-concurrency-test-counter'
        cache.delete(key)
        num_attempts = 25
        start_barrier = threading.Barrier(num_attempts)

        def atomic_increment():
            start_barrier.wait()  # maximize actual overlap between threads
            cache.add(key, 0, SIGNUP_RATE_LIMIT_WINDOW_SECONDS)
            cache.incr(key)

        with ThreadPoolExecutor(max_workers=num_attempts) as executor:
            list(executor.map(lambda _: atomic_increment(), range(num_attempts)))

        self.assertEqual(cache.get(key), num_attempts)
