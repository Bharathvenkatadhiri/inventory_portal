"""ERP subscriptions: the free trial, paying for a plan, upgrades and
downgrades, renewals and what happens when they fail. Every change to an
OrganizationSubscription goes through here; the marketplace subscriptions
in billing.services are separate and unchanged. Both use the same payment
gateway (billing.gateways).

- Trial: ERP_TRIAL_DAYS on the plan chosen at sign-up. Switching plans is
  free during it. Paying during the trial starts the first period when the
  trial ends, so no trial days are lost.
- A period is 30 days. Its price is the plan's monthly price plus
  EXTRA_LIGHT_USER_MONTHLY for each light user past the plan's included
  number, counted when the period is charged.
- Upgrade: the plan price difference for the time left, to the second,
  applies at once. Downgrade: at the end of the period. Cancel: no renewal;
  access until the period ends.
- Renewal: charged to the saved payment method at expiry. A failure makes
  the subscription past due: access continues to grace_until while it's
  retried; after the last retry it lapses.
- Lapsed (trial or period ended unpaid): everything can be read, nothing
  changed, until someone pays (writes_allowed).
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.tasks import queue_template_email
from plans import erp

from .gateways import get_gateway
from .gateways.base import FAILED, SUCCEEDED
from .models import OrganizationPayment, OrganizationSubscription
from .services import MIN_CHARGE, PAISE, grace_period

logger = logging.getLogger(__name__)

Sub = OrganizationSubscription
Pay = OrganizationPayment
PERIOD = timedelta(days=30)
TRIAL_REMINDER_DAYS = 3


class BillingError(Exception):
    """A request that can't be carried out; the message says why."""


def trial_length():
    return timedelta(days=settings.ERP_TRIAL_DAYS)


# --- The subscription and what it allows ---------------------------------------

def start_trial(company, plan, now=None):
    now = now or timezone.now()
    subscription, _ = Sub.objects.get_or_create(company=company, defaults={
        'plan': plan, 'status': Sub.TRIALING, 'trial_ends_at': now + trial_length(),
    })
    return subscription


def subscription_for(company):
    """The organization's subscription; one that somehow has none starts a trial."""
    subscription = Sub.objects.filter(company=company).first()
    return subscription or start_trial(company, company.erp_plan or erp.DEFAULT_PLAN)


def is_entitled(subscription, now=None):
    """Whether the organization can use the ERP right now (else read-only)."""
    now = now or timezone.now()
    if subscription.status == Sub.TRIALING:
        return subscription.trial_ends_at is not None and now < subscription.trial_ends_at
    if subscription.status == Sub.ACTIVE:
        if subscription.expires_at is None:
            return False
        # Expired but renewing: keep access while the hourly job charges it.
        limit = subscription.expires_at + (grace_period() if subscription.auto_renew else timedelta(0))
        return now < limit
    if subscription.status == Sub.PAST_DUE:
        return subscription.grace_until is not None and now < subscription.grace_until
    return False


def writes_allowed(company, now=None):
    return is_entitled(subscription_for(company), now)


def _in_trial(subscription, now):
    return subscription.status == Sub.TRIALING and is_entitled(subscription, now)


def period_price(company, plan):
    """(total, plan part, extra light users) for one period on `plan`."""
    from organizations.workspace import seat_usage

    included = erp.PLANS[plan]['light_users']
    light = seat_usage(company)['light']
    extras = max(0, light - included) if included is not None else 0
    plan_price = Decimal(erp.PLANS[plan]['monthly'])
    return plan_price + Decimal(extras * erp.EXTRA_LIGHT_USER_MONTHLY), plan_price, extras


def _set_plan(subscription, plan):
    subscription.plan = plan
    company = subscription.company
    if company.erp_plan != plan:
        company.erp_plan = plan
        company.save(update_fields=['erp_plan', 'updated_at'])


# --- What a choice would do --------------------------------------------------------

