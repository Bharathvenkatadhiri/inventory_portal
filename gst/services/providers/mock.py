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
    # Manufacturers that both buy and sell, for trying the ERP sign-up.
    # Unlike the ones above, these carry a correct GSTIN check digit.
    '27AAGCN2468V1ZG': ('NASHIK GEARTECH PRIVATE LIMITED', 'Nashik Geartech',
                        'Private Limited Company', 'F-12, MIDC Ambad', 'Nashik', '422010'),
    '09AAHCB2468V1ZP': ('BLUEWAVE CONTROLS PRIVATE LIMITED', 'Bluewave Controls',
                        'Private Limited Company', 'C-58, Sector 63', 'Noida', '201301'),
    '19AAFCH2468V1ZK': ('HOOGHLY FORGINGS PRIVATE LIMITED', 'Hooghly Forgings',
                        'Private Limited Company', '7, Taratala Industrial Estate', 'Kolkata', '700088'),
    '03AAJFP2468V1ZF': ('PANCHNAD FASTENERS', 'Panchnad Fasteners',
                        'Partnership', 'Plot 210, Focal Point Phase VIII', 'Ludhiana', '141010'),
    '08AAKCA2468V1ZP': ('ARAVALI PACKAGING PRIVATE LIMITED', 'Aravali Packaging',
                        'Private Limited Company', 'G1-44, RIICO Sitapura', 'Jaipur', '302022'),
    '33AALCK2468V1ZL': ('KONGU PUMPS AND MOTORS PRIVATE LIMITED', 'Kongu Pumps',
                        'Private Limited Company', '88, SIDCO Industrial Estate, Kurichi', 'Coimbatore', '641021'),
    '29AAMCM2468V1Z7': ('MYSORE AERO COMPONENTS PRIVATE LIMITED', 'Mysore Aero Components',
                        'Private Limited Company', '301, KIADB Hebbal Industrial Area', 'Mysuru', '570016'),
    '37AANCV3579U1ZW': ('VIZAG MARINE FABRICATORS PRIVATE LIMITED', 'Vizag Marine Fabricators',
                        'Private Limited Company', 'Plot 14, Autonagar', 'Visakhapatnam', '530012'),
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
