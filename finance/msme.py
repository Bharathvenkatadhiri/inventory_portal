"""Paying micro and small suppliers on time: MSMED Act s.15-16 and Income
Tax Act s.43B(h).

- A supplier is covered when we've recorded it as a micro or small
  enterprise (its Udyam registration) doing manufacturing or services.
  Medium enterprises and traders aren't covered.
- It must be paid within the agreed credit period, which can't be more
  than 45 days; with no written agreement, within 15 days. The days run
  from the day the goods or services are accepted; we use the supplier's
  invoice date (goods are inspected and accepted on receipt). When a bill
  is posted its due date is capped at this (purchase.services._post).
- Paid late, the buyer owes compound interest with monthly rests at three
  times the RBI bank rate (s.16), which isn't tax-deductible (s.23).
- s.43B(h): what's still unpaid at the end of the financial year after
  that due date can't be deducted that year, only in the year it's paid.
  The expense is the bill's taxable value; GST taken as input credit isn't
  an expense, so the disallowance is the unpaid share of the taxable value.
- Companies also report what's been owed to micro and small suppliers for
  more than 45 days in MSME Form 1, every half year (msme1_rows).
"""
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal('0')
CENT = Decimal('0.01')
MAX_DAYS = 45
DEFAULT_DAYS = 15
# RBI bank rate (%) used for s.16 interest: 5.75% since June 2025 (RBI monetary policy,
# 6 June 2025). Update when the RBI changes it.
BANK_RATE = Decimal('5.75')
UDYAM = re.compile(r'^UDYAM-[A-Z]{2}-\d{2}-\d{7}$')

MICRO, SMALL, MEDIUM = 'micro', 'small', 'medium'
CATEGORIES = [('', 'Not an MSME / not known'), (MICRO, 'Micro'), (SMALL, 'Small'), (MEDIUM, 'Medium')]
ACTIVITIES = [('', '—'), ('manufacturing', 'Manufacturing'), ('services', 'Services'), ('trading', 'Trading')]


class MsmeError(Exception):
    pass


def clean_udyam(value):
    number = re.sub(r'\s+', '', (value or '')).upper()
    if number and not UDYAM.match(number):
        raise MsmeError("A Udyam registration number looks like UDYAM-TN-03-0012345.")
    return number


def covered(relationship):
    """Whether the supplier is a micro or small enterprise the 45-day rule protects."""
    return bool(relationship and relationship.msme_category in (MICRO, SMALL)
                and relationship.msme_activity in ('manufacturing', 'services'))


def allowed_days(relationship):
    """Days the supplier must be paid in: the agreed credit, at most 45; 15 with none agreed."""
    terms = relationship.payment_terms_days
    return min(terms, MAX_DAYS) if terms is not None else DEFAULT_DAYS


def due_date(relationship, accepted_on):
    return accepted_on + timedelta(days=allowed_days(relationship))


def apply_to_bill(bill):
    """At posting: records whether the bill is covered and its s.15 due
    date, and brings its due date forward to it if needed."""
    from organizations.models import BusinessRelationship

    relationship = BusinessRelationship.objects.filter(buyer=bill.company, supplier=bill.supplier).first()
    if not covered(relationship):
        bill.msme_category, bill.msme_due_date = '', None
        return
    bill.msme_category = relationship.msme_category
    bill.msme_due_date = due_date(relationship, bill.invoice_date)
    if bill.due_date is None or bill.due_date > bill.msme_due_date:
        bill.due_date = bill.msme_due_date


def interest(amount, due, paid_on, rate=BANK_RATE):
    """s.16 interest on `amount` paid on `paid_on` though due on `due`:
    compound, monthly rests, at three times the bank rate."""
    days = (paid_on - due).days
    if days <= 0 or amount <= 0:
        return ZERO
    monthly = rate * 3 / 100 / 12
    months = Decimal(days) / Decimal('30.4375')
    whole = int(months)
    factor = (1 + monthly) ** whole * (1 + monthly * (months - whole))
    return (amount * (factor - 1)).quantize(CENT, rounding=ROUND_HALF_UP)


def fy_bounds(fy):
    """'2026-27' -> (1 Apr 2026, 31 Mar 2027)."""
    start = int(fy[:4])
    return date(start, 4, 1), date(start + 1, 3, 31)


def current_fy(today=None):
    today = today or timezone.localdate()
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def _payments(bill_ids):
    """{bill id: [(payment date, amount)]} from posted payments, oldest first."""
    from finance.models import Payment, PaymentAllocation

    out = {}
    rows = (PaymentAllocation.objects.filter(purchase_bill_id__in=bill_ids, payment__status=Payment.Status.POSTED)
            .values('purchase_bill_id', 'payment__payment_date').annotate(total=Sum('amount')).order_by('payment__payment_date'))
    for r in rows:
        out.setdefault(r['purchase_bill_id'], []).append((r['payment__payment_date'], r['total']))
    return out


