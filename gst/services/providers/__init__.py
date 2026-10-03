"""Picks the GST provider from settings.GST_PROVIDER, so switching from the
mock to Setu or ClearTax is an env change, not a code change."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .cleartax import ClearTaxGSTProvider
from .mock import MockGSTProvider
from .setu import SetuGSTProvider

PROVIDERS = {
    MockGSTProvider.name: MockGSTProvider,
    SetuGSTProvider.name: SetuGSTProvider,
    ClearTaxGSTProvider.name: ClearTaxGSTProvider,
}

# Adapters whose API call is still a stub (see setu.py); gst.checks warns
# when one of these is configured.
NOT_IMPLEMENTED = {SetuGSTProvider.name, ClearTaxGSTProvider.name}


def get_gst_provider():
    name = settings.GST_PROVIDER
    if name not in PROVIDERS:
        raise ImproperlyConfigured(f"Unknown GST_PROVIDER {name!r}; expected one of {', '.join(sorted(PROVIDERS))}.")
    return PROVIDERS[name]()
