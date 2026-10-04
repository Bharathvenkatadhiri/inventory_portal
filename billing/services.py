"""The subscription lifecycle. Every change to a SubscriptionPlan goes
through here.

    Free ──pay──▶ Active ──expiry, auto-renew──▶ renewal charge
                    │                               │ fails
                    │                               ▼
                    │                           Past due (paid access until
                    │                           grace_until, retried)
                    │                               │ all retries fail
                    ▼ cancelled / expired           ▼
                  Free ◀────────────────────────────┘

- New subscription (Free or expired → paid): full price, a new period from
  the moment it's paid.
- Upgrade: charged the prorated difference for the time left, to the
  second; the higher plan applies at once; the period and expiry stay.
- Downgrade, billing-cycle change, or stopping (→ Free): scheduled for the
  current expiry; paid access continues until then; can be cancelled.
- Cancel: auto-renew off, access until expiry. Reactivate before expiry:
  auto-renew back on, same subscription.
- Renewal: at expiry, the next period's price (the plan's current catalogue
  price, or the scheduled plan's) is charged to the saved method. A failure
  makes the subscription past due, keeping access for the grace period
  while it's retried; after the last retry it moves to Free.

Payments are applied by apply_payment, whether the result arrives by the
gateway's webhook, the payer's redirect back, or reconciliation. It applies
each payment once, and only while the subscription is still in the state
the payment was priced for; anything else is recorded, never re-applied.
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts import team
from accounts.models import SubscriptionPlan
from core.emails import send_template_email
from plans import catalog

from .gateways import get_gateway
from .gateways.base import FAILED, SUCCEEDED
from .models import Payment, WebhookEvent

logger = logging.getLogger(__name__)

PAISE = Decimal('0.01')
# Below this, an upgrade's prorated charge isn't worth a payment (and is
# under gateways' minimum): the upgrade applies without one.
MIN_CHARGE = Decimal('1.00')


class BillingError(Exception):
    """A request that can't be carried out; the message says why."""


def period_length(cycle):
    return timedelta(days=catalog.PERIOD_DAYS[cycle])


def grace_period():
    return timedelta(days=settings.BILLING_GRACE_DAYS)


# --- Subscriptions and what they entitle ---------------------------------------

def subscription_for_owner(owner):
    """The owner's subscription row, created on Free if missing."""
    subscription, _ = SubscriptionPlan.objects.get_or_create(user_profile=owner, defaults={'plan_type': catalog.DEFAULT_PLAN})
    return subscription


def subscription_for(user):
    return subscription_for_owner(team.owner_user(user))


def entitled_plan(subscription, now=None):
    """The plan whose features and limits apply right now."""
    now = now or timezone.now()
    if subscription is None or not subscription.is_active or not subscription.is_paid:
        return catalog.DEFAULT_PLAN
    if subscription.expires_at is None or now < subscription.expires_at:
        return subscription.plan_type
    if subscription.status == SubscriptionPlan.PAST_DUE:
        return subscription.plan_type if subscription.grace_until and now < subscription.grace_until else catalog.DEFAULT_PLAN
    # Expired but auto-renewing: keep access while the hourly job charges the
    # renewal (it moves to past due or Free within the grace period at most).
    if subscription.auto_renew and now < subscription.expires_at + grace_period():
        return subscription.plan_type
    return catalog.DEFAULT_PLAN


def is_live_paid(subscription, now=None):
    """A paid subscription still in its period (or grace)."""
    return subscription.is_paid and entitled_plan(subscription, now) == subscription.plan_type


# --- Quotes ---------------------------------------------------------------------

@dataclass
class Quote:
    """What choosing `plan`/`cycle` would do, and cost, right now."""
    action: str                      # 'new', 'upgrade', 'schedule', 'renew', 'reactivate', 'none'
    plan_type: str
    billing_cycle: str
    amount: Decimal = Decimal('0')
    period_start: object = None
    period_end: object = None
    effective_at: object = None      # when a scheduled change applies
    proration: dict = field(default_factory=dict)
    message: str = ''


