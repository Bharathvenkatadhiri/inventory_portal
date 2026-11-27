"""TDS on supplier bills, deposits, and the quarterly 26Q rows.

A bill's TDS is worked out when it's posted, from the section set on the
supplier relationship (BusinessRelationship.tds_section) and the
supplier's other posted bills in the same financial year:

- 194C / 194J: nothing until a threshold is crossed; once the year's
  total crosses the yearly limit, TDS covers the whole year so far less
  what was already taxed (catching up earlier small bills). A single 194C
  bill over ₹30,000 is taxed on its own.
- 194Q: only the part of the year's purchases above ₹50 lakh, and only
  when the company has 194Q switched on.

TDS is rounded to the nearest rupee (s.288B). Cancelling a bill doesn't
re-work later bills' TDS.
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from organizations.models import BusinessRelationship
from purchase.models import PurchaseBill

from . import rules
from .models import TdsDeposit

ZERO, RUPEE = Decimal('0'), Decimal('1')
MONTHLY_INTEREST = Decimal('1.5')  # % per month or part, for deposits after the due date (s.201(1A))


class TdsError(Exception):
    pass


def financial_year(day):
    start_year = day.year if day.month >= 4 else day.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def pan_of(company):
    pan = (company.pan or (company.gstin or '')[2:12]).upper()
    return pan if len(pan) == 10 else ''


def section_for(bill):
    rel = BusinessRelationship.objects.filter(buyer=bill.company, supplier=bill.supplier).first()
    section = rel.tds_section if rel else ''
    if section == rules.Q_GOODS and not bill.company.tds_194q:
        return ''
    return section


def apply_to_bill(bill):
    """Sets the bill's TDS fields (not saved: the caller saves the bill)."""
    section = section_for(bill)
    if not section:
        bill.tds_section, bill.tds_rate, bill.tds_base, bill.tds_amount = '', ZERO, ZERO, ZERO
        return bill
    fy_start, fy_end = financial_year(bill.invoice_date)
    earlier = (PurchaseBill.objects.filter(company=bill.company, supplier=bill.supplier, status=PurchaseBill.Status.POSTED,
                                           invoice_date__gte=fy_start, invoice_date__lte=fy_end)
               .exclude(pk=bill.pk).aggregate(total=Sum('taxable_value'), taxed=Sum('tds_base')))
    earlier_total, already_taxed = earlier['total'] or ZERO, earlier['taxed'] or ZERO
    year_total = earlier_total + bill.taxable_value

    if section == rules.Q_GOODS:
        base = max(ZERO, year_total - rules.Q_EXEMPT_UP_TO) - already_taxed
    elif year_total > rules.AGGREGATE_LIMIT[section]:
        base = year_total - already_taxed  # catches up earlier bills below the threshold
    elif bill.taxable_value > rules.SINGLE_LIMIT.get(section, year_total + 1):
        base = bill.taxable_value
    else:
        base = ZERO
    base = max(base, ZERO)
    rate = rules.rate(section, pan_of(bill.supplier))
    bill.tds_section, bill.tds_rate, bill.tds_base = section, rate, base
    bill.tds_amount = (base * rate / 100).quantize(RUPEE, rounding=ROUND_HALF_UP)
    return bill


# --- Register and deposits --------------------------------------------------------------------

def _month(day):
    return day.replace(day=1)


def deductions(company, start, end):
    """Posted bills with TDS, dated between start and end."""
    return (PurchaseBill.objects.filter(company=company, status=PurchaseBill.Status.POSTED, tds_amount__gt=0,
                                        invoice_date__gte=start, invoice_date__lte=end)
            .select_related('supplier').order_by('invoice_date', 'id'))


def interest(amount, deducted_on, deposited_on):
    """1.5% a month or part of a month, from deduction to deposit, when late."""
    if deposited_on <= rules.due_date(deducted_on):
        return ZERO
    months = (deposited_on.year - deducted_on.year) * 12 + deposited_on.month - deducted_on.month + 1
    return (amount * MONTHLY_INTEREST / 100 * months).quantize(RUPEE, rounding=ROUND_HALF_UP)


def monthly_status(company, start, end, today=None):
    """Per month and section: deducted, deposited, balance, due date, overdue."""
    today = today or timezone.localdate()
    deducted = defaultdict(lambda: ZERO)
    for bill in deductions(company, start, end):
        deducted[(_month(bill.invoice_date), bill.tds_section)] += bill.tds_amount
    deposited = defaultdict(lambda: ZERO)
    for dep in TdsDeposit.objects.filter(company=company, period__gte=_month(start), period__lte=end):
        deposited[(dep.period, dep.section)] += dep.amount
    rows = []
    for (month, section) in sorted(set(deducted) | set(deposited)):
        balance = deducted[(month, section)] - deposited[(month, section)]
        due = rules.due_date(month)
        rows.append({'month': month, 'section': section, 'deducted': deducted[(month, section)],
                     'deposited': deposited[(month, section)], 'balance': balance, 'due_date': due,
                     'overdue': balance > 0 and today > due,
                     'interest_if_paid_today': interest(balance, month, today) if balance > 0 else ZERO})
    return rows


