"""GST Suvidha Providers (GSPs): who registers e-invoices with the Invoice
Registration Portal (IRP) and generates e-way bills on our behalf.

settings.EINVOICE_PROVIDER picks one. 'mock' works without any account and
behaves like the portals for the rules this app relies on: the IRN is the
SHA-256 of the seller's GSTIN, financial year, document type and number
(so the same document can't be registered twice), cancellation is allowed
for 24 hours, and the signed QR carries the fields the IRP's does. Its
signatures are ours, not the IRP's, and say so, so a mock QR can never pass
for a real one.

A real GSP is added by subclassing Provider and mapping its API; every
method returns the normalized dicts documented below or raises
ProviderError with a message to show.
"""
import base64
import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

CANCEL_WINDOW = timedelta(hours=24)


class ProviderError(Exception):
    """The GSP or the portal refused the request; the message says why."""


class Provider:
    name = ''

    def generate_irn(self, payload):
        """-> {'irn', 'ack_number', 'ack_date' (aware datetime), 'signed_qr', 'raw'}"""
        raise NotImplementedError

    def cancel_irn(self, irn, reason, remark):
        """-> {'cancelled_at', 'raw'}"""
        raise NotImplementedError

    def generate_ewb(self, payload):
        """-> {'number', 'generated_at', 'raw'}"""
        raise NotImplementedError

    def update_vehicle(self, number, payload):
        """-> {'updated_at', 'raw'}"""
        raise NotImplementedError

    def cancel_ewb(self, number, reason, remark):
        """-> {'cancelled_at', 'raw'}"""
        raise NotImplementedError


def irn_for(gstin, fy, doc_type, doc_number):
    """How the IRP makes an IRN: SHA-256 of these, joined."""
    return hashlib.sha256(f"{gstin}{fy}{doc_type}{doc_number}".upper().encode()).hexdigest()


class MockProvider(Provider):
    """For development and demos: no network, no account."""
    name = 'mock'

    def _sign(self, claims):
        header = {'alg': 'HS256', 'typ': 'JWT', 'kid': 'MAKESETU-MOCK-NOT-IRP'}
        part = lambda d: base64.urlsafe_b64encode(json.dumps(d, separators=(',', ':')).encode()).rstrip(b'=').decode()
        body = f"{part(header)}.{part(claims)}"
        signature = hmac.new(settings.SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest()
        return f"{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    def generate_irn(self, payload):
        from .models import EInvoice

        doc = payload['DocDtls']
        seller = payload['SellerDtls']['Gstin']
        irn = irn_for(seller, payload['_fy'], doc['Typ'], doc['No'])
        if EInvoice.objects.filter(irn=irn, status=EInvoice.Status.ACTIVE).exists():
            raise ProviderError("2150: Duplicate IRN. This document is already registered.")
        if EInvoice.objects.filter(irn=irn, status=EInvoice.Status.CANCELLED).exists():
            raise ProviderError("2278: An IRN for this document number was cancelled; a cancelled number can't be used again.")
        now = timezone.now()
        ack = f"{now:%y}{secrets.randbelow(10 ** 13):013d}"
        items = payload['ItemList']
        qr = self._sign({'data': json.dumps({
            'SellerGstin': seller, 'BuyerGstin': payload['BuyerDtls']['Gstin'], 'DocNo': doc['No'], 'DocTyp': doc['Typ'],
            'DocDt': doc['Dt'], 'TotInvVal': payload['ValDtls']['TotInvVal'], 'ItemCnt': len(items),
            'MainHsnCode': max(items, key=lambda i: i['AssAmt'])['HsnCd'], 'Irn': irn,
            'IrnDt': timezone.localtime(now).strftime('%Y-%m-%d %H:%M:%S'),
        }), 'iss': 'MakeSetu mock IRP (not valid for GST)'})
        return {'irn': irn, 'ack_number': ack, 'ack_date': now, 'signed_qr': qr,
                'raw': {'AckNo': ack, 'AckDt': timezone.localtime(now).strftime('%Y-%m-%d %H:%M:%S'), 'Irn': irn, 'Status': 'ACT', 'mock': True}}

    def cancel_irn(self, irn, reason, remark):
        now = timezone.now()
        return {'cancelled_at': now, 'raw': {'Irn': irn, 'CancelDate': timezone.localtime(now).strftime('%Y-%m-%d %H:%M:%S'), 'mock': True}}

    def generate_ewb(self, payload):
        from .models import EwayBill

        while True:
            number = f"{secrets.randbelow(9) + 1}{secrets.randbelow(10 ** 11):011d}"
            if not EwayBill.objects.filter(number=number).exists():
                break
        now = timezone.now()
        return {'number': number, 'generated_at': now,
                'raw': {'ewayBillNo': number, 'ewayBillDate': timezone.localtime(now).strftime('%d/%m/%Y %I:%M:%S %p'), 'mock': True}}

    def update_vehicle(self, number, payload):
        now = timezone.now()
        return {'updated_at': now, 'raw': {'ewbNo': number, 'vehUpdDate': timezone.localtime(now).strftime('%d/%m/%Y %I:%M:%S %p'), 'mock': True}}

    def cancel_ewb(self, number, reason, remark):
        now = timezone.now()
        return {'cancelled_at': now, 'raw': {'ewayBillNo': number, 'cancelDate': timezone.localtime(now).strftime('%d/%m/%Y %I:%M:%S %p'), 'mock': True}}


class NotConfigured(Provider):
    name = 'none'

    def __getattribute__(self, name):
        if name in ('generate_irn', 'cancel_irn', 'generate_ewb', 'update_vehicle', 'cancel_ewb'):
            def refuse(*args, **kwargs):
                raise ProviderError("E-invoicing isn't connected yet: set EINVOICE_PROVIDER to your GSP.")
            return refuse
        return super().__getattribute__(name)


PROVIDERS = {'mock': MockProvider, 'none': NotConfigured}


def get_provider():
    return PROVIDERS.get(getattr(settings, 'EINVOICE_PROVIDER', 'mock'), NotConfigured)()