def prorate(subscription, target_plan, now):
    """The upgrade charge: the new plan's cost for the time left, less the
    current plan's unused value, both to the second."""
    length = (subscription.expires_at - subscription.current_period_start).total_seconds()
    left = max((subscription.expires_at - now).total_seconds(), 0)
    fraction = Decimal(left) / Decimal(length) if length > 0 else Decimal('0')
    credit = (subscription.price * fraction).quantize(PAISE, ROUND_HALF_UP)
    new_full = Decimal(catalog.price(target_plan, subscription.billing_cycle))
    cost = (new_full * fraction).quantize(PAISE, ROUND_HALF_UP)
    amount = max(cost - credit, Decimal('0'))
    return amount, {
        'seconds_left': int(left), 'period_seconds': int(length), 'fraction': str(fraction.quantize(Decimal('0.000001'))),
        'current_price': str(subscription.price), 'credit': str(credit),
        'new_full_price': str(new_full), 'cost_for_time_left': str(cost), 'charge': str(amount),
    }


def quote(subscription, plan, cycle, now=None):
    now = now or timezone.now()
    if plan not in catalog.PLAN_LABELS or cycle not in dict(catalog.BILLING_CYCLES):
        raise BillingError("Unknown plan.")
    live = is_live_paid(subscription, now)

    if not live:
        if plan == catalog.DEFAULT_PLAN:
            return Quote('none', plan, cycle, message="You're on the Free plan.")
        if not catalog.purchasable(plan):
            raise BillingError("That plan is no longer available.")
        price = Decimal(catalog.price(plan, cycle))
        return Quote('new', plan, cycle, amount=price, period_start=now, period_end=now + period_length(cycle))

    current = subscription.plan_type
    if subscription.expires_at is None:
        # Paid with no expiry (set by staff): nothing to prorate or schedule
        # against, so another paid plan is a new period from now.
        if plan == catalog.DEFAULT_PLAN or (plan == current and cycle == subscription.billing_cycle):
            return Quote('none', plan, cycle, message="This plan was set up by MakeSetu. Write to makesetu@gmail.com to change it.")
        if not catalog.purchasable(plan):
            raise BillingError("That plan is no longer available.")
        return Quote('new', plan, cycle, amount=Decimal(catalog.price(plan, cycle)), period_start=now, period_end=now + period_length(cycle))

    if subscription.status == SubscriptionPlan.PAST_DUE:
        if plan == current and cycle == subscription.billing_cycle:
            price = Decimal(catalog.price(plan, cycle))
            start = subscription.expires_at
            return Quote('renew', plan, cycle, amount=price, period_start=start, period_end=start + period_length(cycle),
                         message="Pay the overdue renewal to keep your plan.")
        raise BillingError("Your last renewal payment failed. Pay it first, then change your plan.")

    if plan == current and cycle == subscription.billing_cycle:
        if not subscription.auto_renew or subscription.scheduled_plan_type:
            return Quote('reactivate', plan, cycle, effective_at=subscription.expires_at)
        return Quote('none', plan, cycle, message=f"You're already on {catalog.PLAN_LABELS[plan]}.")

    if catalog.rank(plan) > catalog.rank(current):
        if not catalog.purchasable(plan):
            raise BillingError("That plan is no longer available.")
        amount, detail = prorate(subscription, plan, now)
        # An upgrade keeps the current billing cycle; a different cycle is
        # scheduled for the next period.
        return Quote('upgrade', plan, subscription.billing_cycle, amount=amount,
                     period_start=subscription.current_period_start, period_end=subscription.expires_at,
                     effective_at=now, proration=detail)

    # Lower plan, Free, or same plan on another cycle: at expiry.
    if plan != catalog.DEFAULT_PLAN and not catalog.purchasable(plan):
        raise BillingError("That plan is no longer available.")
    return Quote('schedule', plan, cycle, effective_at=subscription.expires_at)


# --- Checkout review ----------------------------------------------------------------

PAYING_ACTIONS = ('new', 'upgrade', 'renew')


def _duration(seconds):
    days, rest = divmod(int(seconds), 86400)
    hours = rest // 3600
    parts = [f"{days} day{'' if days == 1 else 's'}"] if days else []
    if hours or not days:
        parts.append(f"{hours} hour{'' if hours == 1 else 's'}")
    return ' '.join(parts)


