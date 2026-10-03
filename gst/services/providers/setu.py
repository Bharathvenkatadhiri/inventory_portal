"""Setu GST verification adapter.

Waiting on Setu's commercial response, so the HTTP call isn't written yet:
when the contract arrives, implement _fetch() to call their API and return
the taxpayer record, translating failures into gst.exceptions errors. The
rest of the app (service, registration, API, frontend) doesn't change.
Credentials belong in env vars only.
"""
import logging

from gst.exceptions import GSTServiceUnavailable

from .base import GSTProvider, from_gstn_record

logger = logging.getLogger(__name__)


class SetuGSTProvider(GSTProvider):
    name = 'setu'

    def verify(self, gstin):
        record, reference, raw = self._fetch(gstin)
        return from_gstn_record(gstin, record, provider_reference=reference, raw_response=raw)

    def _fetch(self, gstin):
        """Returns (GSTN taxpayer record, Setu's request id, full response).
        Should raise GSTINNotFound for an unknown GSTIN, GSTProviderTimeout /
        GSTServiceUnavailable / GSTProviderRateLimited for transport and
        quota failures, and GSTInvalidResponse for a payload it can't read."""
        logger.error("GST_PROVIDER=setu but the Setu adapter isn't implemented yet (gst/services/providers/setu.py).")
        raise GSTServiceUnavailable("Setu adapter not implemented")
