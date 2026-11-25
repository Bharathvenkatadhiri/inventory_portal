"""Customers and suppliers: finding companies on MakeSetu, connecting with
them, and the relationship's life afterwards.

    request ─▶ invited          (we buy from them: the supplier accepts)
            ─▶ pending_approval (we sell to them: the buyer approves us as a vendor)
            ─▶ active           (they aren't on MakeSetu yet: nobody there to ask)
    invited / pending_approval ─accept─▶ active   ─decline / cancel─▶ (removed)
    active ─block─▶ blocked ─unblock (same side)─▶ active

A company that isn't on MakeSetu yet is added by its GSTIN: the GST lookup
creates an unclaimed accounts.Company (no members). When that company
registers with the same GSTIN, sign-up reuses the row
(accounts.services.company_registration), so it finds these relationships
already there.
"""
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company
from core.tasks import queue_template_email
from gst.forms import GSTINForm
from gst.services.verification import GSTVerificationService

from .models import BusinessRelationship, Membership

SUPPLIER, CUSTOMER = 'supplier', 'customer'   # what the partner is to us
SEARCH_LIMIT = 20
GST_LOOKUPS_PER_HOUR = 30
JOIN_EMAILS_PER_DAY = 50

Status = BusinessRelationship.Status


class PartnerError(Exception):
    """A request that can't be carried out; the message says why."""


def on_network(company):
    """An ERP organization people work in (not unclaimed, not marketplace-only)."""
    return Membership.objects.filter(company=company, is_active=True).exists()


def _display_name(company):
    return company.name or company.trade_name or company.legal_name


def search(company, query):
    """Organizations on MakeSetu matching `query` by name, or by GSTIN. By
    name, only findable ones (Company.findable); an exact GSTIN finds any
    organization, since whoever has it knows the company already."""
    query = ' '.join((query or '').split())
    if len(query) < 3:
        return []
    orgs = Company.objects.filter(memberships__is_active=True).exclude(pk=company.pk).distinct()
    gstin = query.replace(' ', '').upper()
    if GSTINForm({'gstin': gstin}).is_valid():
        return list(orgs.filter(gstin=gstin)[:1])
    return list(orgs.filter(findable=True).filter(
        Q(name__icontains=query) | Q(legal_name__icontains=query) | Q(trade_name__icontains=query),
    ).order_by('legal_name')[:SEARCH_LIMIT])


def relationship_between(company, other):
    return BusinessRelationship.objects.filter(
        Q(buyer=company, supplier=other) | Q(buyer=other, supplier=company))


def _side_enabled(company, relation):
    if relation == SUPPLIER and not company.buying_enabled:
        raise PartnerError("Switch on buying in your company settings to add suppliers.")
    if relation == CUSTOMER and not company.selling_enabled:
        raise PartnerError("Switch on selling in your company settings to add customers.")


def _lookup_unclaimed(company, gstin):
    """The Company for a GSTIN not yet on MakeSetu, from the GST records."""
    key = f'partner-gst-lookups:{company.pk}'
    cache.add(key, 0, timeout=60 * 60)
    if cache.incr(key) > GST_LOOKUPS_PER_HOUR:
        raise PartnerError("Too many GSTIN lookups in the last hour. Please try again later.")
    verification = GSTVerificationService().verify(gstin)
    if not verification.is_success:
        raise PartnerError(verification.as_payload().get('message') or "We couldn't verify that GSTIN.")
    other = Company(name=(verification.trade_name or verification.legal_name)[:255])
    GSTVerificationService.apply_to_company(verification, other)
    return other


@transaction.atomic
def connect(company, user, relation, *, target=None, gstin='', email=''):
    """Starts a relationship where the other company is our `relation`
    (SUPPLIER or CUSTOMER), found by search (`target`) or by `gstin`.
    Returns (relationship, joined_email_sent)."""
    if relation not in (SUPPLIER, CUSTOMER):
        raise PartnerError("Choose supplier or customer.")
    _side_enabled(company, relation)
    if target is None:
        form = GSTINForm({'gstin': (gstin or '').replace(' ', '').upper()})
        if not form.is_valid():
            raise PartnerError(form.errors['gstin'][0])
        gstin = form.cleaned_data['gstin']
        target = Company.objects.filter(gstin=gstin).first() or _lookup_unclaimed(company, gstin)
    if target.pk == company.pk:
        raise PartnerError("That's your own company.")

    buyer, supplier = (company, target) if relation == SUPPLIER else (target, company)
    existing = BusinessRelationship.objects.filter(buyer=buyer, supplier=supplier).first()
    if existing is not None:
        raise PartnerError(f"{_display_name(target)} is already on your list ({existing.get_status_display().lower()}).")

    networked = on_network(target)
    if not networked:
        status = Status.ACTIVE
    else:
        status = Status.INVITED if relation == SUPPLIER else Status.PENDING_APPROVAL
    try:
        with transaction.atomic():
            relationship = BusinessRelationship.objects.create(
                buyer=buyer, supplier=supplier, initiated_by=company, status=status, created_by=user,
                activated_at=timezone.now() if status == Status.ACTIVE else None,
            )
    except IntegrityError:
        raise PartnerError(f"{_display_name(target)} is already on your list.")

    joined_email_sent = False
    if networked:
        _notify_request(relationship, company, target)
    elif email:
        joined_email_sent = _invite_to_join(company, target, relation, email)
    return relationship, joined_email_sent


