"""Billing pages: choosing a plan, paying for it, managing renewal, and
the gateway's webhook. Only the company's owner manages the subscription
(accounts.team 'subscription.manage'); the lifecycle rules are in
billing.services."""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from accounts import team
from plans import catalog

from . import services
from .gateways import get_gateway
from .gateways.base import FAILED, PENDING, SUCCEEDED, InvalidWebhook
from .models import Payment

logger = logging.getLogger(__name__)


def billing_url():
    return reverse('profile') + '?tab=billing'


def _owner_subscription_or_403(request):
    if not team.can_manage_subscription(request.user):
        messages.error(request, "Only your company's owner can change the plan.")
        return None
    return services.subscription_for(request.user)


def _owner_payment(request, pk):
    """A payment on the requesting owner's own subscription, else 404."""
    payment = get_object_or_404(Payment.objects.select_related('subscription'), pk=pk)
    if not team.can_manage_subscription(request.user) or payment.subscription.user_profile_id != team.owner_user(request.user).email:
        raise Http404
    return payment


def _billed_to(user):
    profile = team.company(user)
    company = getattr(profile, 'company', None)
    name = getattr(profile, 'Name', None) or getattr(profile, 'companyname', None) or ''
    return {
        'name': (company.legal_name if company and company.legal_name else name) or user.get_full_name(),
        'gstin': company.gstin if company else '',
        'email': user.email,
    }


class CheckoutView(LoginRequiredMixin, View):
    """Review before paying: the plan and cycle chosen, the price split
    (an upgrade's proration), the dates and the next renewal, with every
    other plan and cycle one click away. Nothing is created until the owner
    proceeds, which posts to ChangePlanView."""

    def get(self, request):
        subscription = _owner_subscription_or_403(request)
        if subscription is None:
            return redirect(billing_url())
        plan = request.GET.get('plan', '')
        cycle = request.GET.get('cycle') or subscription.billing_cycle or catalog.MONTHLY
        if not catalog.purchasable(plan) or cycle not in dict(catalog.BILLING_CYCLES):
            messages.error(request, "Choose a plan to continue.")
            return redirect(billing_url())
        context = {
            'selected_plan': plan, 'selected_cycle': cycle,
            'options': services.checkout_options(subscription),
            'billed_to': _billed_to(request.user),
        }
        try:
            context['summary'] = services.checkout_summary(subscription, plan, cycle)
        except services.BillingError as exc:
            context['error'] = str(exc)
        return render(request, 'billing/checkout.html', context)


class ChangePlanView(LoginRequiredMixin, View):
    """The owner picks a plan and cycle: pay (new subscription, upgrade or
    overdue renewal), or schedule it for the current expiry."""
    http_method_names = ['post']

    def post(self, request):
        if _owner_subscription_or_403(request) is None:
            return redirect(billing_url())
        plan, cycle = request.POST.get('plan_type', ''), request.POST.get('billing_cycle') or catalog.MONTHLY
        try:
            url, message = services.change_plan(
                request.user, plan, cycle,
                lambda payment: request.build_absolute_uri(reverse('billing-return', kwargs={'pk': payment.pk})),
            )
        except services.BillingError as exc:
            messages.error(request, str(exc))
            return redirect(billing_url())
        if url:
            return redirect(url)
        messages.success(request, message)
        return redirect(billing_url())


class PaymentReturnView(LoginRequiredMixin, View):
    """Where the gateway sends the payer back. The result is checked with
    the gateway itself (never taken from the URL), so this works whether or
    not the webhook has arrived yet, and applies the payment at most once."""
    def get(self, request, pk):
        payment = _owner_payment(request, pk)
        outcome = services.reconcile(payment)
        payment.refresh_from_db()
        if payment.status == Payment.SUCCEEDED and payment.applied:
            # This is the confirmation for a payment made here (no email is sent
            # for it, services._apply), so it says what was bought and until when.
            subscription = payment.subscription
            subscription.refresh_from_db()
            ends = timezone.localtime(subscription.expires_at).strftime('%d %b %Y, %H:%M') if subscription.expires_at else ''
            messages.success(request, f"Payment of ₹{payment.amount} received. You're on {catalog.PLAN_LABELS[subscription.plan_type]}"
                                      + (f" until {ends}, and it renews automatically." if ends and subscription.auto_renew
                                         else f" until {ends}." if ends else "."))
        elif payment.status == Payment.SUCCEEDED:
            messages.warning(request, "Your payment went through, but your plan had changed in the meantime, so it wasn't applied. We'll refund it.")
        elif payment.status == Payment.FAILED:
            messages.error(request, f"The payment didn't go through{': ' + payment.failure_reason if payment.failure_reason else ''}. Your plan hasn't changed.")
        else:
            messages.info(request, "We're waiting for the payment to be confirmed. Your plan will change as soon as it is.")
        logger.info("Payment #%s return: %s", payment.pk, outcome)
        return redirect(billing_url())


