"""Payments and what's outstanding. Callers (finance.api) check the plain
permission; this checks the party, the documents and the amounts, and
raises FinanceError with a message to show.

A document's balance: its total, less what posted payments have settled
("paid"), less what payments waiting for approval would settle ("pending").
New allocations can only use what's left, so the same bill can't be paid
twice while a payment waits for approval.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import Company
from organizations import access
from organizations.models import BankAccount, BusinessRelationship
from purchase.models import PurchaseBill
from purchase.services import _decimal, approvals_on, next_number
from sales.models import SalesInvoice

from .models import Payment, PaymentAllocation

Direction, Status = Payment.Direction, Payment.Status
ZERO = Decimal('0')
CASH_RECEIPT_LIMIT = Decimal('200000')   # Income Tax Act s.269ST: no cash receipt of ₹2 lakh or more
CASH_PAYMENT_LIMIT = Decimal('10000')    # s.40A(3): cash payments above ₹10,000 a day aren't deductible
MAX_ALLOCATIONS = 200


class FinanceError(Exception):
    """A change that isn't allowed; the message says why."""


# --- Documents and their balances --------------------------------------------------------

def _doc_field(direction):
    return 'sales_invoice' if direction == Direction.RECEIVED else 'purchase_bill'


def _documents(company, direction):
    if direction == Direction.RECEIVED:
        return SalesInvoice.objects.filter(company=company, status=SalesInvoice.Status.POSTED)
    return PurchaseBill.objects.filter(company=company, status=PurchaseBill.Status.POSTED)


def settled(direction, document_ids):
    """{document id: (paid, pending)} from posted and waiting payments."""
    field = _doc_field(direction)
    rows = (PaymentAllocation.objects.filter(**{f'{field}_id__in': list(document_ids)})
            .exclude(payment__status=Status.CANCELLED)
            .values(f'{field}_id', 'payment__status').annotate(total=Sum('amount')))
    out = {}
    for row in rows:
        paid, pending = out.get(row[f'{field}_id'], (ZERO, ZERO))
        if row['payment__status'] == Status.POSTED:
            paid += row['total']
        else:
            pending += row['total']
        out[row[f'{field}_id']] = (paid, pending)
    return out


def adjusted_totals(direction, documents):
    """{document id: total + debit notes - credit notes} (posted notes only)."""
    from adjustments.models import CreditDebitNote
    from adjustments.services import adjustments_for
    side = CreditDebitNote.Side.SALES if direction == Direction.RECEIVED else CreditDebitNote.Side.PURCHASE
    notes = adjustments_for(side, [d.pk for d in documents])
    return {d.pk: d.total + notes.get(d.pk, (ZERO, ZERO))[0] - notes.get(d.pk, (ZERO, ZERO))[1] for d in documents}


def payment_state(total, paid, pending, due_date, today=None):
    """(key, label) for a posted invoice or bill; `total` after credit and debit notes."""
    today = today or timezone.localdate()
    outstanding = total - paid
    if outstanding < 0:
        return 'credit', f"Credit ₹{-outstanding:,.2f} due back"
    if outstanding == 0:
        return 'paid', 'Paid'
    if due_date and due_date < today:
        return 'overdue', f"Overdue {(today - due_date).days} days"
    if paid > 0:
        return 'partly_paid', 'Partly paid'
    return 'unpaid', 'Unpaid'


def balance(document):
    """Money facts for one posted invoice or bill (for the documents' own APIs)."""
    direction = Direction.RECEIVED if isinstance(document, SalesInvoice) else Direction.MADE
    paid, pending = settled(direction, [document.pk]).get(document.pk, (ZERO, ZERO))
    total = adjusted_totals(direction, [document])[document.pk]
    key, label = payment_state(total, paid, pending, document.due_date)
    posted = document.status == 'posted'
    return {'adjusted_total': total, 'paid': paid, 'payment_pending': pending, 'outstanding': total - paid,
            'payment_status': key if posted else '', 'payment_status_label': label if posted else ''}


def has_live_payments(document):
    return document.allocations.exclude(payment__status=Status.CANCELLED).exists()


AGEING = [('current', 'Not yet due'), ('1_30', '1–30 days'), ('31_60', '31–60 days'), ('61_90', '61–90 days'), ('90_plus', 'Over 90 days')]


def _bucket(due_date, today):
    if not due_date or due_date >= today:
        return 'current'
    days = (today - due_date).days
    return '1_30' if days <= 30 else '31_60' if days <= 60 else '61_90' if days <= 90 else '90_plus'