def checkout_summary(subscription, plan, cycle, now=None):
    """What the checkout review page shows for choosing `plan` on `cycle`
    now: what happens, the price split, the dates and the next renewal.
    Built from quote(), the same numbers change_plan charges; the charge is
    worked out again to the second when the owner proceeds, so it can only
    have gone down (an upgrade's time left shrinks). Raises BillingError."""
    now = now or timezone.now()
    result = quote(subscription, plan, cycle, now)
    label = catalog.PLAN_LABELS[result.plan_type]
    per = 'month' if result.billing_cycle == catalog.MONTHLY else 'year'
    summary = {
        'action': result.action, 'plan': result.plan_type, 'plan_label': label,
        'cycle': result.billing_cycle, 'requested_cycle': cycle, 'per': per,
        'amount': result.amount, 'pays': result.action in PAYING_ACTIONS and result.amount >= MIN_CHARGE,
        'lines': [], 'notes': [], 'message': result.message,
        'current_label': catalog.PLAN_LABELS[entitled_plan(subscription, now)],
    }
    if result.action == 'new':
        expires = now + period_length(result.billing_cycle)
        summary['lines'].append({
            'label': f"{label} plan, billed {result.billing_cycle}",
            'detail': f"₹{catalog.price(result.plan_type, result.billing_cycle):,}/{per} for {catalog.PERIOD_DAYS[result.billing_cycle]} days",
            'amount': result.amount,
        })
        summary.update(starts_at=now, expires_at=expires, renews=True, renews_at=expires,
                       renewal_label=label, renewal_cycle=result.billing_cycle, renewal_amount=result.amount)
    elif result.action == 'upgrade':
        detail = result.proration
        left = _duration(detail['seconds_left'])
        percent = (Decimal(detail['fraction']) * 100).quantize(Decimal('0.1'))
        summary['lines'] += [
            {'label': f"{label} for the time left in this period",
             'detail': f"₹{Decimal(detail['new_full_price']):,.2f}/{per} × {percent}% ({left} of {_duration(detail['period_seconds'])})",
             'amount': Decimal(detail['cost_for_time_left'])},
            {'label': f"Less unused {catalog.PLAN_LABELS[subscription.plan_type]} time",
             'detail': f"₹{Decimal(detail['current_price']):,.2f}/{per} × {percent}% ({left}) already paid",
             'amount': -Decimal(detail['credit'])},
        ]
        summary['time_left'] = left
        renew_plan = subscription.scheduled_plan_type or result.plan_type
        renew_cycle = subscription.scheduled_billing_cycle or subscription.billing_cycle
        renews = subscription.auto_renew and renew_plan != catalog.DEFAULT_PLAN
        summary.update(starts_at=now, expires_at=subscription.expires_at, renews=renews, renews_at=subscription.expires_at,
                       renewal_label=catalog.PLAN_LABELS[renew_plan], renewal_cycle=renew_cycle,
                       renewal_amount=Decimal(catalog.price(renew_plan, renew_cycle)) if renews else Decimal('0'))
        summary['notes'].append(f"Your billing date doesn't change: {label} applies as soon as you pay, until {timezone.localtime(subscription.expires_at):%d %b %Y, %H:%M}.")
        if cycle != result.billing_cycle:
            summary['notes'].append(f"Upgrades keep your current {result.billing_cycle} billing. To switch to {cycle} billing, choose it again after upgrading; it starts at your next renewal.")
        if result.amount < MIN_CHARGE:
            summary['notes'].append("There's nothing left to charge for this period, so the upgrade applies straight away.")
    elif result.action == 'renew':
        summary['lines'].append({
            'label': f"Overdue renewal: {label}, billed {result.billing_cycle}",
            'detail': f"₹{catalog.price(result.plan_type, result.billing_cycle):,}/{per}",
            'amount': result.amount,
        })
        summary.update(starts_at=result.period_start, expires_at=result.period_end, renews=True, renews_at=result.period_end,
                       renewal_label=label, renewal_cycle=result.billing_cycle, renewal_amount=result.amount)
        summary['notes'].append("The new period continues from your last expiry date, so you don't lose any days.")
    elif result.action == 'schedule':
        summary.update(effective_at=result.effective_at)
    elif result.action == 'reactivate':
        summary.update(effective_at=result.effective_at, renewal_amount=Decimal(catalog.price(result.plan_type, result.billing_cycle)))
    return summary


