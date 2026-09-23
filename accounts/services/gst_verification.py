"""
GST verification service layer.

The registration flow (and any future "refresh GST details" action) talks
only to `verify_gstin()` below — never to a provider directly. This keeps
provider credentials and error handling in one place, and lets a real
provider be dropped in later (see `get_provider`) without touching the view
or registration logic.
"""
import logging
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# 2-digit state code, 10-char PAN (5 letters, 4 digits, 1 letter),
# 1-digit entity number, literal 'Z', 1 alphanumeric checksum.
GSTIN_PATTERN = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')

STATE_CODES = {
    '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
    '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi',
    '08': 'Rajasthan', '09': 'Uttar Pradesh', '10': 'Bihar', '11': 'Sikkim',
    '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
    '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam',
    '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha',
    '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
    '26': 'Dadra and Nagar Haveli and Daman and Diu', '27': 'Maharashtra',
    '28': 'Andhra Pradesh', '29': 'Karnataka', '30': 'Goa', '31': 'Lakshadweep',
    '32': 'Kerala', '33': 'Tamil Nadu', '34': 'Puducherry',
    '35': 'Andaman and Nicobar Islands', '36': 'Telangana',
    '37': 'Andhra Pradesh', '38': 'Ladakh',
}

USER_MESSAGES = {
    'invalid_format': "That doesn't look like a valid GSTIN. Please check and try again.",
    'not_found': "We couldn't find this GSTIN with GSTN. Please check and try again.",
    'timeout': "The GST verification service is taking too long to respond. Please try again shortly.",
    'unavailable': "The GST verification service is temporarily unavailable. Please try again shortly.",
    'rate_limited': "Too many verification requests right now. Please try again in a minute.",
    'malformed_response': "We received an unexpected response while verifying this GSTIN. Please try again.",
    'unknown': "We couldn't verify this GSTIN right now. Please try again.",
}


class GSTProviderError(Exception):
    """Base class for provider-level failures."""


class GSTProviderNotFound(GSTProviderError):
    """Provider explicitly reports the GSTIN doesn't exist."""


class GSTProviderTimeout(GSTProviderError):
    pass


class GSTProviderUnavailable(GSTProviderError):
    pass


class GSTProviderRateLimited(GSTProviderError):
    pass


def normalize_gstin(raw_gstin):
    return (raw_gstin or '').strip().upper().replace(' ', '')


def is_valid_gstin_format(gstin):
    return bool(GSTIN_PATTERN.match(gstin))


class BaseGSTProvider:
    """Interface every GST verification provider must implement."""

    def verify(self, gstin):
        """
        Look up `gstin` and return a raw dict with at least `legal_name`
        and `status`. Raise a `GSTProviderError` subclass for any failure —
        never return a falsy/partial result to signal failure.
        """
        raise NotImplementedError


class MockGSTProvider(BaseGSTProvider):
    """
    Deterministic stand-in used when no real GST provider is configured.

    Lets the rest of the flow (name matching, Company persistence, the
    registration UI's states) be built and exercised end-to-end before a
    real provider contract is in place. Swap in a real provider via
    `GST_VERIFICATION_PROVIDER` + `get_provider()` — nothing in the
    registration view or Company persistence needs to change.
    """

    def verify(self, gstin):
        if gstin.startswith('00'):
            raise GSTProviderNotFound(gstin)

        # Last digit of the checksum position drives a deterministic status
        # so the various UI states (active / suspended / cancelled) are
        # reachable in dev/test without a real registry.
        status_by_last_digit = {'0': 'CANCELLED', '1': 'SUSPENDED'}
        status = status_by_last_digit.get(gstin[-1], 'ACTIVE')

        state_code = gstin[:2]
        pan = gstin[2:12]
        return {
            'legal_name': f'BUSINESS {pan} PRIVATE LIMITED',
            'trade_name': f'Business {pan}',
            'status': status,
            'registered_address': 'Registered address on file with GSTN',
            'state': STATE_CODES.get(state_code, ''),
            'city': '',
            'pincode': '',
            'entity_type': 'private_limited',
            'cin': None,
            'mca_status': None,
        }