def open_documents(company, direction, party_id=None):
    """Posted invoices (received) or bills (made) with something left to
    settle, oldest due first, each with its balance and ageing bucket."""
    docs = _documents(company, direction).select_related('customer' if direction == Direction.RECEIVED else 'supplier', 'order')
    if party_id:
        docs = docs.filter(**{('customer_id' if direction == Direction.RECEIVED else 'supplier_id'): party_id})
    docs = list(docs)
    sums = settled(direction, [d.pk for d in docs])
    totals = adjusted_totals(direction, docs)
    today = timezone.localdate()
    out = []
    for d in docs:
        paid, pending = sums.get(d.pk, (ZERO, ZERO))
        total = totals[d.pk]
        available = total - paid - pending
        if total - paid <= 0:
            continue
        party = d.customer if direction == Direction.RECEIVED else d.supplier
        key, label = payment_state(total, paid, pending, d.due_date, today)
        out.append({
            'document': d, 'party': party,
            'number': d.number if direction == Direction.RECEIVED else d.supplier_invoice_number,
            'our_number': d.number, 'date': d.invoice_date, 'due_date': d.due_date, 'total': total, 'paid': paid,
            'pending': pending, 'outstanding': total - paid, 'available': max(available, ZERO),
            'status': key, 'status_label': label, 'bucket': _bucket(d.due_date, today),
        })
    out.sort(key=lambda r: (r['due_date'] or r['date'], r['date']))
    return out


def ageing_summary(rows):
    """Totals per party and per ageing bucket."""
    parties = {}
    totals = {key: ZERO for key, _ in AGEING}
    for r in rows:
        p = parties.setdefault(r['party'].pk, {'party': r['party'], 'outstanding': ZERO, 'overdue': ZERO, 'documents': 0,
                                               **{key: ZERO for key, _ in AGEING}})
        p['outstanding'] += r['outstanding']
        p[r['bucket']] += r['outstanding']
        if r['bucket'] != 'current':
            p['overdue'] += r['outstanding']
        p['documents'] += 1
        totals[r['bucket']] += r['outstanding']
    return sorted(parties.values(), key=lambda p: (-p['overdue'], -p['outstanding'])), totals


# --- Recording payments ------------------------------------------------------------------

def parties(company, direction):
    """Companies we can receive from (customers) or pay (suppliers):
    anyone we have a relationship with in that direction, in any status,
    since money can still be owed after a relationship is blocked."""
    if direction == Direction.RECEIVED:
        ids = BusinessRelationship.objects.filter(supplier=company).values('buyer_id')
    else:
        ids = BusinessRelationship.objects.filter(buyer=company).values('supplier_id')
    return Company.objects.filter(pk__in=ids).order_by('name')


def _needs_approval(membership, direction):
    return (direction == Direction.MADE and approvals_on(membership.company)
            and not access.check(membership, 'payment.approve'))


def warnings_for(payment):
    if payment.mode == Payment.Mode.CASH and payment.direction == Direction.MADE and payment.amount > CASH_PAYMENT_LIMIT:
        return ["Cash payments over ₹10,000 to one party in a day aren't allowed as a business expense (Income Tax Act "
                "s.40A(3)). Pay by bank or UPI where you can."]
    return []