def checkout_options(subscription, now=None):
    """Every paid plan on each cycle, with what choosing it would do now,
    for the review page's plan switcher."""
    now = now or timezone.now()
    options = []
    for plan in catalog.PLAN_ORDER:
        if not catalog.purchasable(plan):
            continue
        cycles = []
        for cycle in (catalog.MONTHLY, catalog.YEARLY):
            try:
                result = quote(subscription, plan, cycle, now)
                action = result.action
            except BillingError:
                action = 'unavailable'
            cycles.append({'cycle': cycle, 'price': catalog.price(plan, cycle), 'action': action})
        options.append({'plan': plan, 'label': catalog.PLAN_LABELS[plan], 'cycles': cycles})
    return options


# --- Starting a change ------------------------------------------------------------

def _new_payment(subscription, kind, plan, cycle, amount, user, period_start=None, period_end=None, proration=None, attempt=1):
    gateway = get_gateway()
    payment = Payment(
        subscription=subscription, kind=kind, plan_type=plan, billing_cycle=cycle, amount=amount,
        period_start=period_start, period_end=period_end, proration=proration or {},
        basis_plan_type=subscription.plan_type, basis_expires_at=subscription.expires_at,
        gateway=gateway.name, gateway_order_id='', created_by=user, attempt=attempt,
    )
    payment.gateway_order_id = gateway.create_order(payment)
    payment.save()
    return payment, gateway


def change_plan(user, plan, cycle, return_url_for, now=None):
    """The owner chose `plan` on `cycle`. Returns (redirect URL to pay, or
    None, message). A paid change creates a pending Payment and sends the
    owner to the gateway; nothing changes until it's paid."""
    now = now or timezone.now()
    with transaction.atomic():
        subscription = SubscriptionPlan.objects.select_for_update().get(pk=subscription_for(user).pk)
        result = quote(subscription, plan, cycle, now)
        label = catalog.PLAN_LABELS[result.plan_type]

        if result.action == 'none':
            return None, result.message
        if result.action == 'reactivate':
            reactivate(subscription, user)
            return None, f"Your {label} subscription will renew on {timezone.localtime(subscription.expires_at):%d %b %Y}."
        if result.action == 'schedule':
            if subscription.scheduled_plan_type == plan and subscription.scheduled_billing_cycle == cycle:
                return None, "That change is already scheduled."
            subscription.scheduled_plan_type = plan
            subscription.scheduled_billing_cycle = cycle
            subscription.auto_renew = plan != catalog.DEFAULT_PLAN
            subscription.save()
            when = f"{timezone.localtime(subscription.expires_at):%d %b %Y, %H:%M}"
            team.log(user, 'billing.change_scheduled', f"Scheduled a change to {label} ({cycle}) at {when}")
            if plan == catalog.DEFAULT_PLAN:
                return None, f"Your plan will end on {when}; you'll move to Free then."
            return None, f"You'll move to {label} ({cycle}) on {when}. You keep your current plan until then."
        if result.action == 'upgrade' and result.amount < MIN_CHARGE:
            payment, _gateway = _new_payment(subscription, Payment.UPGRADE, result.plan_type, result.billing_cycle,
                                             result.amount, user, result.period_start, result.period_end, result.proration)
            payment.status, payment.paid_at = Payment.SUCCEEDED, now
            payment.save(update_fields=['status', 'paid_at', 'updated_at'])
            _apply(payment, subscription, gateway_payment_id=None, mandate_id='', now=now)
            return None, f"You're now on {label} for the rest of this period. There was nothing left to charge."

        kind = {'new': Payment.NEW, 'upgrade': Payment.UPGRADE, 'renew': Payment.RENEWAL}[result.action]
        payment, gateway = _new_payment(subscription, kind, result.plan_type, result.billing_cycle, result.amount, user,
                                        result.period_start, result.period_end, result.proration)
    return gateway.checkout_url(payment, return_url_for(payment)), None


def cancel(subscription, user):
    """UC-20: stop renewing; paid access continues until expiry."""
    subscription.auto_renew = False
    subscription.scheduled_plan_type = ''
    subscription.scheduled_billing_cycle = ''
    subscription.save()
    team.log(user, 'billing.cancelled', "Cancelled the subscription; it ends at the current expiry")


def reactivate(subscription, user):
    """UC-21: before expiry, the same subscription renews again."""
    if not is_live_paid(subscription):
        raise BillingError("This subscription has ended. Choose a plan to start a new one.")
    subscription.auto_renew = True
    subscription.scheduled_plan_type = ''
    subscription.scheduled_billing_cycle = ''
    subscription.save()
    team.log(user, 'billing.reactivated', "Turned automatic renewal back on")