def _their_side(relationship, company):
    return relationship.supplier if relationship.buyer_id == company.pk else relationship.buyer


def can_respond(relationship, company):
    """Whether `company` is the side being asked (it didn't start it)."""
    return relationship.status in (Status.INVITED, Status.PENDING_APPROVAL) and relationship.initiated_by_id != company.pk


@transaction.atomic
def accept(relationship, company, user):
    relationship = BusinessRelationship.objects.select_for_update().get(pk=relationship.pk)
    if not can_respond(relationship, company):
        raise PartnerError("There's nothing to accept here.")
    _side_enabled(company, CUSTOMER if relationship.supplier_id == company.pk else SUPPLIER)
    relationship.status = Status.ACTIVE
    relationship.activated_at = timezone.now()
    relationship.save(update_fields=['status', 'activated_at', 'updated_at'])
    return relationship


def decline(relationship, company, user):
    """The asked side says no, or the side that asked withdraws: the request
    goes away (either can send a new one later)."""
    if relationship.status not in (Status.INVITED, Status.PENDING_APPROVAL):
        raise PartnerError("Only a request that's still waiting can be declined or withdrawn.")
    relationship.delete()


def block(relationship, company, user):
    if relationship.status != Status.ACTIVE:
        raise PartnerError("Only an active customer or supplier can be blocked.")
    relationship.status = Status.BLOCKED
    relationship.blocked_by = company
    relationship.save(update_fields=['status', 'blocked_by', 'updated_at'])
    return relationship


def unblock(relationship, company, user):
    if relationship.status != Status.BLOCKED or relationship.blocked_by_id != company.pk:
        raise PartnerError("Only the company that blocked this can unblock it.")
    relationship.status = Status.ACTIVE
    relationship.blocked_by = None
    relationship.save(update_fields=['status', 'blocked_by', 'updated_at'])
    return relationship


# Which side may change which term: the buyer keeps its own code for the
# supplier, the supplier its code for the customer and the credit it gives;
# payment terms are agreed, so either side may set them.
BUYER_TERMS = {'vendor_code', 'payment_terms_days'}
SUPPLIER_TERMS = {'customer_code', 'credit_limit', 'payment_terms_days'}


def update_terms(relationship, company, user, changes):
    allowed = BUYER_TERMS if relationship.buyer_id == company.pk else SUPPLIER_TERMS
    refused = sorted(set(changes) - allowed)
    if refused:
        raise PartnerError(f"The other company sets {', '.join(f.replace('_', ' ') for f in refused)}.")
    for field, value in changes.items():
        setattr(relationship, field, value)
    relationship.save()
    return relationship


def for_company(company):
    return BusinessRelationship.objects.filter(Q(buyer=company) | Q(supplier=company)).select_related(
        'buyer', 'supplier', 'initiated_by', 'blocked_by')


def _admin_emails(company):
    return list(Membership.objects.filter(
        company=company, is_active=True, user__is_active=True, role__permissions__code='partners.manage',
    ).values_list('user__email', flat=True).distinct())


def _notify_request(relationship, company, target):
    asked_as = CUSTOMER if relationship.supplier_id == company.pk else SUPPLIER
    for address in _admin_emails(target):
        queue_template_email(
            address, 'emails/partner_request_subject.txt', 'emails/partner_request.txt',
            {'company_name': _display_name(company), 'asked_as': 'customer' if asked_as == CUSTOMER else 'supplier',
             'url': settings.SITE_URL + '/app/partners'},
        )


def _invite_to_join(company, target, relation, email):
    key = f'partner-join-emails:{company.pk}:{timezone.localdate()}'
    cache.add(key, 0, timeout=60 * 60 * 24)
    if cache.incr(key) > JOIN_EMAILS_PER_DAY:
        return False
    queue_template_email(
        email.strip(), 'emails/partner_join_subject.txt', 'emails/partner_join.txt',
        {'company_name': _display_name(company), 'partner_name': target.legal_name,
         'as_what': 'supplier' if relation == SUPPLIER else 'customer',
         'url': settings.SITE_URL + reverse('register')},
    )
    return True


def describe(relationship, company):
    """One relationship as `company` sees it, for the API."""
    other = _their_side(relationship, company)
    we_buy = relationship.buyer_id == company.pk
    return {
        'id': relationship.pk,
        'relation': SUPPLIER if we_buy else CUSTOMER,
        'status': relationship.status,
        'waiting_on': ('us' if can_respond(relationship, company) else 'them')
        if relationship.status in (Status.INVITED, Status.PENDING_APPROVAL) else None,
        'blocked_by_us': relationship.status == Status.BLOCKED and relationship.blocked_by_id == company.pk,
        'partner': {
            'id': other.pk, 'name': _display_name(other), 'legal_name': other.legal_name, 'gstin': other.gstin or '',
            'city': other.city, 'state': other.state, 'on_network': on_network(other),
        },
        'payment_terms_days': relationship.payment_terms_days,
        'credit_limit': relationship.credit_limit,
        'vendor_code': relationship.vendor_code,
        'customer_code': relationship.customer_code,
        'editable_terms': sorted(BUYER_TERMS if we_buy else SUPPLIER_TERMS),
        'created_at': relationship.created_at,
        'activated_at': relationship.activated_at,
    }

