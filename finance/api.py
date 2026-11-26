"""/api/v1 endpoints for money in and out: receivables and payables with
ageing, recording payments against invoices and bills, approving and
cancelling them. Seeing any of it needs payment.record, payment.approve or
ledger.view; recording needs payment.record."""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from inventory.api import ChoiceOut
from organizations import access
from organizations.app_api import membership_for
from purchase.api import PartyOut, _name, _party, _writes_allowed

from . import services
from .models import Payment

router = Router(tags=['accounts'])
VIEW_PERMISSIONS = ('payment.record', 'payment.approve', 'ledger.view')
Direction = Payment.Direction
LIST_LIMIT = 500


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.FinanceError as exc:
        raise HttpError(400, str(exc))


def _can(membership, code):
    return bool(access.check(membership, code))


def viewer(request):
    membership = membership_for(request)
    if not any(_can(membership, code) for code in VIEW_PERMISSIONS):
        raise HttpError(403, "Your role doesn't include accounts.")
    return membership


def _direction(value):
    if value not in Direction.values:
        raise HttpError(400, "direction must be 'received' or 'made'.")
    return value


# --- Outstanding ---------------------------------------------------------------------

class OpenDocumentOut(Schema):
    id: int
    number: str            # the number on the document: ours for invoices, the supplier's for bills
    our_number: str
    order_number: str
    party: PartyOut
    date: date
    due_date: Optional[date]
    total: Decimal
    paid: Decimal
    pending: Decimal
    outstanding: Decimal
    # What a new payment can still settle (outstanding less payments waiting for approval).
    available: Decimal
    status: str
    status_label: str
    bucket: str


class PartyBalanceOut(Schema):
    party: PartyOut
    documents: int
    outstanding: Decimal
    overdue: Decimal
    current: Decimal
    d1_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    d90_plus: Decimal
    advance: Decimal


class BucketOut(Schema):
    key: str
    label: str
    amount: Decimal


class OutstandingOut(Schema):
    direction: str
    total: Decimal
    overdue: Decimal
    buckets: List[BucketOut]
    parties: List[PartyBalanceOut]
    documents: List[OpenDocumentOut]


def _doc_out(r):
    return {
        'id': r['document'].pk, 'number': r['number'], 'our_number': r['our_number'], 'order_number': r['document'].order.number,
        'party': _party(r['party']), 'date': r['date'], 'due_date': r['due_date'], 'total': r['total'], 'paid': r['paid'],
        'pending': r['pending'], 'outstanding': r['outstanding'], 'available': r['available'], 'status': r['status'],
        'status_label': r['status_label'], 'bucket': r['bucket'],
    }


@router.get('/accounts/outstanding', response=OutstandingOut)
def outstanding(request, direction: str, party_id: Optional[int] = None):
    """Receivables (direction=received: customers owe us) or payables
    (direction=made: we owe suppliers), with ageing by days overdue."""
    membership = viewer(request)
    direction = _direction(direction)
    rows = services.open_documents(membership.company, direction, party_id)
    per_party, totals = services.ageing_summary(rows)
    advances = services.advances(membership.company, direction)
    return {
        'direction': direction,
        'total': sum((r['outstanding'] for r in rows), Decimal('0')),
        'overdue': sum((r['outstanding'] for r in rows if r['bucket'] != 'current'), Decimal('0')),
        'buckets': [{'key': key, 'label': label, 'amount': totals[key]} for key, label in services.AGEING],
        'parties': [{
            'party': _party(p['party']), 'documents': p['documents'], 'outstanding': p['outstanding'], 'overdue': p['overdue'],
            'current': p['current'], 'd1_30': p['1_30'], 'd31_60': p['31_60'], 'd61_90': p['61_90'], 'd90_plus': p['90_plus'],
            'advance': advances.get(p['party'].pk, Decimal('0')),
        } for p in per_party],
        'documents': [_doc_out(r) for r in rows],
    }


# --- Choices for the payment form ------------------------------------------------------------

class OurBankAccountOut(Schema):
    id: int
    label: str
    is_default: bool


class PaymentOptionsOut(Schema):
    parties: List[PartyOut]
    bank_accounts: List[OurBankAccountOut]
    modes: List[ChoiceOut]
    needs_approval: bool


@router.get('/accounts/payment-options', response=PaymentOptionsOut)
def payment_options(request, direction: str):
    membership = viewer(request)
    direction = _direction(direction)
    company = membership.company
    return {
        'parties': [_party(c) for c in services.parties(company, direction)],
        # Only the last four digits: the full number isn't needed to choose one.
        'bank_accounts': [{'id': b.pk, 'label': f"{b.bank_name} ····{b.account_number[-4:]}", 'is_default': b.is_default}
                          for b in company.bank_accounts.all()],
        'modes': [{'value': v, 'label': l} for v, l in Payment.Mode.choices],
        'needs_approval': services._needs_approval(membership, direction),
    }


