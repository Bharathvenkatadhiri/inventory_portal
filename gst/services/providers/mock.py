"""A stand-in provider for dev, the test site and the test suite: no
network, deterministic results, so registration can be built and tested
end to end before a real provider contract is in place."""
from datetime import date

from gst.exceptions import GSTINNotFound

from .base import STATE_NAMES, GSTProvider

# Fictional companies the mock recognises, so dev and the test site verify a
# believable name instead of "BUSINESS <PAN> PRIVATE LIMITED". Not real
# GSTINs. Listed in README.md ("Sample companies for GST verification").
# gstin: (legal name, trade name, constitution, street address, city, pincode)
SAMPLE_COMPANIES = {
    # Suppliers / manufacturers
    '33AABCS1234K1Z7': ('SRI LAKSHMI PRECISION ENGINEERING PRIVATE LIMITED', 'Sri Lakshmi Precision',
                        'Private Limited Company', '14, SIDCO Industrial Estate, Guindy', 'Chennai', '600032'),
    '27AADCK5678M1Z3': ('KAVERI CASTINGS PRIVATE LIMITED', 'Kaveri Castings',
                        'Private Limited Company', 'Plot 22, MIDC Bhosari', 'Pune', '411026'),
    '29AAFCV2468P1Z9': ('VEGA SHEET METAL WORKS LLP', 'Vega Sheet Metal',
                        'Limited Liability Partnership', '3rd Phase, Peenya Industrial Area', 'Bengaluru', '560058'),
    '24AAGCR1357Q1Z2': ('RUDRA POLYMERS PRIVATE LIMITED', 'Rudra Polymers',
                        'Private Limited Company', 'Survey 118, Changodar GIDC', 'Ahmedabad', '382213'),
    # Buyers
    '36AAHCT9753L1Z4': ('TEJAS ELECTRONICS PRIVATE LIMITED', 'Tejas Electronics',
                        'Private Limited Company', 'Plot 9, Hardware Park, Raviryal', 'Hyderabad', '501510'),
    '07AAJCN8642R1Z6': ('NORTHLINE INFRASTRUCTURE LIMITED', 'Northline Infra',
                        'Public Limited Company', 'A-41, Okhla Industrial Area Phase II', 'New Delhi', '110020'),
    '32AAKCM3141S1Z8': ('MALABAR AUTOMATION PRIVATE LIMITED', 'Malabar Automation',
                        'Private Limited Company', 'KINFRA Hi-Tech Park, Kalamassery', 'Kochi', '683503'),
    '06AALCH2718T1Z5': ('HARYANA AGRO MACHINES PRIVATE LIMITED', 'Haryana Agro Machines',
                        'Private Limited Company', 'Sector 37, IMT Manesar', 'Gurugram', '122051'),
}

# The last character picks the status, so every UI state is reachable:
# '0' cancelled, '1' suspended, anything else active. A GSTIN starting "00"
# (no such state) isn't found.
_STATUS_BY_LAST_CHAR = {'0': 'Cancelled', '1': 'Suspended'}


class MockGSTProvider(GSTProvider):
    name = 'mock'

    def verify(self, gstin):
        if gstin.startswith('00'):
            raise GSTINNotFound(gstin)
        gst_status = _STATUS_BY_LAST_CHAR.get(gstin[-1], 'Active')
        state_code = gstin[:2]
        state = STATE_NAMES.get(state_code, '')
        if gstin in SAMPLE_COMPANIES:
            legal_name, trade_name, constitution, street, city, pincode = SAMPLE_COMPANIES[gstin]
        else:
            pan = gstin[2:12]
            legal_name, trade_name, constitution = f'BUSINESS {pan} PRIVATE LIMITED', f'Business {pan}', 'Private Limited Company'
            street, city, pincode = 'Registered address on file with GSTN', '', ''
        address = ', '.join(p for p in (street, city) if p) + (f' - {pincode}' if pincode else '')
        return {
            'success': True,
            'gstin': gstin,
            'legal_name': legal_name,
            'trade_name': trade_name,
            'gst_status': gst_status,
            'registration_date': date(2019, 7, 1),
            'cancellation_date': date(2025, 3, 31) if gst_status == 'Cancelled' else None,
            'taxpayer_type': 'Regular',
            'business_constitution': constitution,
            'principal_address': {'address': address, 'city': city, 'district': city, 'state': state, 'pincode': pincode},
            'state': state,
            'state_code': state_code,
            'pincode': pincode,
            'provider_reference': f'MOCK-{gstin}',
            'raw_response': {},
        }
