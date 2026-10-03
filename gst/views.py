"""POST /api/gst/verify/ — the registration page's "Verify" button."""
import json

from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .forms import GSTINForm
from .models import GSTVerification
from .services.verification import GSTVerificationService

# Lookups cost money with a real provider, so cap them per IP.
RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 60

# The session key holding this browser's latest successful verification;
# accounts.services.company_registration is the only reader.
SESSION_KEY = 'gst_verification_id'

HTTP_STATUS = {
    GSTVerification.Status.SUCCESS: 200,
    GSTVerification.Status.INVALID: 404,
    GSTVerification.Status.INACTIVE: 422,
}
FAILED_HTTP_STATUS = {'GST_PROVIDER_TIMEOUT': 504, 'GST_PROVIDER_RATE_LIMITED': 429}


def _error(code, message, status):
    return JsonResponse({'success': False, 'code': code, 'message': message}, status=status)


def _rate_limited(request):
    key = f"gst-verify-throttle:{request.META.get('REMOTE_ADDR', 'unknown')}"
    count = cache.get(key, 0)
    if count >= RATE_LIMIT:
        return True
    cache.set(key, count + 1, timeout=RATE_WINDOW_SECONDS)
    return False


@login_not_required
@require_POST
def verify_gstin(request):
    """Body: {"gstin": "..."}. Only for someone partway through sign-up
    (email verified, company step pending), so an anonymous visitor can't
    spend provider lookups.

    On success the verification is remembered in the session; the sign-up
    step then builds the Company from that server-side record, never from
    anything the browser sends back."""
    from accounts.services.company_registration import registration_conflict, signing_up_role

    if not signing_up_role(request):
        return _error('SIGN_UP_REQUIRED', "Start by creating your account.", 403)
    if _rate_limited(request):
        return _error('RATE_LIMITED', "Too many verification attempts. Please try again in a minute.", 429)
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _error('INVALID_REQUEST', "Invalid request.", 400)
    form = GSTINForm(payload if isinstance(payload, dict) else {})
    if not form.is_valid():
        return _error('INVALID_GSTIN_FORMAT', form.errors['gstin'][0], 400)
    gstin = form.cleaned_data['gstin']

    request.session.pop(SESSION_KEY, None)
    conflict = registration_conflict(request, gstin)
    if conflict:
        return _error('GSTIN_ALREADY_REGISTERED', conflict, 409)

    verification = GSTVerificationService().verify(gstin)
    if verification.is_success:
        request.session[SESSION_KEY] = verification.pk
    status = HTTP_STATUS.get(verification.status) or FAILED_HTTP_STATUS.get(verification.error_code, 503)
    return JsonResponse(verification.as_payload(), status=status)