# --- Payments -----------------------------------------------------------------------------------

class PaymentAllocationIn(Schema):
    document_id: int
    amount: Decimal


class PartyPaymentIn(Schema):
    direction: str
    party_id: int
    amount: Decimal
    payment_date: Optional[date] = None
    mode: str = Payment.Mode.BANK
    reference: str = ''
    bank_account_id: Optional[int] = None
    notes: str = ''
    allocations: List[PaymentAllocationIn] = []


class PaymentAllocationOut(Schema):
    document_id: int
    number: str
    amount: Decimal


class PartyPaymentActionsOut(Schema):
    approve: bool
    cancel: bool


class PartyPaymentOut(Schema):
    id: int
    number: str
    direction: str
    direction_label: str
    party: PartyOut
    status: str
    status_label: str
    payment_date: date
    amount: Decimal
    unallocated: Decimal
    mode: str
    mode_label: str
    reference: str
    bank_account: str
    notes: str
    created_by: str
    approved_by: str
    rejection_reason: str
    cancel_reason: str
    allocations: List[PaymentAllocationOut]
    warnings: List[str]
    actions: PartyPaymentActionsOut


def _payment_out(p, membership):
    writable = _writes_allowed(membership)
    allocations = []
    for a in p.allocations.select_related('sales_invoice', 'purchase_bill'):
        doc = a.sales_invoice or a.purchase_bill
        allocations.append({'document_id': doc.pk, 'amount': a.amount,
                            'number': doc.number if a.sales_invoice_id else doc.supplier_invoice_number})
    return {
        'id': p.pk, 'number': p.number, 'direction': p.direction, 'direction_label': p.get_direction_display(),
        'party': _party(p.party), 'status': p.status, 'status_label': p.get_status_display(), 'payment_date': p.payment_date,
        'amount': p.amount, 'unallocated': p.amount - sum((a['amount'] for a in allocations), Decimal('0')),
        'mode': p.mode, 'mode_label': p.get_mode_display(), 'reference': p.reference,
        'bank_account': f"{p.bank_account.bank_name} ····{p.bank_account.account_number[-4:]}" if p.bank_account else '',
        'notes': p.notes, 'created_by': _name(p.created_by), 'approved_by': _name(p.approved_by),
        'rejection_reason': p.rejection_reason, 'cancel_reason': p.cancel_reason, 'allocations': allocations,
        'warnings': services.warnings_for(p),
        'actions': {
            'approve': p.status == Payment.Status.PENDING_APPROVAL and writable and _can(membership, 'payment.approve'),
            'cancel': p.status == Payment.Status.POSTED and writable and _can(membership, 'payment.record'),
        },
    }


def _payment(membership, payment_id):
    return get_object_or_404(Payment.objects.select_related('party', 'bank_account', 'created_by', 'approved_by'),
                             pk=payment_id, company=membership.company)


@router.get('/payments', response=List[PartyPaymentOut])
def list_payments(request, direction: str = '', status: str = ''):
    membership = viewer(request)
    payments = Payment.objects.filter(company=membership.company).select_related('party', 'bank_account', 'created_by', 'approved_by')
    if direction:
        payments = payments.filter(direction=_direction(direction))
    if status:
        payments = payments.filter(status=status)
    return [_payment_out(p, membership) for p in payments[:LIST_LIMIT]]


@router.post('/payments', response=PartyPaymentOut)
def record_payment(request, payload: PartyPaymentIn):
    membership = membership_for(request, 'payment.record', write=True)
    payment = _run(services.record_payment, membership, payload.dict())
    return _payment_out(_payment(membership, payment.pk), membership)


class ReasonIn(Schema):
    reason: str = ''


@router.post('/payments/{payment_id}/approve', response=PartyPaymentOut)
def approve_payment(request, payment_id: int):
    membership = membership_for(request, 'payment.approve', write=True)
    payment = _run(services.approve_payment, membership, _payment(membership, payment_id))
    return _payment_out(_payment(membership, payment.pk), membership)


@router.post('/payments/{payment_id}/reject', response=PartyPaymentOut)
def reject_payment(request, payment_id: int, payload: ReasonIn):
    membership = membership_for(request, 'payment.approve', write=True)
    payment = _run(services.reject_payment, membership, _payment(membership, payment_id), payload.reason)
    return _payment_out(_payment(membership, payment.pk), membership)


@router.post('/payments/{payment_id}/cancel', response=PartyPaymentOut)
def cancel_payment(request, payment_id: int, payload: ReasonIn):
    membership = membership_for(request, 'payment.record', write=True)
    payment = _run(services.cancel_payment, membership, _payment(membership, payment_id), payload.reason)
    return _payment_out(_payment(membership, payment.pk), membership)