def cancel_scheduled_change(subscription, user):
    """UC-14: keep the current plan after expiry."""
    subscription.scheduled_plan_type = ''
    subscription.scheduled_billing_cycle = ''
    subscription.auto_renew = True
    subscription.save()
    team.log(user, 'billing.change_cancelled', "Cancelled the scheduled plan change")


# --- Applying gateway results ---------------------------------------------------------

def _apply(payment, subscription, gateway_payment_id, mandate_id, now):
    """Carries out a successful payment on its (locked) subscription, if the
    subscription is still in the state the payment was priced for."""
    if payment.applied:
        return 'already applied'
    if subscription.plan_type != payment.basis_plan_type or subscription.expires_at != payment.basis_expires_at:
        payment.needs_refund = True
        payment.save(update_fields=['needs_refund', 'updated_at'])
        logger.warning("Payment #%s succeeded but the subscription changed since it was priced; flagged for refund", payment.pk)
        return 'stale: flagged for refund'

    if payment.kind == Payment.UPGRADE:
        subscription.plan_type = payment.plan_type
        subscription.price = Decimal(catalog.price(payment.plan_type, subscription.billing_cycle))
    else:  # NEW or RENEWAL: a whole period
        if payment.kind == Payment.NEW:
            subscription.started_at = now
            period_start = now
        else:
            period_start = payment.period_start
        subscription.plan_type = payment.plan_type
        subscription.billing_cycle = payment.billing_cycle
        subscription.price = payment.amount
        subscription.current_period_start = period_start
        subscription.expires_at = period_start + period_length(payment.billing_cycle)
        subscription.scheduled_plan_type = ''
        subscription.scheduled_billing_cycle = ''
        subscription.auto_renew = True
        subscription.intended_plan_type = ''
        subscription.intended_billing_cycle = ''
    subscription.status = SubscriptionPlan.ACTIVE
    subscription.grace_until = None
    subscription.next_retry_at = None
    subscription.renewal_attempts = 0
    subscription.is_active = True
    if mandate_id:
        subscription.gateway_mandate_id = mandate_id
    subscription.save()

    payment.applied = True
    payment.period_start = subscription.current_period_start
    payment.period_end = subscription.expires_at
    payment.save(update_fields=['applied', 'period_start', 'period_end', 'updated_at'])
    _forget_cached_plans(subscription)
    label = catalog.PLAN_LABELS[subscription.plan_type]
    team.log(payment.created_by or subscription.user_profile, f'billing.{payment.kind}',
             f"Paid ₹{payment.amount} — {payment.get_kind_display().lower()}, {label}", reverse('profile') + '?tab=billing',
             profile=team.company(subscription.user_profile))
    # The owner paying in the portal sees the result there (PaymentReturnView);
    # only automatic renewals, charged with nobody on the page, are emailed.
    if payment.created_by_id is None:
        _email(subscription, 'billing_payment_received', {'payment': payment, 'plan_label': label})
    return 'applied'


def apply_result(payment, result, now=None):
    """Brings `payment` in line with the gateway's `result`. Idempotent and
    order-safe: a success is final (a later failure for the same payment is
    stale and ignored); a pending result changes nothing."""
    now = now or timezone.now()
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        subscription = SubscriptionPlan.objects.select_for_update().get(pk=payment.subscription_id)
        if result.status == SUCCEEDED:
            if payment.status != Payment.SUCCEEDED:
                payment.status = Payment.SUCCEEDED
                payment.paid_at = now
                payment.gateway_payment_id = result.gateway_payment_id or None
                payment.save(update_fields=['status', 'paid_at', 'gateway_payment_id', 'updated_at'])
            return _apply(payment, subscription, result.gateway_payment_id, result.mandate_id, now)
        if result.status == FAILED:
            if payment.status in (Payment.SUCCEEDED, Payment.FAILED):
                return 'ignored: already final'
            payment.status = Payment.FAILED
            payment.failure_reason = (result.reason or '')[:300]
            payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
            # Only the automatic charges count towards the retry schedule; the
            # owner's own "Pay now" failing leaves past due as it was.
            if payment.kind == Payment.RENEWAL and payment.created_by_id is None \
                    and subscription.expires_at == payment.basis_expires_at:
                _renewal_failed(subscription, payment, now)
            return 'failed'
        return 'pending'


