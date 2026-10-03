"""The provider interface and the normalized result every provider returns.

A provider's verify(gstin) returns a dict with these keys, whatever its API
calls them:

    success                True (a provider only returns for a GSTIN it found)
    gstin                  "33ABCDE1234F1Z5"
    legal_name             "ABC MANUFACTURING PRIVATE LIMITED"
    trade_name             "ABC Manufacturing"
    gst_status             "Active" | "Cancelled" | "Suspended" | "Inactive" | ...
    registration_date      date or None
    cancellation_date      date or None
    taxpayer_type          "Regular", "Composition", ...
    business_constitution  "Private Limited Company", "Proprietorship", ...
    principal_address      {"address": one line, "city", "district", "state", "pincode"}
    state, state_code, pincode
    provider_reference     the provider's id for the lookup, if any
    raw_response           the provider's payload, kept for the audit trail

and raises a gst.exceptions error for anything else (GSTINNotFound when GSTN
has no such GSTIN).
"""
from abc import ABC, abstractmethod
from datetime import datetime

from gst.exceptions import GSTInvalidResponse

ACTIVE = 'Active'

STATE_NAMES = {
    '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh', '05': 'Uttarakhand',
    '06': 'Haryana', '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh', '10': 'Bihar', '11': 'Sikkim',
    '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur', '15': 'Mizoram', '16': 'Tripura',
    '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh',
    '23': 'Madhya Pradesh', '24': 'Gujarat', '26': 'Dadra and Nagar Haveli and Daman and Diu', '27': 'Maharashtra',
    '28': 'Andhra Pradesh', '29': 'Karnataka', '30': 'Goa', '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu',
    '34': 'Puducherry', '35': 'Andaman and Nicobar Islands', '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
}


class GSTProvider(ABC):
    name = ''

    @abstractmethod
    def verify(self, gstin: str) -> dict:
        """Look up a normalized GSTIN and return the normalized result."""
        raise NotImplementedError


def _gstn_date(value):
    """GSTN dates come as "dd/mm/yyyy"; blank or unparseable means none."""
    try:
        return datetime.strptime((value or '').strip(), '%d/%m/%Y').date()
    except ValueError:
        return None


def _status(value):
    return (value or '').strip().title()


def from_gstn_record(gstin, record, provider_reference='', raw_response=None):
    """Normalizes a taxpayer record in GSTN's own field names (lgnm,
    tradeNam, sts, rgdt, cxdt, dty, ctb, pradr), which aggregators such as
    Setu and ClearTax pass through, so their adapters only have to fetch."""
    if not isinstance(record, dict):
        raise GSTInvalidResponse(f"expected a taxpayer record, got {type(record).__name__}")
    legal_name = (record.get('lgnm') or '').strip()
    gst_status = _status(record.get('sts'))
    if not legal_name or not gst_status:
        raise GSTInvalidResponse("taxpayer record has no legal name or status")
    addr = ((record.get('pradr') or {}).get('addr')) or {}
    parts = [addr.get(key) for key in ('bno', 'flno', 'bnm', 'st', 'loc', 'dst')]
    state = (addr.get('stcd') or '').strip() or STATE_NAMES.get(gstin[:2], '')
    pincode = str(addr.get('pncd') or '').strip()
    line = ', '.join(p.strip() for p in parts if p and p.strip())
    return {
        'success': True,
        'gstin': gstin,
        'legal_name': legal_name,
        'trade_name': (record.get('tradeNam') or '').strip(),
        'gst_status': gst_status,
        'registration_date': _gstn_date(record.get('rgdt')),
        'cancellation_date': _gstn_date(record.get('cxdt')),
        'taxpayer_type': (record.get('dty') or '').strip(),
        'business_constitution': (record.get('ctb') or '').strip(),
        'principal_address': {
            'address': f"{line} - {pincode}" if line and pincode else line,
            'city': (addr.get('loc') or addr.get('dst') or '').strip(),
            'district': (addr.get('dst') or '').strip(),
            'state': state,
            'pincode': pincode,
        },
        'state': state,
        'state_code': gstin[:2],
        'pincode': pincode,
        'provider_reference': provider_reference or '',
        'raw_response': raw_response if raw_response is not None else record,
    }
