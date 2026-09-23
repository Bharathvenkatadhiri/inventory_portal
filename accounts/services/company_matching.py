"""
Supporting (non-authoritative) comparison between the name a user types
during registration and the legal name a GST verification provider
returns. GSTIN validity is the primary verification signal — this only
flags likely mismatches for review, it never blocks verification on its
own.
"""
import difflib
import re

LEGAL_SUFFIXES = [
    'private limited', 'pvt limited', 'pvt ltd', 'private ltd',
    'limited liability partnership', 'limited', 'ltd', 'llp',
]

NAME_MATCH_THRESHOLD = 0.85


def normalize_company_name(name):
    if not name:
        return ''
    normalized = name.strip().upper()
    normalized = re.sub(r'\s+', ' ', normalized)
    for suffix in sorted(LEGAL_SUFFIXES, key=len, reverse=True):
        normalized = re.sub(r'\b' + re.escape(suffix.upper()) + r'\.?$', '', normalized).strip()
    normalized = re.sub(r'[.,]', '', normalized)
    return re.sub(r'\s+', ' ', normalized).strip()


def company_names_match(user_provided_name, verified_legal_name):
    """Return True when the two names are likely the same company."""
    a = normalize_company_name(user_provided_name)
    b = normalize_company_name(verified_legal_name)
    if not a or not b:
        return False
    if a == b:
        return True
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return ratio >= NAME_MATCH_THRESHOLD