def process_event(gateway_name, event, now=None):
    """A webhook, once: UC-06 / UC-28 (duplicates) and UC-29 (out of order)."""
    record, created = WebhookEvent.objects.get_or_create(
        gateway=gateway_name, event_id=event.event_id,
        defaults={'event_type': event.event_type, 'payload': event.payload},
    )
    if not created and record.processed_at:
        return 'duplicate'
    payment = Payment.objects.filter(gateway=gateway_name, gateway_order_id=event.order_id).first()
    result = apply_result(payment, event.result, now) if payment else 'unknown order'
    record.processed_at = timezone.now()
    record.result = result[:200]
    record.save(update_fields=['processed_at', 'result'])
    return result


def reconcile(payment, now=None):
    """Asks the gateway where a payment stands and applies it: the payer's
    redirect back (UC-05/UC-27) and the hourly job both use this."""
    return apply_result(payment, get_gateway(payment.gateway).fetch_status(payment), now)


# --- Renewals, retries and expiry (the hourly job) -------------------------------------

def _renewal_target(subscription):
    plan = subscription.scheduled_plan_type or subscription.plan_type
    cycle = subscription.scheduled_billing_cycle or subscription.billing_cycle
    while plan in catalog.RETIRED_PLANS:   # UC-31: retired plans renew into their successor
        plan = catalog.RETIRED_PLANS[plan] or catalog.DEFAULT_PLAN
    return plan, cycle


def _charge_renewal(subscription, now):
    plan, cycle = _renewal_target(subscription)
    attempt = subscription.renewal_attempts + 1
    payment, gateway = _new_payment(
        subscription, Payment.RENEWAL, plan, cycle, Decimal(catalog.price(plan, cycle)), None,
        period_start=subscription.expires_at, period_end=subscription.expires_at + period_length(cycle), attempt=attempt,
    )
    result = gateway.charge_renewal(subscription, payment)
    return apply_result(payment, result, now)


def _renewal_failed(subscription, payment, now):
    """UC-17: past due, access kept through the grace period, retried; UC-19
    after the last retry, Free."""
    subscription.renewal_attempts = payment.attempt
    retries = settings.BILLING_RETRY_DAYS
    if subscription.renewal_attempts > len(retries):
        _move_to_free(subscription, now, reason='renewal_failed')
        return
    subscription.status = SubscriptionPlan.PAST_DUE
    subscription.grace_until = subscription.expires_at + grace_period()
    subscription.next_retry_at = subscription.expires_at + timedelta(days=retries[subscription.renewal_attempts - 1])
    subscription.save()
    _email(subscription, 'billing_renewal_failed', {
        'payment': payment, 'plan_label': catalog.PLAN_LABELS[subscription.plan_type],
        'grace_until': subscription.grace_until, 'next_retry_at': subscription.next_retry_at,
    })


def _move_to_free(subscription, now, reason):
    """UC-19 / UC-20 / UC-33: paid entitlements end; the company is on Free
    with a fresh 30-day usage window."""
    previous = catalog.PLAN_LABELS.get(subscription.plan_type, subscription.plan_type)
    subscription.plan_type = catalog.DEFAULT_PLAN
    subscription.billing_cycle = catalog.MONTHLY
    subscription.price = Decimal('0')
    subscription.status = SubscriptionPlan.ACTIVE
    subscription.started_at = now
    subscription.current_period_start = now
    subscription.expires_at = None
    subscription.auto_renew = False
    subscription.scheduled_plan_type = ''
    subscription.scheduled_billing_cycle = ''
    subscription.grace_until = None
    subscription.next_retry_at = None
    subscription.renewal_attempts = 0
    subscription.save()
    _forget_cached_plans(subscription)
    team.log(None, 'billing.moved_to_free', f"{previous} ended ({reason.replace('_', ' ')}); now on Free",
             profile=team.company(subscription.user_profile))
    _email(subscription, 'billing_moved_to_free', {'previous_label': previous, 'reason': reason})


def _reminder_context(subscription):
    """What happens at expiry, for the reminder emails."""
    plan, cycle = _renewal_target(subscription)
    renews = subscription.auto_renew and plan != catalog.DEFAULT_PLAN
    return {
        'plan_label': catalog.PLAN_LABELS[subscription.plan_type],
        'expires_at': subscription.expires_at,
        'renews': renews,
        'next_label': catalog.PLAN_LABELS[plan] if renews else '',
        'next_cycle': cycle,
        'next_amount': catalog.price(plan, cycle) if renews else 0,
        'changes_plan': renews and (plan, cycle) != (subscription.plan_type, subscription.billing_cycle),
    }


