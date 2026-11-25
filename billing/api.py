"""/api/v1/billing: the ERP organization's plan and payments (billing.erp).
Needs billing.manage. Works while the subscription is lapsed, since paying
is how a lapsed organization gets going again."""
from datetime import datetime
from typing import List, Optional

from django.urls import reverse
from ninja import Router, Schema
from ninja.errors import HttpError

from organizations.app_api import SeatsOut, SubscriptionOut, membership_for, subscription_out
from organizations.workspace import seat_usage
from plans import erp as erp_plans

from . import erp
from .models import OrganizationPayment

router = Router(tags=['billing'])


class PlanChoiceOut(Schema):
    key: str
    label: str
    monthly: int
    users: Optional[int]
    light_users: Optional[int]
    teams: Optional[int]
    warehouses: Optional[int]
    action: str
    amount: str
    message: str


class PaymentOut(Schema):
    id: int
    kind: str
    kind_label: str
    plan_label: str
    amount: str
    extra_light_users: int
    status: str
    status_label: str
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    created_at: datetime
    paid_at: Optional[datetime]


class BillingOut(Schema):
    subscription: SubscriptionOut
    plans: List[PlanChoiceOut]
    payments: List[PaymentOut]
    seats: SeatsOut


class PlanIn(Schema):
    plan: str


class ActionOut(Schema):
    redirect_url: Optional[str]
    message: Optional[str]


def _billing_out(company):
    subscription = erp.subscription_for(company)
    plans = []
    for card in erp_plans.cards():
        q = erp.quote(subscription, card['key'])
        plans.append({
            'key': card['key'], 'label': card['label'], 'monthly': card['monthly'], 'users': card['users'],
            'light_users': card['light_users'], 'teams': card['teams'], 'warehouses': card['warehouses'],
            'action': q.action, 'amount': str(q.amount), 'message': q.message,
        })
    payments = OrganizationPayment.objects.filter(subscription=subscription).exclude(status=OrganizationPayment.CANCELED)[:24]
    return {
        'subscription': subscription_out(company),
        'plans': plans,
        'payments': [{
            'id': p.pk, 'kind': p.kind, 'kind_label': p.get_kind_display(), 'plan_label': erp_plans.PLANS[p.plan]['label'],
            'amount': str(p.amount), 'extra_light_users': p.extra_light_users, 'status': p.status,
            'status_label': p.get_status_display(), 'period_start': p.period_start, 'period_end': p.period_end,
            'created_at': p.created_at, 'paid_at': p.paid_at,
        } for p in payments],
        'seats': seat_usage(company),
    }


@router.get('/billing', response=BillingOut)
def get_billing(request):
    return _billing_out(membership_for(request, 'billing.manage').company)


@router.post('/billing/plan', response=ActionOut)
def choose_plan(request, payload: PlanIn):
    company = membership_for(request, 'billing.manage').company
    try:
        url, message = erp.change_plan(
            company, request.user, payload.plan,
            lambda payment: reverse('billing-erp-return', kwargs={'pk': payment.pk}),
        )
    except erp.BillingError as exc:
        raise HttpError(400, str(exc))
    return {'redirect_url': url, 'message': message}


@router.post('/billing/cancel', response=ActionOut)
def cancel_subscription(request):
    company = membership_for(request, 'billing.manage').company
    try:
        subscription = erp.cancel(company, request.user)
    except erp.BillingError as exc:
        raise HttpError(400, str(exc))
    return {'redirect_url': None,
            'message': f"Your subscription won't renew. You can use MakeSetu until {subscription.expires_at:%d %b %Y}."}


@router.post('/billing/reactivate', response=ActionOut)
def reactivate_subscription(request):
    company = membership_for(request, 'billing.manage').company
    try:
        erp.reactivate(company, request.user)
    except erp.BillingError as exc:
        raise HttpError(400, str(exc))
    return {'redirect_url': None, 'message': "Your subscription will renew automatically again."}
