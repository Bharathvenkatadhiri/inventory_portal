"""Email-verification OTPs for the registration flow (accounts.views.register,
verify_email). Codes are hashed at rest with Django's password hasher — the
same reasoning as a password: short-lived, but no reason to keep it in the
clear in the database either.
"""
import random
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from core.emails import send_template_email
from .models import EmailVerification

CODE_LENGTH = 6
TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_LIMIT = 3
RESEND_WINDOW_SECONDS = 10 * 60


def _generate_code():
    return f"{random.randint(0, 10 ** CODE_LENGTH - 1):0{CODE_LENGTH}d}"


def _resend_key(user):
    return f"otp-resend:{user.pk}"


def resend_allowed(user):
    return cache.get(_resend_key(user), 0) < RESEND_LIMIT


def issue_and_send(user):
    """Creates a new OTP for `user` and emails it. Every call — the first
    one from register(), verify_email's GET topping up an expired code, or
    the explicit "Resend" button — counts against the resend limit;
    callers check resend_allowed() first."""
    code = _generate_code()
    EmailVerification.objects.create(
        user=user, code_hash=make_password(code), expires_at=timezone.now() + timedelta(minutes=TTL_MINUTES),
    )
    key = _resend_key(user)
    cache.add(key, 0, timeout=RESEND_WINDOW_SECONDS)
    cache.incr(key)
    name = user.get_short_name() or user.first_name or user.username
    send_template_email(
        user.email,
        'emails/otp_verification_subject.txt', 'emails/otp_verification.txt',
        {'name': name, 'code': code, 'ttl_minutes': TTL_MINUTES},
        html_template='emails/otp_verification.html',
    )
    return code


def has_pending_code(user):
    return EmailVerification.objects.filter(user=user, consumed_at__isnull=True, expires_at__gt=timezone.now()).exists()


def verify(user, submitted_code):
    """Checks `submitted_code` against the latest OTP issued to `user`
    that hasn't been consumed. Returns (ok, reason); reason is one of
    None, 'no_pending', 'expired', 'locked', 'mismatch'."""
    otp = EmailVerification.objects.filter(user=user, consumed_at__isnull=True).order_by('-created_at').first()
    if otp is None:
        return False, 'no_pending'
    if otp.attempts >= MAX_ATTEMPTS:
        return False, 'locked'
    if otp.expires_at <= timezone.now():
        return False, 'expired'
    if check_password(submitted_code or '', otp.code_hash):
        otp.consumed_at = timezone.now()
        otp.save(update_fields=['consumed_at'])
        return True, None
    otp.attempts += 1
    otp.save(update_fields=['attempts'])
    return False, 'mismatch'