def send_expiry_reminders(now=None):
    """Emails the Owner of each paid, active subscription
    BILLING_REMINDER_DAYS_BEFORE days before it expires and again on the
    expiry date (local time), once each per period: when it renews, for how
    much, or that it ends and the company moves to Free. When both fall due
    together (a period that started inside the window), only the expiry-day
    one is sent. Returns how many were sent."""
    now = now or timezone.now()
    before = timedelta(days=settings.BILLING_REMINDER_DAYS_BEFORE)
    sent = 0
    due = (SubscriptionPlan.objects.filter(is_active=True, status=SubscriptionPlan.ACTIVE,
                                           expires_at__gt=now, expires_at__lte=now + before)
           .exclude(plan_type=catalog.DEFAULT_PLAN))
    for subscription in due:
        expires = subscription.expires_at
        if timezone.localdate(expires) == timezone.localdate(now):
            if subscription.reminder_day_sent_for == expires:
                continue
            template = 'billing_expires_today'
        elif subscription.reminder_before_sent_for != expires and subscription.reminder_day_sent_for != expires:
            template = 'billing_expiry_reminder'
        else:
            continue
        # Claim it first, so overlapping runs never send the same reminder twice.
        field = 'reminder_day_sent_for' if template == 'billing_expires_today' else 'reminder_before_sent_for'
        claimed = (SubscriptionPlan.objects.filter(pk=subscription.pk, expires_at=expires)
                   .exclude(**{field: expires}).update(**{field: expires}))
        if not claimed:
            continue
        _email(subscription, template, _reminder_context(subscription))
        sent += 1
    return sent


def days_left(subscription, now=None):
    """Calendar days (local time) until a paid subscription's current
    period ends: 0 on the expiry date itself. None for Free or no expiry."""
    if subscription is None or not subscription.is_paid or subscription.expires_at is None:
        return None
    now = now or timezone.now()
    return max((timezone.localdate(subscription.expires_at) - timezone.localdate(now)).days, 0)


def renewal_alert(subscription, now=None):
    """The in-portal renewal alert, shown from BILLING_REMINDER_DAYS_BEFORE
    days before a paid plan's expiry through the end of the expiry date
    (local time), the same window as the reminder emails. A renewal moves
    expires_at on, which ends it. None when nothing is due.

    `key` names this period and stage ('soon', then 'today'), so a
    dismissal or a read notification for one doesn't hide the next."""
    now = now or timezone.now()
    if subscription is None or not subscription.is_active or not subscription.is_paid or subscription.expires_at is None:
        return None
    expires = subscription.expires_at
    if now < expires - timedelta(days=settings.BILLING_REMINDER_DAYS_BEFORE):
        return None
    if timezone.localdate(now) > timezone.localdate(expires):
        return None
    left = days_left(subscription, now)
    stage = 'today' if left == 0 else 'soon'
    return {
        **_reminder_context(subscription),
        'key': f"plan-expiry:{int(expires.timestamp())}:{stage}",
        'days_left': left,
        'expired': now >= expires,
        'past_due': subscription.status == SubscriptionPlan.PAST_DUE,
        'starts_at': max(expires - timedelta(days=settings.BILLING_REMINDER_DAYS_BEFORE), subscription.current_period_start),
    }


def feed_events(user, now=None):
    """The renewal alert as a notification-bell entry for everyone on the
    company account (marketplace.services.notification_feed)."""
    subscription = SubscriptionPlan.objects.filter(user_profile=team.owner_user(user)).first()
    alert = renewal_alert(subscription, now)
    if alert is None:
        return []
    if alert['past_due']:
        title = f"{alert['plan_label']} renewal payment failed"
    elif alert['renews']:
        title = f"{alert['plan_label']} plan renews {'today' if alert['days_left'] == 0 else 'in ' + _days(alert['days_left'])}"
    elif alert['expired']:
        title = f"{alert['plan_label']} plan has ended"
    else:
        title = f"{alert['plan_label']} plan {'ends today' if alert['days_left'] == 0 else 'ends in ' + _days(alert['days_left'])}"
    when = timezone.localtime(alert['expires_at']).strftime('%d %b %Y, %H:%M')
    if alert['past_due']:
        detail = f"Pay the renewal to keep {alert['plan_label']}; otherwise your company moves to Free."
    elif alert['renews']:
        detail = f"Renews automatically on {when}."
    elif alert['expired']:
        detail = f"It ended on {when} and your company moved to Free. Subscribe again to get it back."
    else:
        detail = f"Renewal is off. On {when} your company moves to Free unless it's renewed."
    return [{
        'key': alert['key'], 'kind': 'billing',
        'title': title, 'detail': detail,
        'url': reverse('profile') + '?tab=billing',
        'timestamp': alert['starts_at'],
    }]


