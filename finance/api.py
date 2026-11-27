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
    # A bill from a micro or small supplier, due within 45 days (finance.msme).
    msme: bool
    msme_due_date: Optional[date]


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
        'msme': bool(getattr(r['document'], 'msme_category', '')), 'msme_due_date': getattr(r['document'], 'msme_due_date', None),
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


# --- MSME: paying micro and small suppliers on time (s.43B(h)) -------------------------------------

class MsmeBillOut(Schema):
    id: int
    number: str
    supplier_invoice_number: str
    supplier: PartyOut
    category: str
    invoice_date: date
    msme_due_date: date
    payable: Decimal
    paid_on_time: Decimal
    paid_late: Decimal
    unpaid: Decimal
    days_overdue: int
    # allowed; disallowed (s.43B(h), after the year end); while the year runs, overdue
    # (past due: pay before 31 March to keep the deduction) or open (not due yet).
    effect: str
    disallowed_expense: Decimal
    interest: Decimal


class MsmeSummaryOut(Schema):
    payable: Decimal
    paid_on_time: Decimal
    paid_late: Decimal
    unpaid: Decimal
    disallowed_expense: Decimal
    interest: Decimal
    overdue: Decimal
    open: Decimal


class MsmeDueOut(Schema):
    id: int
    number: str
    supplier: PartyOut
    msme_due_date: date
    days_left: int
    outstanding: Decimal


class MsmeOut(Schema):
    fy: str
    fy_options: List[str]
    start: date
    end: date
    bank_rate: Decimal
    summary: MsmeSummaryOut
    bills: List[MsmeBillOut]
    due_soon: List[MsmeDueOut]


@router.get('/accounts/msme', response=MsmeOut)
def msme_report(request, fy: str = ''):
    """Bills from micro and small suppliers for a financial year: paid on time,
    late, still owed, and what s.43B(h) disallows; plus what falls due this week."""
    from django.utils import timezone

    from . import msme

    membership = viewer(request)
    company = membership.company
    current = msme.current_fy()
    fy = fy or current
    try:
        start, end = msme.fy_bounds(fy)
    except (ValueError, IndexError):
        raise HttpError(400, "Choose a financial year like 2026-27.")
    first = int(current[:4])
    rows = msme.bill_rows(company, start, end)
    today = timezone.localdate()
    return {
        'fy': fy, 'fy_options': [f"{y}-{str(y + 1)[2:]}" for y in range(first, first - 4, -1)], 'start': start, 'end': end,
        'bank_rate': msme.BANK_RATE, 'summary': msme.summary(rows),
        'bills': [{
            'id': r['bill'].pk, 'number': r['bill'].number, 'supplier_invoice_number': r['bill'].supplier_invoice_number,
            'supplier': _party(r['supplier']), 'category': r['category'], 'invoice_date': r['bill'].invoice_date,
            'msme_due_date': r['bill'].msme_due_date, 'payable': r['payable'], 'paid_on_time': r['paid_on_time'],
            'paid_late': r['paid_late'], 'unpaid': r['unpaid'], 'days_overdue': r['days_overdue'], 'effect': r['effect'],
            'disallowed_expense': r['disallowed_expense'], 'interest': r['interest'],
        } for r in rows],
        'due_soon': [{'id': r['document'].pk, 'number': r['number'], 'supplier': _party(r['party']),
                      'msme_due_date': r['document'].msme_due_date, 'days_left': (r['document'].msme_due_date - today).days,
                      'outstanding': r['outstanding']} for r in msme.due_soon(company)],
    }


@router.get('/accounts/msme/form1.csv')
def msme_form1(request, fy: str, half: str):
    """MSME Form 1 for a half year (H1 April-September, H2 October-March):
    what's owed to micro and small suppliers for more than 45 days."""
    import csv
    import io

    from django.http import HttpResponse

    from . import msme

    company = viewer(request).company
    try:
        period = {h: (s, e) for h, s, e in msme.half_years(fy)}[half]
    except (KeyError, ValueError, IndexError):
        raise HttpError(400, "Choose the financial year and H1 or H2.")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Name of the supplier', 'PAN', 'GSTIN', 'Udyam registration', 'Category', 'Bill', 'Invoice date',
                     'Amount due (Rs)', 'Due from', 'Days since acceptance', 'Reason for delay'])
    from organizations.models import BusinessRelationship
    udyam = dict(BusinessRelationship.objects.filter(buyer=company).values_list('supplier_id', 'udyam_number'))
    for r in msme.msme1_rows(company, period[1]):
        s = r['supplier']
        writer.writerow([s.legal_name or s.name, s.pan or '', s.gstin or '', udyam.get(s.pk, ''), r['bill'].msme_category.title(),
                         r['bill'].supplier_invoice_number, r['bill'].invoice_date.isoformat(), f"{r['owed']:.2f}",
                         r['due_from'].isoformat() if r['due_from'] else '', r['days'], ''])
    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="MSME-1_{fy}_{half}.csv"'
    return response
