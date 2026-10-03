"""ClearTax GST verification adapter. Like the Setu one, only _fetch() is
left to write once there's a contract; see setu.py."""
import logging

from gst.exceptions import GSTServiceUnavailable

from .base import GSTProvider, from_gstn_record

logger = logging.getLogger(__name__)


class ClearTaxGSTProvider(GSTProvider):
    name = 'cleartax'

    def verify(self, gstin):
        record, reference, raw = self._fetch(gstin)
        return from_gstn_record(gstin, record, provider_reference=reference, raw_response=raw)

    def _fetch(self, gstin):
        """Returns (GSTN taxpayer record, ClearTax's request id, full
        response), raising gst.exceptions errors as described in setu.py."""
        logger.error("GST_PROVIDER=cleartax but the ClearTax adapter isn't implemented yet (gst/services/providers/cleartax.py).")
        raise GSTServiceUnavailable("ClearTax adapter not implemented")