@dataclass
class Quote:
    action: str          # 'current', 'switch', 'subscribe', 'new', 'upgrade', 'schedule', 'keep', 'renew'
    plan: str
    amount: Decimal = Decimal('0')
    plan_amount: Decimal = Decimal('0')
    extra_light_users: int = 0
    period_start: object = None
    period_end: object = None
    effective_at: object = None
    proration: dict = field(default_factory=dict)
    message: str = ''


def prorate(subscription, plan, now):
    """The upgrade charge: the new plan's price for the time left, less the
    current plan's, to the second. Extra light users don't change."""
    length = (subscription.expires_at - subscription.current_period_start).total_seconds()
    left = max((subscription.expires_at - now).total_seconds(), 0)
    fraction = Decimal(left) / Decimal(length) if length > 0 else Decimal('0')
    credit = (subscription.plan_price * fraction).quantize(PAISE, ROUND_HALF_UP)
    new_price = Decimal(erp.PLANS[plan]['monthly'])
    cost = (new_price * fraction).quantize(PAISE, ROUND_HALF_UP)
    amount = max(cost - credit, Decimal('0'))
    return amount, {
        'seconds_left': int(left), 'period_seconds': int(length), 'fraction': str(fraction.quantize(Decimal('0.000001'))),
        'current_plan_price': str(subscription.plan_price), 'credit': str(credit),
        'new_plan_price': str(new_price), 'cost_for_time_left': str(cost), 'charge': str(amount),
    }


def quote(subscription, plan, now=None):
    now = now or timezone.now()
    if plan not in erp.PLANS:
        raise BillingError("Unknown plan.")
    label = erp.PLANS[plan]['label']
    company = subscription.company
    total, plan_price, extras = period_price(company, plan)
    order = erp.PLAN_ORDER.index

    if _in_trial(subscription, now):
        if plan != subscription.plan:
            return Quote('switch', plan, message=f"Switch your trial to {label}. Nothing is charged.")
        start = subscription.trial_ends_at
        return Quote('subscribe', plan, total, plan_price, extras, start, start + PERIOD,
                     message=f"Pay for {label} now; your first month starts when the trial ends.")

    if subscription.status == Sub.PAST_DUE:
        start = subscription.expires_at
        return Quote('renew', plan, total, plan_price, extras, start, start + PERIOD,
                     message=f"Pay ₹{total} to renew on {label}.")

    if subscription.status == Sub.ACTIVE and is_entitled(subscription, now):
        if plan == subscription.plan:
            if subscription.scheduled_plan:
                return Quote('keep', plan, message=f"Stay on {label} instead of the scheduled change.")
            return Quote('current', plan, message=f"You're on {label}.")
        if order(plan) > order(subscription.plan):
            amount, proration = prorate(subscription, plan, now)
            return Quote('upgrade', plan, amount, amount, 0, subscription.current_period_start, subscription.expires_at,
                         proration=proration, message=f"Move to {label} now for ₹{amount} for the rest of this period.")
        return Quote('schedule', plan, effective_at=subscription.expires_at,
                     message=f"Move to {label} when this period ends on "
                             f"{timezone.localtime(subscription.expires_at):%d %b %Y}.")

    # Lapsed, or a trial or period that ended before the hourly job noticed.
    return Quote('new', plan, total, plan_price, extras, message=f"Subscribe to {label} for ₹{total} a month.")


# --- Changes -----------------------------------------------------------------------

def _new_payment(subscription, kind, q, user, attempt=1):
    gateway = get_gateway()
    payment = Pay(
        subscription=subscription, kind=kind, plan=q.plan, amount=q.amount, plan_amount=q.plan_amount,
        extra_light_users=q.extra_light_users, period_start=q.period_start, period_end=q.period_end,
        proration=q.proration or {}, basis_status=subscription.status, basis_plan=subscription.plan,
        basis_expires_at=subscription.expires_at, gateway=gateway.name, gateway_order_id='', created_by=user,
        attempt=attempt,
    )
    payment.gateway_order_id = gateway.create_order(payment)
    payment.save()
    return payment, gateway


