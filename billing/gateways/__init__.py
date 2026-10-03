"""Picks the payment gateway from settings.BILLING_GATEWAY."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .mock import MockGateway

GATEWAYS = {MockGateway.name: MockGateway}


def get_gateway(name=None):
    name = name or settings.BILLING_GATEWAY
    if name not in GATEWAYS:
        raise ImproperlyConfigured(f"Unknown BILLING_GATEWAY {name!r}; expected one of {', '.join(sorted(GATEWAYS))}.")
    return GATEWAYS[name]()
