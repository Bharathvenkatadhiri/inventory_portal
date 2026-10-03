"""What billing needs from a payment gateway. A real gateway (Razorpay,
Cashfree…) is one adapter implementing this; nothing else in billing
changes."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

SUCCEEDED, FAILED, PENDING = 'succeeded', 'failed', 'pending'


@dataclass
class GatewayResult:
    """A payment's state at the gateway."""
    status: str                      # SUCCEEDED, FAILED or PENDING
    gateway_payment_id: str = ''
    reason: str = ''
    mandate_id: str = ''             # saved method for automatic renewals, if one was set up


@dataclass
class GatewayEvent:
    """One webhook delivery, already authenticated and parsed."""
    event_id: str
    event_type: str
    order_id: str
    result: GatewayResult
    payload: dict = field(default_factory=dict)


class InvalidWebhook(Exception):
    """Bad signature or unreadable payload: never processed."""


class PaymentGateway(ABC):
    name = ''

    @abstractmethod
    def create_order(self, payment):
        """Registers `payment` (amount, currency) and returns its order id."""

    @abstractmethod
    def checkout_url(self, payment, return_url):
        """Where to send the payer to pay `payment`."""

    @abstractmethod
    def fetch_status(self, payment):
        """The payment's current state at the gateway (GatewayResult), for
        reconciling when a redirect or webhook is late or missing."""

    @abstractmethod
    def charge_renewal(self, subscription, payment):
        """Charges the subscription's saved payment method for `payment`.
        Returns a GatewayResult; PENDING means the result arrives by webhook."""

    @abstractmethod
    def parse_webhook(self, request):
        """Authenticates and parses a webhook request into a GatewayEvent,
        or raises InvalidWebhook."""
