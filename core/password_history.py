"""Records password hashes to core.PasswordHistory so
core.validators.PasswordHistoryValidator can block reusing a recent one.

Call `record(user, password_hash)` at every point a password is actually
saved to the database — not just in the password-change flow. As of this
writing that's: accounts.otp.complete_registration (registration),
accounts.team_views.team_join (team invite acceptance),
homepage.views.PasswordChangeVerifyView (change-password), and
homepage.views.ThrottledPasswordResetConfirmView (forgot-password reset).
"""
import logging

from django.conf import settings
from django.contrib.auth.hashers import check_password

from .models import PasswordHistory

logger = logging.getLogger(__name__)

# How many of a user's most recent passwords are kept and checked against.
KEEP_LAST = getattr(settings, 'PASSWORD_HISTORY_LIMIT', 5)


def recent_hashes(user, limit=KEEP_LAST):
    """The user's most recent password hashes, newest first."""
    if user is None or not user.pk:
        return []
    return list(PasswordHistory.objects.filter(user=user).order_by('-created_at')[:limit].values_list('password_hash', flat=True))


def was_recently_used(user, raw_password, limit=KEEP_LAST):
    """True if `raw_password` matches any of the user's last `limit`
    passwords. Safe to call before the user has any history (registration):
    returns False since there's nothing to compare against yet."""
    return any(check_password(raw_password, old_hash) for old_hash in recent_hashes(user, limit))


def record(user, password_hash):
    """Saves `password_hash` (already hashed — never a raw password) as the
    newest entry for `user`, then trims older rows beyond KEEP_LAST. Never
    raises: a failure to record history must not block the password change
    it's describing — it only means that one change won't be checked against
    later, which is logged loudly so it can be noticed."""
    try:
        PasswordHistory.objects.create(user=user, password_hash=password_hash)
        keep_ids = list(
            PasswordHistory.objects.filter(user=user).order_by('-created_at').values_list('id', flat=True)[:KEEP_LAST]
        )
        PasswordHistory.objects.filter(user=user).exclude(id__in=keep_ids).delete()
    except Exception:
        logger.exception("Could not record password history for user %s", getattr(user, 'pk', None))
