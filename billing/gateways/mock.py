"""A stand-in gateway for development, the test site and tests.

Checkout happens on MakeSetu's own mock checkout page (billing.views.
mock_checkout), where the payer chooses an outcome: pay, pay with the
webhook delayed (as if the browser closed or the gateway were slow), fail,
or close without paying. The outcome is kept in the cache, as the gateway's
own record, and delivered as an HMAC-signed webhook exactly like a real one.

Renewals charge the saved mock mandate and succeed, unless
settings.BILLING_MOCK_RENEWAL_RESULT is 'fail' (to try the past-due flow).
"""
import hashlib
import hmac
import json
import secrets

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from .base import FAILED, PENDING, SUCCEEDED, GatewayEvent, GatewayResult, InvalidWebhook, PaymentGateway

SIGNATURE_HEADER = 'HTTP_X_MOCK_SIGNATURE'
_STATE_TTL = 60 * 60 * 24 * 30


def _state_key(order_id):
    return f'mock-gateway:{order_id}'


def sign(body):
    return hmac.new(settings.BILLING_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


class MockGateway(PaymentGateway):
    name = 'mock'

    def create_order(self, payment):
        order_id = f'mock_order_{secrets.token_hex(8)}'
        cache.set(_state_key(order_id), {'status': PENDING}, _STATE_TTL)
        return order_id

    def checkout_url(self, payment, return_url):
        return reverse('billing-mock-checkout', kwargs={'order_id': payment.gateway_order_id})

    def record(self, order_id, status, reason=''):
        """The payer's choice on the mock checkout page, as the gateway's record."""
        state = {'status': status, 'reason': reason}
        if status == SUCCEEDED:
            state.update(payment_id=f'mock_pay_{secrets.token_hex(8)}', mandate_id=f'mock_mandate_{secrets.token_hex(6)}')
        cache.set(_state_key(order_id), state, _STATE_TTL)
        return state

    def fetch_status(self, payment):
        state = cache.get(_state_key(payment.gateway_order_id)) or {'status': PENDING}
        return GatewayResult(
            status=state['status'], gateway_payment_id=state.get('payment_id', ''),
            reason=state.get('reason', ''), mandate_id=state.get('mandate_id', ''),
        )

    def charge_renewal(self, subscription, payment):
        if not subscription.gateway_mandate_id:
            return GatewayResult(status=FAILED, reason="No saved payment method for automatic renewal.")
        if getattr(settings, 'BILLING_MOCK_RENEWAL_RESULT', 'succeed') == 'fail':
            self.record(payment.gateway_order_id, FAILED, "Card declined (mock).")
            return GatewayResult(status=FAILED, reason="Card declined (mock).")
        state = self.record(payment.gateway_order_id, SUCCEEDED)
        return GatewayResult(status=SUCCEEDED, gateway_payment_id=state['payment_id'], mandate_id=subscription.gateway_mandate_id)

    def webhook_body(self, order_id, event_id=None):
        """The signed webhook the mock gateway would send for `order_id`."""
        state = cache.get(_state_key(order_id)) or {'status': PENDING}
        event_type = {SUCCEEDED: 'payment.captured', FAILED: 'payment.failed'}.get(state['status'], 'payment.pending')
        body = json.dumps({
            'id': event_id or f'mock_evt_{secrets.token_hex(8)}', 'type': event_type, 'order_id': order_id,
            'payment_id': state.get('payment_id', ''), 'mandate_id': state.get('mandate_id', ''),
            'reason': state.get('reason', ''),
        }).encode()
        return body, sign(body)

    def parse_webhook(self, request):
        signature = request.META.get(SIGNATURE_HEADER, '')
        if not signature or not hmac.compare_digest(signature, sign(request.body)):
            raise InvalidWebhook("bad signature")
        try:
            data = json.loads(request.body)
            status = {'payment.captured': SUCCEEDED, 'payment.failed': FAILED}.get(data['type'], PENDING)
            return GatewayEvent(
                event_id=data['id'], event_type=data['type'], order_id=data['order_id'], payload=data,
                result=GatewayResult(status=status, gateway_payment_id=data.get('payment_id', ''),
                                     reason=data.get('reason', ''), mandate_id=data.get('mandate_id', '')),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidWebhook(f"unreadable payload: {exc}") from exc
