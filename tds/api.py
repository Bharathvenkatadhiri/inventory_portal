"""/api/v1 endpoints for TDS: the sections to choose from, the monthly
register with what's deposited and what's due, recording a challan, and
the quarter's 26Q rows as CSV. Seeing needs ledger.view or payment.record;
recording a deposit needs payment.record."""
import csv
import io
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.http import HttpResponse
from ninja import Router, Schema
from ninja.errors import HttpError

from inventory.api import ChoiceOut
from organizations import access
from organizations.app_api import membership_for
from purchase.api import PartyOut, _name, _party

from . import rules, services
from .models import TdsDeposit

router = Router(tags=['tds'])
VIEW = ('ledger.view', 'payment.record', 'payment.approve')


def _viewer(request):
    membership = membership_for(request)
    if not any(access.check(membership, code) for code in VIEW):
        raise HttpError(403, "Your role doesn't include accounts.")
    return membership


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.TdsError as exc:
        raise HttpError(400, str(exc))


@router.get('/tds/sections', response=List[ChoiceOut])
def tds_sections(request):
    membership_for(request)
    return [{'value': code, 'label': label} for code, (label, _form_code) in rules.SECTIONS.items()]


class DeductionOut(Schema):
    bill_id: int
    bill_number: str
    supplier_invoice_number: str
    date: date
    supplier: PartyOut
    pan: str
    section: str
    section_label: str
    base: Decimal
    rate: Decimal
    tds: Decimal


class MonthOut(Schema):
    month: date
    section: str
    section_label: str
    deducted: Decimal
    deposited: Decimal
    balance: Decimal
    due_date: date
    overdue: bool
    interest_if_paid_today: Decimal


class DepositOut(Schema):
    id: int
    period: date
    section: str
    section_label: str
    amount: Decimal
    interest: Decimal
    deposited_on: date
    bsr_code: str
    challan_serial: str
    recorded_by: str


class RegisterOut(Schema):
    start: date
    end: date
    tan: str
    deductions: List[DeductionOut]
    months: List[MonthOut]
    deposits: List[DepositOut]


def _label(section):
    return rules.SECTIONS.get(section, (section, ''))[0]


@router.get('/tds/register', response=RegisterOut)
def register(request, quarter: str):
    membership = _viewer(request)
    company = membership.company
    start, end = _run(services.quarter_range, quarter)
    return {
        'start': start, 'end': end, 'tan': company.tan,
        'deductions': [{
            'bill_id': b.pk, 'bill_number': b.number, 'supplier_invoice_number': b.supplier_invoice_number, 'date': b.invoice_date,
            'supplier': _party(b.supplier), 'pan': services.pan_of(b.supplier), 'section': b.tds_section,
            'section_label': _label(b.tds_section), 'base': b.tds_base, 'rate': b.tds_rate, 'tds': b.tds_amount,
        } for b in services.deductions(company, start, end)],
        'months': [{**m, 'section_label': _label(m['section'])} for m in services.monthly_status(company, start, end)],
        'deposits': [{
            'id': d.pk, 'period': d.period, 'section': d.section, 'section_label': _label(d.section), 'amount': d.amount,
            'interest': d.interest, 'deposited_on': d.deposited_on, 'bsr_code': d.bsr_code, 'challan_serial': d.challan_serial,
            'recorded_by': _name(d.created_by),
        } for d in TdsDeposit.objects.filter(company=company, period__gte=start, period__lte=end).select_related('created_by')],
    }


class DepositIn(Schema):
    period: date
    section: str
    amount: Decimal
    interest: Decimal = Decimal('0')
    deposited_on: Optional[date] = None
    bsr_code: str
    challan_serial: str


@router.post('/tds/deposits', response={201: dict})
def record_deposit(request, payload: DepositIn):
    membership = membership_for(request, 'payment.record', write=True)
    deposit = _run(services.record_deposit, membership, payload.dict())
    return 201, {'id': deposit.pk}


@router.get('/tds/26q')
def form_26q(request, quarter: str):
    """The quarter's deductee rows as CSV, to key into the 26Q return utility."""
    membership = _viewer(request)
    rows = _run(services.form_26q_rows, membership.company, quarter)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Deductee PAN', 'Deductee name', 'Section code', 'Date of payment/credit', 'Amount paid/credited',
                     'TDS rate %', 'TDS deducted', 'Date of deduction', 'Bill reference', 'Challan BSR code',
                     'Challan date', 'Challan serial'])
    for r in rows:
        writer.writerow([r['deductee_pan'], r['deductee_name'], r['section_code'], r['date_of_credit'].strftime('%d/%m/%Y'),
                         f"{r['amount_credited']:.2f}", f"{r['rate'].normalize():f}", f"{r['tds_deducted']:.2f}",
                         r['date_of_deduction'].strftime('%d/%m/%Y'), r['bill_reference'], r['bsr_code'],
                         r['challan_date'].strftime('%d/%m/%Y') if r['challan_date'] else '', r['challan_serial']])
    tan = membership.company.tan or 'NO-TAN'
    response = HttpResponse(out.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="26Q_{tan}_{quarter}.csv"'
    response['Cache-Control'] = 'private, no-store'
    return response
