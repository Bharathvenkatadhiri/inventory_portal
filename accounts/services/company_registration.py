"""The company step of sign-up: turning this session's GST verification
into a Company. The browser only ever says "use the GSTIN I verified" and
offers a display name; legal name, address and GST status always come from
the server-side GSTVerification record (gst.views.verify_gstin), so the
step can't be bypassed or fed a made-up identity.

Legal name vs display name: the GST legal name is the company's official
name (invoices, documents). The display name is what the person typed for
how the company appears on MakeSetu, defaulting to the GST trade name.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from gst.models import GSTVerification
from gst.services.verification import GSTVerificationService
from gst.views import SESSION_KEY

from ..models import Company

User = get_user_model()

# The profile relation a company may only have one of, per role. A company
# may hold one buyer and one supplier account.
_PROFILE_FOR_ROLE = {'consumer': 'consumer_profile', 'manufacturer': 'manufacturer_profile'}


def signing_up_role(request):
    """'consumer' or 'manufacturer' when this session is at the company step
    of sign-up (email verified, no profile or team yet), else None."""
    user = User.objects.filter(
        pk=request.session.get('session_user_id'), team_membership__isnull=True,
        manufacturerprofile__isnull=True, consumerprofile__isnull=True,
    ).only('role').first()
    if user is None:
        return None
    return 'manufacturer' if user.role == 'manufacturer' else 'consumer'


def registration_conflict(request, gstin, role=None):
    """Why this GSTIN can't be used for the account being created, or None."""
    role = role or signing_up_role(request)
    company = Company.objects.filter(gstin=gstin).first()
    if company is not None and hasattr(company, _PROFILE_FOR_ROLE.get(role, 'consumer_profile')):
        kind = 'manufacturer' if role == 'manufacturer' else 'buyer'
        return f"This GSTIN is already registered with an existing {kind} account."
    return None


def session_verification(request):
    """This session's successful, recent GST verification, or None."""
    max_age = timedelta(minutes=settings.GST_VERIFICATION_MAX_AGE_MINUTES)
    return GSTVerification.objects.filter(
        pk=request.session.get(SESSION_KEY), status=GSTVerification.Status.SUCCESS,
        created_at__gte=timezone.now() - max_age,
    ).first()


def forget_session_verification(request):
    """One verification, one registration: it can't be replayed."""
    request.session.pop(SESSION_KEY, None)


def display_name_from(posted, verification):
    name = ' '.join((posted or '').split())
    return name or verification.trade_name or verification.legal_name


def register_company(verification, display_name):
    """The Company for a successful verification: created, or refreshed if
    the GSTIN is already on MakeSetu (e.g. a supplier now signing up as a
    buyer too). Call inside the transaction that creates the profile."""
    company = Company.objects.select_for_update().filter(gstin=verification.gstin).first()
    if company is None:
        company = Company(name=display_name[:255])
    elif not company.name:
        company.name = display_name[:255]
    GSTVerificationService.apply_to_company(verification, company)
    return company