class CancelView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request):
        subscription = _owner_subscription_or_403(request)
        if subscription is not None:
            if services.is_live_paid(subscription):
                services.cancel(subscription, request.user)
                messages.success(request, "Automatic renewal is off. You keep your plan until it expires, then move to Free.")
            else:
                messages.info(request, "There's no paid subscription to cancel.")
        return redirect(billing_url())


class ReactivateView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request):
        subscription = _owner_subscription_or_403(request)
        if subscription is not None:
            try:
                services.reactivate(subscription, request.user)
                messages.success(request, "Automatic renewal is back on.")
            except services.BillingError as exc:
                messages.error(request, str(exc))
        return redirect(billing_url())


class CancelScheduledChangeView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request):
        subscription = _owner_subscription_or_403(request)
        if subscription is not None and subscription.scheduled_plan_type:
            services.cancel_scheduled_change(subscription, request.user)
            messages.success(request, "Scheduled change cancelled. Your current plan continues.")
        return redirect(billing_url())


class DismissIntendedPlanView(LoginRequiredMixin, View):
    """"Stay on Free" for a plan chosen at sign-up but not paid for."""
    http_method_names = ['post']

    def post(self, request):
        subscription = _owner_subscription_or_403(request)
        if subscription is not None:
            subscription.intended_plan_type = ''
            subscription.intended_billing_cycle = ''
            subscription.save(update_fields=['intended_plan_type', 'intended_billing_cycle', 'updated_at'])
        next_url = request.POST.get('next', '')
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            next_url = billing_url()
        return redirect(next_url)


# Session key: the renewal alert (services.renewal_alert 'key') closed in
# this session. Cleared at login, so a closed alert shows again next time.
RENEWAL_ALERT_DISMISSED = 'renewal_alert_dismissed'


def forget_dismissed_alerts(sender, request, user, **kwargs):
    """user_logged_in receiver (BillingConfig.ready)."""
    if request is not None and hasattr(request, 'session'):
        request.session.pop(RENEWAL_ALERT_DISMISSED, None)


class DismissRenewalAlertView(LoginRequiredMixin, View):
    """Anyone on the company closes the renewal alert for this session. Only
    the session changes, so it's a personal action (accounts.middleware)."""
    http_method_names = ['post']

    def post(self, request):
        subscription = services.SubscriptionPlan.objects.filter(user_profile=team.owner_user(request.user)).first()
        alert = services.renewal_alert(subscription)
        if alert is not None:
            request.session[RENEWAL_ALERT_DISMISSED] = alert['key']
        if getattr(request, 'htmx', False):
            return HttpResponse(status=204)
        next_url = request.POST.get('next', '')
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            next_url = reverse('home')
        return redirect(next_url)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_not_required, name='dispatch')
class WebhookView(View):
    """POST /billing/webhook/<gateway>/: authenticated by the gateway's
    signature, never by session; each event processed once."""
    http_method_names = ['post']

    def post(self, request, gateway):
        # Only the configured gateway's webhooks are accepted.
        if gateway != settings.BILLING_GATEWAY:
            raise Http404
        adapter = get_gateway(gateway)
        try:
            event = adapter.parse_webhook(request)
        except InvalidWebhook as exc:
            logger.warning("Rejected %s webhook: %s", gateway, exc)
            return HttpResponseBadRequest("invalid webhook")
        result = services.process_event(adapter.name, event)
        return HttpResponse(result)


class MockCheckoutView(LoginRequiredMixin, View):
    """The mock gateway's checkout page (BILLING_GATEWAY=mock only)."""
    OUTCOMES = {
        'pay': (SUCCEEDED, True), 'pay_delay_webhook': (SUCCEEDED, False),
        'fail': (FAILED, True), 'close': (PENDING, False),
    }

    def _payment(self, request, order_id):
        if get_gateway().name != 'mock':
            raise Http404
        payment = get_object_or_404(Payment, gateway='mock', gateway_order_id=order_id)
        return _owner_payment(request, payment.pk)

    def get(self, request, order_id):
        payment = self._payment(request, order_id)
        return render(request, 'billing/mock_checkout.html', {'payment': payment, 'plan_label': catalog.PLAN_LABELS[payment.plan_type]})

    def post(self, request, order_id):
        payment = self._payment(request, order_id)
        status, deliver_webhook = self.OUTCOMES.get(request.POST.get('outcome'), (PENDING, False))
        gateway = get_gateway()
        if status != PENDING and payment.status == Payment.PENDING:
            gateway.record(order_id, status, "Card declined (mock)." if status == FAILED else '')
            if deliver_webhook:
                # As the gateway would: a signed POST to our webhook endpoint.
                body, signature = gateway.webhook_body(order_id)
                event = gateway.parse_webhook(_SignedRequest(body, signature))
                services.process_event(gateway.name, event)
        if request.POST.get('outcome') == 'close':
            messages.info(request, "Checkout closed. Nothing was charged.")
            return redirect(billing_url())
        return redirect(reverse('billing-return', kwargs={'pk': payment.pk}))


class _SignedRequest:
    """The minimum of a request that MockGateway.parse_webhook reads."""
    def __init__(self, body, signature):
        self.body = body
        self.META = {'HTTP_X_MOCK_SIGNATURE': signature}
