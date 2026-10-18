from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        """
        Authenticate a user using either email or username (case-insensitive).
        First attempts to match by email, then falls back to username if no match.

        FOUND DURING AUDIT: this override reimplements ModelBackend's
        lookup-and-check-password logic (needed to add the email/username
        fallback), but had dropped ModelBackend's user_can_authenticate()
        call in every return path -- the check that enforces `is_active`.
        This backend runs first in settings.AUTHENTICATION_BACKENDS, and
        Django's authenticate() stops at the first backend that returns a
        user, so ModelBackend's own is_active check further down the list
        was never actually reached for a normal email/password login.
        Net effect: deactivating a user in the admin (is_active=False,
        exposed right there in signup/admin.py) silently did nothing --
        that account could still log in normally. Every return-a-user
        path below now goes through user_can_authenticate() first, same
        as the backend this is meant to extend.
        """
        if username is None or password is None:
            return None

        UserModel = get_user_model()

        # Try authenticating by email (case-insensitive)
        try:
            user = UserModel.objects.get(email__iexact=username)
            # A user account was actually found by email here -- whether or
            # not the password matches, that resolves this attempt. Falling
            # through to also try `username` as a *username* would just
            # re-run the identical string against a different field it was
            # never meant to match, for no real chance of success.
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
            return None
        except UserModel.DoesNotExist:
            pass  # Not an email at all -- proceed to the username check
        except UserModel.MultipleObjectsReturned:
            # Two accounts exist whose emails differ only by case (e.g.
            # "Name@x.com" and "name@x.com") -- SQLite's default UNIQUE
            # constraint is case-sensitive, so this was possible before
            # signup's duplicate-email check was made case-insensitive.
            # Rather than 500, fall back to an exact (case-sensitive)
            # match so login still works for whichever account matches
            # what was actually typed.
            user = UserModel.objects.filter(email=username).first()
            if user and user.check_password(password) and self.user_can_authenticate(user):
                return user
            return None

        # Fall back to username if email fails (case-insensitive)
        try:
            user = UserModel.objects.get(username__iexact=username)
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        except UserModel.DoesNotExist:
            return None
        except UserModel.MultipleObjectsReturned:
            user = UserModel.objects.filter(username=username).first()
            if user and user.check_password(password) and self.user_can_authenticate(user):
                return user
            return None

        return None

    def get_user(self, user_id):
        """Retrieve a user by their ID.

        FOUND DURING AUDIT (a second, deeper gap in the same fix
        documented on authenticate() above): Django's own ModelBackend.
        get_user() also calls user_can_authenticate() before returning --
        checking is_active isn't just a login-time concern, because
        AuthenticationMiddleware calls get_user() on the backend recorded
        in the session (BACKEND_SESSION_KEY) on every single request to
        rehydrate request.user, not just at login. This override had
        dropped that check entirely, so it didn't just fail to block a
        fresh login for a deactivated account (the bug already fixed
        above) -- it also meant an account deactivated *while already
        logged in* kept full, working access for the rest of its existing
        session, since every subsequent request's session rehydration
        would still happily return the (now-deactivated) user. Restored
        to match ModelBackend's actual contract.
        """
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None