def bill_rows(company, start, end, today=None):
    """Bills from micro and small suppliers dated up to `end` that weren't
    fully paid before `start`: what was paid on time, late, and what's
    still owed at `end`, with the s.43B(h) effect for the year."""
    from finance.services import Direction, adjusted_totals
    from purchase.models import PurchaseBill

    today = today or timezone.localdate()
    bills = list(PurchaseBill.objects.filter(company=company, status=PurchaseBill.Status.POSTED, invoice_date__lte=end)
                 .exclude(msme_category='').select_related('supplier').order_by('msme_due_date', 'invoice_date'))
    totals = adjusted_totals(Direction.MADE, bills)
    paid = _payments([b.pk for b in bills])
    rows = []
    for b in bills:
        payable = totals[b.pk]
        payments = paid.get(b.pk, [])
        before = sum((a for d, a in payments if d < start), ZERO)
        if payable - before <= 0:
            continue  # settled in an earlier year
        on_time = sum((a for d, a in payments if start <= d <= end and d <= b.msme_due_date), ZERO)
        late = sum((a for d, a in payments if start <= d <= end and d > b.msme_due_date), ZERO)
        unpaid = max(payable - before - on_time - late, ZERO)
        owed_at = min(today, end)
        days_over = max((owed_at - b.msme_due_date).days, 0) if unpaid else 0
        # s.43B(h). While the year is running, anything unpaid can still be saved by paying
        # before the year ends ('overdue' once past its due date, 'open' before). After the
        # year end, what was unpaid then is disallowed unless it was paid by its due date.
        if not unpaid:
            effect = 'allowed'
        elif today <= end:
            effect = 'overdue' if today > b.msme_due_date else 'open'
        elif b.msme_due_date <= end:
            effect = 'disallowed'
        elif sum((a for d, a in payments if end < d <= b.msme_due_date), ZERO) >= unpaid:
            effect = 'allowed'
        else:
            effect = 'disallowed' if today > b.msme_due_date else 'open'
        share = unpaid / payable if payable else ZERO
        interest_due = sum((interest(a, b.msme_due_date, d) for d, a in payments if d > b.msme_due_date), ZERO)
        if unpaid and b.msme_due_date < today:
            interest_due += interest(unpaid, b.msme_due_date, today)
        rows.append({
            'bill': b, 'supplier': b.supplier, 'category': b.msme_category, 'payable': payable,
            'paid_on_time': on_time, 'paid_late': late, 'unpaid': unpaid, 'days_overdue': days_over,
            'effect': effect,
            'disallowed_expense': (b.taxable_value * share).quantize(CENT, rounding=ROUND_HALF_UP) if effect == 'disallowed' else ZERO,
            'interest': interest_due,
        })
    return rows


def summary(rows):
    out = {k: ZERO for k in ('payable', 'paid_on_time', 'paid_late', 'unpaid', 'disallowed_expense', 'interest')}
    for r in rows:
        for k in out:
            out[k] += r[k]
    out['overdue'] = sum((r['unpaid'] for r in rows if r['effect'] == 'overdue'), ZERO)
    out['open'] = sum((r['unpaid'] for r in rows if r['effect'] == 'open'), ZERO)
    return out


def due_soon(company, days=7, today=None):
    """Open bills from micro and small suppliers falling due within `days`, or overdue."""
    from finance.services import Direction, open_documents

    today = today or timezone.localdate()
    return [r for r in open_documents(company, Direction.MADE)
            if r['document'].msme_due_date and r['document'].msme_due_date <= today + timedelta(days=days)]


def half_years(fy):
    start, end = fy_bounds(fy)
    return [('H1', start, date(start.year, 9, 30)), ('H2', date(start.year, 10, 1), end)]


def msme1_rows(company, period_end):
    """MSME Form 1: amounts owed at `period_end` to micro and small suppliers
    for more than 45 days from acceptance (supplier, PAN, amount, due since)."""
    from finance.services import Direction, adjusted_totals
    from purchase.models import PurchaseBill

    bills = list(PurchaseBill.objects.filter(company=company, status=PurchaseBill.Status.POSTED, invoice_date__lte=period_end)
                 .exclude(msme_category='').select_related('supplier'))
    totals = adjusted_totals(Direction.MADE, bills)
    paid = _payments([b.pk for b in bills])
    out = []
    for b in bills:
        owed = totals[b.pk] - sum((a for d, a in paid.get(b.pk, []) if d <= period_end), ZERO)
        if owed > 0 and (period_end - b.invoice_date).days > MAX_DAYS:
            out.append({'bill': b, 'supplier': b.supplier, 'owed': owed, 'due_from': b.msme_due_date,
                        'days': (period_end - b.invoice_date).days})
    return sorted(out, key=lambda r: (r['supplier'].legal_name or '', r['bill'].invoice_date))
