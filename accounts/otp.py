"""Email OTPs: verifying the email at registration (accounts.views.register,
verify_email), and confirming a signed-in user's password change
(homepage.views.ThrottledPasswordChangeView / PasswordChangeVerifyView). A PendingRegistration holds the signup data for an email that
hasn't been confirmed yet — no User row is created until the code is
verified, so an abandoned or failed signup never ends up in the users table.
Codes are hashed at rest with Django's password hasher — the same reasoning
as a password: short-lived, but no reason to keep it in the clear in the
database either.
"""
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from core.emails import send_template_email
from .models import EmailVerification, PendingRegistration

CODE_LENGTH = 6
TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_LIMIT = 3
RESEND_WINDOW_SECONDS = 10 * 60


def _generate_code():
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def _resend_key(pending):
    return f"otp-resend:{pending.pk}"


def resend_allowed(pending):
    return cache.get(_resend_key(pending), 0) < RESEND_LIMIT


def start_registration(form):
    """Stashes a validated UserRegistrationForm's data as a PendingRegistration
    (no User row yet) and sends the first code. `form` must already be
    valid — its instance carries the hashed password set during cleaning."""
    instance = form.save(commit=False)
    pending = PendingRegistration.objects.create(
        email=instance.email, username=instance.username,
        first_name=instance.first_name, last_name=instance.last_name,
        password=instance.password, role=instance.role,
    )
    issue_and_send(pending)
    return pending


def password_matches(pending, raw_password):
    return check_password(raw_password or '', pending.password)


def issue_and_send(pending):
    """Creates a new OTP for `pending` and emails it. Every call — the first
    one from start_registration(), verify_email's GET topping up an expired
    code, or the explicit "Resend" button — counts against the resend
    limit; callers check resend_allowed() first."""
    code = _generate_code()
    EmailVerification.objects.create(
        pending_registration=pending, code_hash=make_password(code),
        expires_at=timezone.now() + timedelta(minutes=TTL_MINUTES),
    )
    key = _resend_key(pending)
    cache.add(key, 0, timeout=RESEND_WINDOW_SECONDS)
    cache.incr(key)
    name = pending.first_name or pending.username
    send_template_email(
        pending.email,
        'emails/otp_verification_subject.txt', 'emails/otp_verification.txt',
        {'name': name, 'code': code, 'ttl_minutes': TTL_MINUTES},
        html_template='emails/otp_verification.html',
    )
    return code


def has_pending_code(pending):
    return EmailVerification.objects.filter(
        pending_registration=pending, consumed_at__isnull=True, expires_at__gt=timezone.now(),
    ).exists()


def verify(pending, submitted_code):
    """Checks `submitted_code` against the latest OTP issued to `pending`
    that hasn't been consumed. Returns (ok, reason); reason is one of
    None, 'no_pending', 'expired', 'locked', 'mismatch'."""
    otp = EmailVerification.objects.filter(
        pending_registration=pending, consumed_at__isnull=True,
    ).order_by('-created_at', '-pk').first()  # pk: codes issued in the same instant
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


def complete_registration(pending):
    """Creates the real User now that the OTP is confirmed — the one place
    a PendingRegistration turns into a users-table row. The pending row (and
    its OTP history) is deleted so the same signup can't be replayed."""
    from core.models import User

    user = User(
        username=pending.username, email=pending.email,
        first_name=pending.first_name, last_name=pending.last_name,
        role=pending.role, email_verified=True,
    )
    user.password = pending.password  # already hashed by UserRegistrationForm
    user.save()
    pending.delete()
    from core.password_history import record
    record(user, user.password)
    return user


# --- Confirming a password change ------------------------------------------------
# The new password (hashed) and the code (hashed) wait in the user's own
# session, which is stored server-side; the account's password only changes
# once the code from their inbox is entered, so a borrowed session or a
# shoulder-surfed password isn't enough to take the account over.

PASSWORD_CHANGE_SESSION_KEY = 'password_change_pending'


def _password_resend_key(user):
    return f"otp-resend:password:{user.pk}"


def password_change_resend_allowed(user):
    return cache.get(_password_resend_key(user), 0) < RESEND_LIMIT


def pending_password_change(request):
    pending = request.session.get(PASSWORD_CHANGE_SESSION_KEY)
    if not pending or pending.get('user_id') != request.user.pk:
        return None
    return pending


def start_password_change(request, new_password):
    """Holds `new_password` (already validated) for this session and emails
    the first code. Starting again replaces anything pending."""
    request.session[PASSWORD_CHANGE_SESSION_KEY] = {'user_id': request.user.pk, 'password': make_password(new_password)}
    send_password_change_code(request)


def send_password_change_code(request):
    """A fresh code for the pending change (the old one stops working).
    Counts against the resend limit; callers check it first."""
    pending = pending_password_change(request)
    if pending is None:
        return None
    code = _generate_code()
    pending.update(code_hash=make_password(code), attempts=0,
                   expires_at=(timezone.now() + timedelta(minutes=TTL_MINUTES)).timestamp())
    request.session[PASSWORD_CHANGE_SESSION_KEY] = pending
    key = _password_resend_key(request.user)
    cache.add(key, 0, timeout=RESEND_WINDOW_SECONDS)
    cache.incr(key)
    user = request.user
    send_template_email(
        user.email, 'emails/password_change_otp_subject.txt', 'emails/password_change_otp.txt',
        {'name': user.first_name or user.email, 'code': code, 'ttl_minutes': TTL_MINUTES},
    )
    return code


def verify_password_change(request, submitted_code):
    """Checks the code for the pending change. Returns (new password hash
    or None, reason); reason is None, 'no_pending', 'expired', 'locked' or
    'mismatch'. A correct code ends the pending change."""
    pending = pending_password_change(request)
    if pending is None or 'code_hash' not in pending:
        return None, 'no_pending'
    if pending['attempts'] >= MAX_ATTEMPTS:
        return None, 'locked'
    if pending['expires_at'] <= timezone.now().timestamp():
        return None, 'expired'
    if check_password(submitted_code or '', pending['code_hash']):
        del request.session[PASSWORD_CHANGE_SESSION_KEY]
        return pending['password'], None
    pending['attempts'] += 1
    request.session[PASSWORD_CHANGE_SESSION_KEY] = pending
    return None, 'mismatch'
