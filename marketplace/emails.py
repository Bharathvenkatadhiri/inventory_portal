"""Lifecycle notification emails: a new RFQ reaching matching
manufacturers, and each side hearing about the stages of their own quotes
and orders. Every function here is called from the view or service that
already performs the state change, right after it commits, and never
raises — core.emails.send_template_email already swallows send failures,
and the recipient-lookup here is wrapped the same way, so a bad profile
or a down mail server never blocks the RFQ/quote/order action itself.

There's no task queue in this project (see core/emails.py), so sends
happen inline in the request. notify_new_rfq is the one case that can
fan out to many recipients; it caps the list rather than emailing an
unbounded number of manufacturers synchronously — move this to a
background job (Celery/Django-Q) before that cap ever matters in
practice.
"""
import functools
import logging

from django.conf import settings
from django.urls import reverse

from accounts.models import ManufacturerProfile
from core.emails import send_template_email

logger = logging.getLogger(__name__)

NEW_RFQ_FANOUT_LIMIT = 25


def _guard(fn):
    """Every public notify_* function is wrapped in this: building the
    context (order.customer.user.email and the like) can itself raise —
    a missing profile, a null FK — and the module docstring's guarantee
    is that none of that ever propagates back into the action it's
    attached to."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("Notification email failed in %s", fn.__name__)
            return None
    return wrapper


def _url(name, **kwargs):
    return f"{settings.SITE_URL}{reverse(name, kwargs=kwargs)}"


def _send(to_email, event, context, html=True):
    base = f'emails/{event}'
    send_template_email(
        to_email, f'{base}_subject.txt', f'{base}.txt', context,
        html_template=f'{base}.html' if html else None,
    )


def _matching_suppliers(requirement):
    """Manufacturers worth telling about a new RFQ: capability overlaps
    with at least one part's process, accepting RFQs, not deactivated.
    Same PROCESS_CAPABILITY_MAP compute_match_percent uses, just run in
    the other direction (one RFQ -> many manufacturers instead of one
    manufacturer -> many RFQs)."""
    from .services import PROCESS_CAPABILITY_MAP  # local: services imports this module too
    technologies = set(requirement.requirement_parts.values_list('technology', flat=True))
    capability_keys = set()
    for technology in technologies:
        capability_keys |= PROCESS_CAPABILITY_MAP.get(technology, set())
    if not capability_keys:
        return ManufacturerProfile.objects.none()
    return (
        ManufacturerProfile.objects.filter(
            is_deleted=False, accepting_rfqs=True, capabilities__technology_type__in=capability_keys,
        )
        .exclude(user__is_active=False)
        .select_related('user')
        .distinct()
    )


@_guard
def notify_new_rfq(requirement):
    """Buyer posts an RFQ -> every matching, RFQ-accepting manufacturer.
    See the module docstring for why this is capped and inline."""
    suppliers = list(_matching_suppliers(requirement)[:NEW_RFQ_FANOUT_LIMIT + 1])
    if len(suppliers) > NEW_RFQ_FANOUT_LIMIT:
        logger.warning(
            "Requirement #%s matched more than %d suppliers; only the first %d were emailed",
            requirement.pk, NEW_RFQ_FANOUT_LIMIT, NEW_RFQ_FANOUT_LIMIT,
        )
        suppliers = suppliers[:NEW_RFQ_FANOUT_LIMIT]
    part = requirement.requirement_parts.first()
    context_base = {
        'requirement': requirement,
        'process': part.technology if part else '',
        'part_count': requirement.requirement_parts.count(),
        'url': _url('requirement', pk=requirement.pk),
    }
    for supplier in suppliers:
        try:
            _send(supplier.user.email, 'new_rfq', {**context_base, 'company_name': supplier.companyname or supplier.user.get_short_name()})
        except Exception:
            # One malformed row shouldn't stop the rest of the fan-out.
            logger.exception("Could not email new-RFQ notification to supplier #%s", supplier.pk)


@_guard
def notify_quote_submitted(quote):
    """Manufacturer submits a (non-draft) quote -> the RFQ's buyer."""
    requirement = quote.requirement
    _send(requirement.user.email, 'quote_received', {
        'requirement': requirement,
        'quote': quote,
        'supplier_name': quote.supplier.companyname or str(quote.supplier),
        'buyer_name': requirement.user.get_short_name() or requirement.user.username,
        'url': _url('requirement', pk=requirement.pk),
    })


@_guard
def notify_quote_awarded(order):
    """Buyer selects a quote -> the winning manufacturer."""
    quote = order.quote
    _send(quote.supplier.user.email, 'quote_awarded', {
        'order': order,
        'quote': quote,
        'requirement': order.requirement,
        'supplier_name': quote.supplier.companyname or str(quote.supplier),
        'url': _url('order-detail', billno=order.billno),
    })


@_guard
def notify_quote_not_selected(quote):
    """A quote is rejected — explicitly by the buyer, or automatically
    because another quote on the same RFQ was awarded — -> that quote's
    manufacturer."""
    _send(quote.supplier.user.email, 'quote_not_selected', {
        'quote': quote,
        'requirement': quote.requirement,
        'supplier_name': quote.supplier.companyname or str(quote.supplier),
        'url': _url('requirement-list'),
    })


# One entry per Order.status this fires for: who hears about it, and which
# template. 'quote_selected' is deliberately absent — award already sends
# notify_quote_awarded with the richer, order-just-created context.
_ORDER_STATUS_EVENTS = {
    'in_production': ('order_production_started', 'customer'),
    'payment_pending': ('order_payment_requested', 'customer'),
    'paid': ('order_payment_confirmed', 'supplier'),
    'completed': ('order_completed', 'both'),
    'cancelled': ('order_cancelled', 'both'),
}


@_guard
def notify_order_status(order, status):
    """An order's commercial status changes -> whichever side the change
    is news to (see _ORDER_STATUS_EVENTS)."""
    config = _ORDER_STATUS_EVENTS.get(status)
    if config is None:
        return
    event, audience = config
    context = {
        'order': order,
        'requirement': order.requirement,
        'supplier_name': order.supplier.companyname or str(order.supplier),
        'buyer_name': order.customer.Name or order.customer.user.get_short_name(),
        'url': _url('order-detail', billno=order.billno),
    }
    if audience in ('customer', 'both'):
        _send(order.customer.user.email, event, {**context, 'recipient_is_buyer': True})
    if audience in ('supplier', 'both'):
        _send(order.supplier.user.email, event, {**context, 'recipient_is_buyer': False})


@_guard
def notify_order_dispatched(order, invoice):
    """Production reaches the Dispatched stage (the tax invoice is issued
    in the same step) -> the buyer."""
    _send(order.customer.user.email, 'order_dispatched', {
        'order': order,
        'invoice': invoice,
        'requirement': order.requirement,
        'supplier_name': order.supplier.companyname or str(order.supplier),
        'url': _url('order-detail', billno=order.billno),
    })