def change_plan(company, user, plan, return_url_for, now=None):
    """Someone with billing.manage chose `plan`. Returns (URL to pay at, or
    None, message). Nothing paid changes until the payment succeeds."""
    now = now or timezone.now()
    with transaction.atomic():
        subscription = Sub.objects.select_for_update().select_related('company').get(pk=subscription_for(company).pk)
        q = quote(subscription, plan, now)
        label = erp.PLANS[plan]['label']
        if q.action == 'current':
            return None, q.message
        if q.action == 'switch':
            _set_plan(subscription, plan)
            subscription.save()
            return None, f"Your trial is now on {label}."
        if q.action == 'keep':
            subscription.scheduled_plan = ''
            subscription.save()
            return None, f"You'll stay on {label}."
        if q.action == 'schedule':
            subscription.scheduled_plan = plan
            subscription.save()
            return None, q.message.replace('Move', "You'll move", 1)
        if q.action == 'upgrade' and q.amount < MIN_CHARGE:
            payment, _gateway = _new_payment(subscription, Pay.UPGRADE, q, user)
            payment.status, payment.paid_at = Pay.SUCCEEDED, now
            payment.save(update_fields=['status', 'paid_at', 'updated_at'])
            _apply(payment, subscription, None, '', now)
            return None, f"You're now on {label}. There was nothing left to charge this period."
        kind = {'subscribe': Pay.NEW, 'new': Pay.NEW, 'upgrade': Pay.UPGRADE, 'renew': Pay.RENEWAL}[q.action]
        payment, gateway = _new_payment(subscription, kind, q, user)
    return gateway.checkout_url(payment, return_url_for(payment)), None


def cancel(company, user):
    subscription = subscription_for(company)
    if subscription.status != Sub.ACTIVE:
        raise BillingError("There's no paid subscription to cancel.")
    subscription.auto_renew = False
    subscription.scheduled_plan = ''
    subscription.save()
    return subscription


def reactivate(company, user):
    subscription = subscription_for(company)
    if subscription.status != Sub.ACTIVE or not is_entitled(subscription):
        raise BillingError("This subscription has ended. Choose a plan to start again.")
    subscription.auto_renew = True
    subscription.save()
    return subscription


# --- Applying gateway results -----------------------------------------------------

def _apply(payment, subscription, gateway_payment_id, mandate_id, now):
    if payment.applied:
        return 'already applied'
    if (subscription.status, subscription.plan, subscription.expires_at) != \
            (payment.basis_status, payment.basis_plan, payment.basis_expires_at):
        payment.needs_refund = True
        payment.save(update_fields=['needs_refund', 'updated_at'])
        logger.warning("ERP payment #%s succeeded but the subscription changed since; flagged for refund", payment.pk)
        return 'stale: flagged for refund'

    if payment.kind == Pay.UPGRADE:
        _set_plan(subscription, payment.plan)
        subscription.plan_price = Decimal(erp.PLANS[payment.plan]['monthly'])
        subscription.price = subscription.plan_price + Decimal(subscription.extra_light_users * erp.EXTRA_LIGHT_USER_MONTHLY)
    else:
        if payment.kind == Pay.RENEWAL:
            start = payment.period_start
        else:  # NEW: from now, or from the end of a trial still running
            start = max(now, payment.period_start) if payment.period_start else now
        _set_plan(subscription, payment.plan)
        subscription.plan_price = payment.plan_amount
        subscription.price = payment.amount
        subscription.extra_light_users = payment.extra_light_users
        subscription.current_period_start = start
        subscription.expires_at = start + PERIOD
        subscription.scheduled_plan = ''
        subscription.auto_renew = True
    subscription.status = Sub.ACTIVE
    subscription.grace_until = None
    subscription.next_retry_at = None
    subscription.renewal_attempts = 0
    if mandate_id:
        subscription.gateway_mandate_id = mandate_id
    subscription.save()

    payment.applied = True
    payment.period_start = subscription.current_period_start
    payment.period_end = subscription.expires_at
    payment.save(update_fields=['applied', 'period_start', 'period_end', 'updated_at'])
    if payment.created_by_id is None:  # automatic renewal: nobody saw it happen
        _email(subscription, 'erp_payment_received', {'payment': payment})
    return 'applied'