def _days(n):
    return f"{n} day{'' if n == 1 else 's'}"


def run_due(now=None):
    """Everything time-based, for `manage.py process_subscriptions` (hourly):
    reminder emails before and on the expiry date, renewals and scheduled
    changes at expiry, retries, the end of grace, and pending payments
    nobody finished. Returns counts by outcome."""
    now = now or timezone.now()
    counts = {'renewed': 0, 'renewal_failed': 0, 'moved_to_free': 0, 'retried': 0, 'pending_closed': 0,
              'reminders_sent': send_expiry_reminders(now)}

    for subscription in SubscriptionPlan.objects.filter(is_active=True, status=SubscriptionPlan.ACTIVE,
                                                        expires_at__lte=now).exclude(plan_type=catalog.DEFAULT_PLAN):
        with transaction.atomic():
            subscription = SubscriptionPlan.objects.select_for_update().get(pk=subscription.pk)
            if subscription.status != SubscriptionPlan.ACTIVE or subscription.expires_at is None or subscription.expires_at > now:
                continue
            target, _cycle = _renewal_target(subscription)
            if not subscription.auto_renew or target == catalog.DEFAULT_PLAN:
                _move_to_free(subscription, now, reason='cancelled' if not subscription.auto_renew else 'downgraded')
                counts['moved_to_free'] += 1
                continue
            if subscription.payments.filter(kind=Payment.RENEWAL, status=Payment.PENDING, basis_expires_at=subscription.expires_at).exists():
                continue  # a renewal charge is already awaiting the gateway
        outcome = _charge_renewal(subscription, now)
        counts['renewed' if outcome == 'applied' else 'renewal_failed'] += 1

    for subscription in SubscriptionPlan.objects.filter(status=SubscriptionPlan.PAST_DUE, next_retry_at__lte=now):
        outcome = _charge_renewal(subscription, now)
        counts['retried'] += 1
        if outcome != 'applied':
            subscription.refresh_from_db()
            if subscription.plan_type == catalog.DEFAULT_PLAN:
                counts['moved_to_free'] += 1

    for subscription in SubscriptionPlan.objects.filter(status=SubscriptionPlan.PAST_DUE, grace_until__lte=now):
        with transaction.atomic():
            subscription = SubscriptionPlan.objects.select_for_update().get(pk=subscription.pk)
            awaiting = subscription.payments.filter(kind=Payment.RENEWAL, status=Payment.PENDING).exists()
            if subscription.status == SubscriptionPlan.PAST_DUE and subscription.grace_until <= now and not awaiting:
                _move_to_free(subscription, now, reason='renewal_failed')
                counts['moved_to_free'] += 1

    stale = now - timedelta(minutes=settings.BILLING_PENDING_MINUTES)
    for payment in Payment.objects.filter(status=Payment.PENDING, created_at__lte=stale):
        outcome = reconcile(payment, now)  # UC-26/27: paid after all? apply it
        if outcome == 'pending':
            Payment.objects.filter(pk=payment.pk, status=Payment.PENDING).update(status=Payment.CANCELED, updated_at=now)
            counts['pending_closed'] += 1
    return counts


# --- Helpers ------------------------------------------------------------------------------

def _forget_cached_plans(subscription):
    profile = team.company(subscription.user_profile)
    if profile is not None and hasattr(profile, '_plan_key'):
        del profile._plan_key


def _email(subscription, template, context):
    owner = subscription.user_profile
    send_template_email(
        owner.email, f'emails/{template}_subject.txt', f'emails/{template}.txt',
        {**context, 'subscription': subscription, 'owner_name': owner.first_name or owner.email,
         'billing_url': f"{settings.SITE_URL}{reverse('profile')}?tab=billing"},
    )