@transaction.atomic
def record_payment(membership, data):
    """Records money received or paid, allocated to that party's open
    documents. Unallocated money stays as an advance."""
    company = membership.company
    direction = data.get('direction')
    if direction not in Direction.values:
        raise FinanceError("Say whether money was received or paid.")
    party = parties(company, direction).filter(pk=data.get('party_id')).first()
    if party is None:
        raise FinanceError("Choose one of your customers." if direction == Direction.RECEIVED else "Choose one of your suppliers.")
    amount = _decimal(data.get('amount'), 'the amount').quantize(Decimal('0.01'))
    if amount <= 0:
        raise FinanceError("The amount must be more than 0.")
    payment_date = data.get('payment_date') or timezone.localdate()
    if payment_date > timezone.localdate():
        raise FinanceError("The payment date can't be in the future.")
    mode = data.get('mode') or Payment.Mode.BANK
    if mode not in Payment.Mode.values:
        raise FinanceError("Choose how it was paid.")
    if mode == Payment.Mode.CASH and direction == Direction.RECEIVED and amount >= CASH_RECEIPT_LIMIT:
        raise FinanceError("Cash receipts of ₹2,00,000 or more aren't allowed (Income Tax Act s.269ST). "
                           "Ask the customer to pay by bank transfer or cheque.")
    bank_account = None
    if data.get('bank_account_id'):
        bank_account = BankAccount.objects.filter(company=company, pk=data['bank_account_id']).first()
        if bank_account is None:
            raise FinanceError("Choose one of your bank accounts.")

    rows = [r for r in (data.get('allocations') or []) if _decimal(r.get('amount'), 'an allocation') != 0]
    if len(rows) > MAX_ALLOCATIONS:
        raise FinanceError(f"A payment can settle up to {MAX_ALLOCATIONS} documents.")
    field = _doc_field(direction)
    party_field = 'customer' if direction == Direction.RECEIVED else 'supplier'
    # Lock the documents being settled so two payments can't both use the same balance.
    docs = {d.pk: d for d in _documents(company, direction).select_for_update()
            .filter(**{party_field: party}, pk__in=[r.get('document_id') for r in rows])}
    sums = settled(direction, docs.keys())
    totals = adjusted_totals(direction, docs.values())
    allocations, allocated = [], ZERO
    for row in rows:
        doc = docs.get(row.get('document_id'))
        if doc is None:
            raise FinanceError(f"A document isn't one of {party.name}'s posted {'invoices' if direction == Direction.RECEIVED else 'bills'}.")
        share = _decimal(row.get('amount'), 'an allocation').quantize(Decimal('0.01'))
        if share < 0:
            raise FinanceError("An allocation can't be negative.")
        paid, pending = sums.get(doc.pk, (ZERO, ZERO))
        available = totals[doc.pk] - paid - pending
        label = doc.number if direction == Direction.RECEIVED else doc.supplier_invoice_number
        if share > available:
            raise FinanceError(f"{label} has only ₹{available:,.2f} left to settle.")
        sums[doc.pk] = (paid, pending + share)
        allocations.append(PaymentAllocation(**{field: doc}, amount=share))
        allocated += share
    if allocated > amount:
        raise FinanceError(f"The allocations add up to ₹{allocated:,.2f}, more than the ₹{amount:,.2f} {'received' if direction == Direction.RECEIVED else 'paid'}.")

    pending = _needs_approval(membership, direction)
    payment = Payment.objects.create(
        company=company, direction=direction, party=party,
        number=next_number(company, 'erp_rcpt' if direction == Direction.RECEIVED else 'erp_pay',
                           'RCPT' if direction == Direction.RECEIVED else 'PAY', payment_date),
        status=Status.PENDING_APPROVAL if pending else Status.POSTED, payment_date=payment_date, amount=amount, mode=mode,
        reference=(data.get('reference') or '').strip()[:60], bank_account=bank_account,
        notes=(data.get('notes') or '').strip()[:300], created_by=membership.user,
        approved_by=None if pending else membership.user, approved_at=None if pending else timezone.now(),
    )
    for allocation in allocations:
        allocation.payment = payment
    PaymentAllocation.objects.bulk_create(allocations)
    return payment


def _locked(payment, *statuses):
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if statuses and payment.status not in statuses:
        raise FinanceError(f"This payment is {payment.get_status_display().lower()}.")
    return payment


@transaction.atomic
def approve_payment(membership, payment):
    payment = _locked(payment, Status.PENDING_APPROVAL)
    payment.status = Status.POSTED
    payment.approved_by, payment.approved_at = membership.user, timezone.now()
    payment.save()
    return payment


@transaction.atomic
def reject_payment(membership, payment, reason):
    """A rejected payment is cancelled: it was never made."""
    reason = (reason or '').strip()
    if not reason:
        raise FinanceError("Say why it's rejected.")
    payment = _locked(payment, Status.PENDING_APPROVAL)
    payment.status = Status.CANCELLED
    payment.rejection_reason = reason[:300]
    payment.cancelled_at = timezone.now()
    payment.save()
    return payment


@transaction.atomic
def cancel_payment(membership, payment, reason):
    """Cancels a posted payment (a bounced cheque, an entry made twice);
    the documents it settled are open again."""
    reason = (reason or '').strip()
    if not reason:
        raise FinanceError("Say why it's cancelled, e.g. the cheque bounced.")
    payment = _locked(payment, Status.POSTED)
    payment.status = Status.CANCELLED
    payment.cancel_reason = reason[:300]
    payment.cancelled_at = timezone.now()
    payment.save()
    return payment


def unallocated(payment):
    return payment.amount - (payment.allocations.aggregate(total=Sum('amount'))['total'] or ZERO)


def advances(company, direction, party=None):
    """Money received or paid but not set against any document yet, per party."""
    payments = Payment.objects.filter(company=company, direction=direction, status=Status.POSTED)
    if party is not None:
        payments = payments.filter(party=party)
    out = {}
    for p in payments.annotate(allocated=Sum('allocations__amount')):
        left = p.amount - (p.allocated or ZERO)
        if left > 0:
            out[p.party_id] = out.get(p.party_id, ZERO) + left
    return out