def apply_result(payment, result, now=None):
    """Brings `payment` in line with the gateway's `result`. Idempotent and
    order-safe, like billing.services.apply_result."""
    now = now or timezone.now()
    with transaction.atomic():
        payment = Pay.objects.select_for_update().get(pk=payment.pk)
        subscription = Sub.objects.select_for_update().select_related('company').get(pk=payment.subscription_id)
        if result.status == SUCCEEDED:
            if payment.status != Pay.SUCCEEDED:
                payment.status = Pay.SUCCEEDED
                payment.paid_at = now
                payment.gateway_payment_id = result.gateway_payment_id or None
                payment.save(update_fields=['status', 'paid_at', 'gateway_payment_id', 'updated_at'])
            return _apply(payment, subscription, result.gateway_payment_id, result.mandate_id, now)
        if result.status == FAILED:
            if payment.status in (Pay.SUCCEEDED, Pay.FAILED):
                return 'ignored: already final'
            payment.status = Pay.FAILED
            payment.failure_reason = (result.reason or '')[:300]
            payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
            if payment.kind == Pay.RENEWAL and payment.created_by_id is None \
                    and subscription.expires_at == payment.basis_expires_at:
                _renewal_failed(subscription, payment, now)
            return 'failed'
        return 'pending'


def reconcile(payment, now=None):
    return apply_result(payment, get_gateway(payment.gateway).fetch_status(payment), now)


# --- The hourly job ----------------------------------------------------------------

def _charge_renewal(subscription, now):
    plan = subscription.scheduled_plan or subscription.plan
    total, plan_price, extras = period_price(subscription.company, plan)
    start = subscription.expires_at
    q = Quote('renew', plan, total, plan_price, extras, start, start + PERIOD)
    payment, gateway = _new_payment(subscription, Pay.RENEWAL, q, None, attempt=subscription.renewal_attempts + 1)
    return apply_result(payment, gateway.charge_renewal(subscription, payment), now)


def _renewal_failed(subscription, payment, now):
    subscription.renewal_attempts = payment.attempt
    retries = settings.BILLING_RETRY_DAYS
    if subscription.renewal_attempts > len(retries):
        _lapse(subscription, 'renewal_failed')
        return
    subscription.status = Sub.PAST_DUE
    subscription.grace_until = subscription.expires_at + grace_period()
    subscription.next_retry_at = subscription.expires_at + timedelta(days=retries[subscription.renewal_attempts - 1])
    subscription.save()
    _email(subscription, 'erp_renewal_failed', {'payment': payment, 'grace_until': subscription.grace_until})


def _lapse(subscription, reason):
    subscription.status = Sub.LAPSED
    subscription.auto_renew = False
    subscription.scheduled_plan = ''
    subscription.grace_until = None
    subscription.next_retry_at = None
    subscription.save()
    _email(subscription, 'erp_lapsed', {'reason': reason})