@transaction.atomic
def record_deposit(membership, data):
    company = membership.company
    section = data.get('section')
    if section not in rules.SECTIONS:
        raise TdsError("Choose the section the TDS was deducted under.")
    if not data.get('period'):
        raise TdsError("Choose the month the TDS was deducted in.")
    period = _month(data['period'])
    deposited_on = data.get('deposited_on') or timezone.localdate()
    if deposited_on > timezone.localdate():
        raise TdsError("The deposit date can't be in the future.")
    if deposited_on < period:
        raise TdsError("TDS can't be deposited before the month it was deducted in.")
    amount = Decimal(str(data.get('amount') or 0))
    if amount <= 0:
        raise TdsError("Enter the amount deposited.")
    bsr, serial = (data.get('bsr_code') or '').strip(), (data.get('challan_serial') or '').strip()
    if not (bsr.isdigit() and len(bsr) == 7):
        raise TdsError("The BSR code is 7 digits (on the challan counterfoil).")
    if not (serial.isdigit() and 1 <= len(serial) <= 5):
        raise TdsError("The challan serial number is up to 5 digits.")
    serial = serial.zfill(5)  # stored padded, as on the challan
    if TdsDeposit.objects.filter(company=company, bsr_code=bsr, challan_serial=serial, deposited_on=deposited_on, section=section).exists():
        raise TdsError("This challan is already recorded for that section.")
    return TdsDeposit.objects.create(
        company=company, period=period, section=section, amount=amount,
        interest=Decimal(str(data.get('interest') or 0)), deposited_on=deposited_on, bsr_code=bsr,
        challan_serial=serial, created_by=membership.user,
    )


# --- Form 26Q ------------------------------------------------------------------------------

def quarter_range(value):
    """'2026-Q3' -> the dates of Oct-Dec 2026 (Q1 is April-June of the FY starting that year)."""
    import re
    match = re.fullmatch(r'(\d{4})-Q([1-4])', value or '')
    if not match:
        raise TdsError("Give the quarter as YYYY-Qn, e.g. 2026-Q3 (Q1 is April–June).")
    year, q = int(match[1]), int(match[2])
    start_month = 3 * q + 1  # Q1 -> April
    start = date(year + (1 if start_month > 12 else 0), (start_month - 1) % 12 + 1, 1)
    end_month = start.month + 2
    import calendar
    return start, date(start.year, end_month, calendar.monthrange(start.year, end_month)[1])


def form_26q_rows(company, quarter):
    """Deductee rows for the quarter, matched to deposits by month and
    section. When a month has several challans, deductions are matched to
    them in date order by amount: each goes to the first challan with
    enough left to cover it (or, failing that, the one with the most left)."""
    start, end = quarter_range(quarter)
    pools = defaultdict(list)  # (month, section): [[challan, amount not yet matched]]
    for dep in TdsDeposit.objects.filter(company=company, period__gte=start, period__lte=end).order_by('deposited_on', 'id'):
        pools[(dep.period, dep.section)].append([dep, dep.amount])
    rows = []
    for bill in deductions(company, start, end):
        pool = pools.get((_month(bill.invoice_date), bill.tds_section), [])
        slot = next((s for s in pool if s[1] >= bill.tds_amount), None) or (max(pool, key=lambda s: s[1]) if pool else None)
        challan = None
        if slot is not None:
            challan = slot[0]
            slot[1] -= bill.tds_amount
        rows.append({
            'deductee_pan': pan_of(bill.supplier) or 'PANNOTAVBL',
            'deductee_name': bill.supplier.legal_name or bill.supplier.name,
            'section_code': rules.SECTIONS[bill.tds_section][1],
            'date_of_credit': bill.invoice_date, 'amount_credited': bill.tds_base, 'rate': bill.tds_rate,
            'tds_deducted': bill.tds_amount, 'date_of_deduction': bill.invoice_date,
            'bill_reference': f"{bill.supplier_invoice_number} / {bill.number}",
            'bsr_code': challan.bsr_code if challan else '', 'challan_date': challan.deposited_on if challan else None,
            'challan_serial': challan.challan_serial if challan else '',
        })
    return rows