def get_provider():
    provider_name = getattr(settings, 'GST_VERIFICATION_PROVIDER', 'mock')
    if provider_name == 'mock':
        return MockGSTProvider()
    raise ImproperlyConfigured(
        f"Unknown GST_VERIFICATION_PROVIDER '{provider_name}'. "
        "Register a BaseGSTProvider subclass for it in get_provider()."
    )


def _failure(error_code, message, http_status):
    return {'success': False, 'error_code': error_code, 'message': message, 'http_status': http_status}


def verify_gstin(raw_gstin):
    """
    Normalize, format-validate and verify a GSTIN through the configured
    provider.

    Never raises for expected failure modes: always returns a dict with
    `success`. On failure that dict also carries a user-safe `message` and
    an `http_status` the view can pass straight through; the technical
    detail (provider exceptions, malformed payloads) is logged here, not
    surfaced to the caller.

    On success, returns:
        {
            "success": True,
            "gstin": "33XXXXXXXXXXXXZ",
            "legal_name": "...",
            "trade_name": "...",
            "status": "ACTIVE" | "CANCELLED" | "SUSPENDED" | ...,
            "registered_address": "...",
            "state": "...",
            "city": "...",
            "pincode": "...",
            "entity_type": "...",
            "cin": "..." | None,
            "mca_status": "..." | None,
        }
    """
    gstin = normalize_gstin(raw_gstin)

    if not gstin:
        return _failure('invalid_format', 'GSTIN is required.', 400)
    if not is_valid_gstin_format(gstin):
        return _failure('invalid_format', USER_MESSAGES['invalid_format'], 400)

    provider = get_provider()
    try:
        raw = provider.verify(gstin)
    except GSTProviderNotFound:
        return _failure('not_found', USER_MESSAGES['not_found'], 404)
    except GSTProviderTimeout as exc:
        logger.warning('GST provider timeout for gstin=%s: %s', gstin, exc)
        return _failure('timeout', USER_MESSAGES['timeout'], 504)
    except GSTProviderRateLimited as exc:
        logger.warning('GST provider rate-limited us for gstin=%s: %s', gstin, exc)
        return _failure('rate_limited', USER_MESSAGES['rate_limited'], 429)
    except GSTProviderUnavailable as exc:
        logger.error('GST provider unavailable for gstin=%s: %s', gstin, exc)
        return _failure('unavailable', USER_MESSAGES['unavailable'], 503)
    except GSTProviderError as exc:
        logger.error('GST provider error for gstin=%s: %s', gstin, exc)
        return _failure('unknown', USER_MESSAGES['unknown'], 502)
    except Exception:
        logger.exception('Unexpected error verifying gstin=%s', gstin)
        return _failure('unknown', USER_MESSAGES['unknown'], 500)

    try:
        status = (raw['status'] or '').upper()
        legal_name = raw['legal_name'].strip()
        if not status or not legal_name:
            raise ValueError('empty status/legal_name')
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        logger.error('Malformed GST provider response for gstin=%s: %r (%s)', gstin, raw, exc)
        return _failure('malformed_response', USER_MESSAGES['malformed_response'], 502)

    return {
        'success': True,
        'gstin': gstin,
        'legal_name': legal_name,
        'trade_name': (raw.get('trade_name') or '').strip(),
        'status': status,
        'registered_address': raw.get('registered_address') or '',
        'state': raw.get('state') or '',
        'city': raw.get('city') or '',
        'pincode': raw.get('pincode') or '',
        'entity_type': raw.get('entity_type') or '',
        'cin': raw.get('cin') or None,
        'mca_status': raw.get('mca_status') or None,
    }