def run_due(now=None):
    """Everything time-based for ERP subscriptions (manage.py
    process_subscriptions runs it hourly). Returns counts by outcome."""
    now = now or timezone.now()
    counts = {'erp_trial_reminders': 0, 'erp_lapsed': 0, 'erp_renewed': 0, 'erp_renewal_failed': 0,
              'erp_retried': 0, 'erp_pending_closed': 0}

    soon = now + timedelta(days=TRIAL_REMINDER_DAYS)
    for subscription in Sub.objects.filter(status=Sub.TRIALING, trial_ends_at__gt=now, trial_ends_at__lte=soon):
        if subscription.trial_reminder_sent_for != subscription.trial_ends_at:
            _email(subscription, 'erp_trial_ending', {})
            subscription.trial_reminder_sent_for = subscription.trial_ends_at
            subscription.save(update_fields=['trial_reminder_sent_for', 'updated_at'])
            counts['erp_trial_reminders'] += 1

    for subscription in Sub.objects.filter(status=Sub.TRIALING, trial_ends_at__lte=now):
        _lapse(subscription, 'trial_ended')
        counts['erp_lapsed'] += 1

    for subscription in Sub.objects.filter(status=Sub.ACTIVE, expires_at__lte=now).select_related('company'):
        with transaction.atomic():
            subscription = Sub.objects.select_for_update().select_related('company').get(pk=subscription.pk)
            if subscription.status != Sub.ACTIVE or subscription.expires_at > now:
                continue
            if not subscription.auto_renew:
                _lapse(subscription, 'cancelled')
                counts['erp_lapsed'] += 1
                continue
            if subscription.payments.filter(kind=Pay.RENEWAL, status=Pay.PENDING,
                                            basis_expires_at=subscription.expires_at).exists():
                continue
        outcome = _charge_renewal(subscription, now)
        counts['erp_renewed' if outcome == 'applied' else 'erp_renewal_failed'] += 1

    for subscription in Sub.objects.filter(status=Sub.PAST_DUE, next_retry_at__lte=now).select_related('company'):
        _charge_renewal(subscription, now)
        counts['erp_retried'] += 1

    for subscription in Sub.objects.filter(status=Sub.PAST_DUE, grace_until__lte=now):
        if not subscription.payments.filter(kind=Pay.RENEWAL, status=Pay.PENDING).exists():
            _lapse(subscription, 'renewal_failed')
            counts['erp_lapsed'] += 1

    stale = now - timedelta(minutes=settings.BILLING_PENDING_MINUTES)
    for payment in Pay.objects.filter(status=Pay.PENDING, created_at__lte=stale):
        if reconcile(payment, now) == 'pending':
            Pay.objects.filter(pk=payment.pk, status=Pay.PENDING).update(status=Pay.CANCELED, updated_at=now)
            counts['erp_pending_closed'] += 1
    return counts


# --- For the app ---------------------------------------------------------------------

def summary(company, now=None):
    """The subscription as the billing page and the app's banner show it."""
    now = now or timezone.now()
    subscription = subscription_for(company)
    total, plan_price, extras = period_price(company, subscription.plan)
    ends = subscription.trial_ends_at if subscription.status == Sub.TRIALING else (
        subscription.grace_until if subscription.status == Sub.PAST_DUE else subscription.expires_at)
    return {
        'plan': subscription.plan,
        'plan_label': erp.PLANS[subscription.plan]['label'],
        'status': subscription.status,
        'entitled': is_entitled(subscription, now),
        'trial_ends_at': subscription.trial_ends_at,
        'current_period_start': subscription.current_period_start,
        'expires_at': subscription.expires_at,
        'grace_until': subscription.grace_until,
        'auto_renew': subscription.auto_renew,
        'scheduled_plan': subscription.scheduled_plan or None,
        'days_left': max(0, (ends - now).days) if ends else None,
        'price': subscription.price,
        'next_amount': total,
        'next_plan_amount': plan_price,
        'next_extra_light_users': extras,
    }


def _day(value):
    return f"{timezone.localtime(value):%d %b %Y}" if value else ''


def _email(subscription, template, context):
    """To everyone in the organization who manages billing. The context is
    plain text, since the email is sent from the job queue."""
    from organizations.models import Membership

    company = subscription.company
    payment = context.get('payment')
    values = {
        'company_name': company.name or company.legal_name,
        'plan_label': erp.PLANS[subscription.plan]['label'],
        'billing_url': settings.SITE_URL + '/app/settings/billing',
        'reason': context.get('reason', ''),
        'amount': str(payment.amount) if payment else '',
        'trial_ends_at': _day(subscription.trial_ends_at),
        'expires_at': _day(subscription.expires_at),
        'grace_until': _day(context.get('grace_until')),
    }
    addresses = Membership.objects.filter(
        company=company, is_active=True, user__is_active=True, role__permissions__code='billing.manage',
    ).values_list('user__email', flat=True).distinct()
    for address in addresses:
        queue_template_email(address, f'emails/{template}_subject.txt', f'emails/{template}.txt', values)
