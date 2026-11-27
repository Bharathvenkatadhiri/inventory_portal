"""Credit and debit notes. Callers (adjustments.api) check the plain
permission; this checks the original document, quantities, values, dates
and stock, and raises NoteError with a message to show.

Limits, per line of the original invoice or bill, counting every note that
isn't cancelled (drafts too, so two drafts can't both use the same room):
- returned quantity <= quantity billed - returned already;
- credited value <= value billed + debited - credited already.
A sales credit note must be dated by 30 November after the end of the
financial year of the invoice (CGST Act s.34(2)).
"""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from inventory import batches
from inventory import services as inventory
from inventory.models import StockEntry, Warehouse
from purchase.models import PurchaseBill, PurchaseBillLine
from purchase.services import _decimal, next_number
from sales.models import SalesInvoice, SalesInvoiceLine

from .models import CreditDebitNote, CreditDebitNoteLine

Note, Side, Kind, Reason, Status = CreditDebitNote, CreditDebitNote.Side, CreditDebitNote.Kind, CreditDebitNote.Reason, CreditDebitNote.Status
CENT, RUPEE, ZERO = Decimal('0.01'), Decimal('1'), Decimal('0')
CREDIT_REASONS = [Reason.GOODS_RETURNED, Reason.DISCOUNT, Reason.RATE_CHANGE, Reason.SHORT_SUPPLY, Reason.OTHER]
DEBIT_REASONS = [Reason.RATE_CHANGE, Reason.ADDITIONAL_CHARGES, Reason.OTHER]


class NoteError(Exception):
    """A change that isn't allowed; the message says why."""


def _money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def credit_note_deadline(invoice_date):
    """30 November after the end of the invoice's financial year (April-March)."""
    fy_end_year = invoice_date.year + 1 if invoice_date.month >= 4 else invoice_date.year
    return date(fy_end_year, 11, 30)


# --- The original document ---------------------------------------------------------------

def _document(company, side, document_id, lock=False):
    model = SalesInvoice if side == Side.SALES else PurchaseBill
    docs = model.objects.filter(company=company, pk=document_id)
    if lock:
        docs = docs.select_for_update()
    doc = docs.first()
    if doc is None:
        raise NoteError("Choose one of your posted invoices." if side == Side.SALES else "Choose one of your posted bills.")
    if doc.status != 'posted':
        raise NoteError("Notes can only be raised against a posted document.")
    return doc


def _doc_lines(side, doc):
    if side == Side.SALES:
        return {l.pk: l for l in doc.lines.select_related('item')}
    return {l.pk: l for l in doc.lines.select_related('item', 'order_line')}


def _line_field(side):
    return 'sales_invoice_line' if side == Side.SALES else 'purchase_bill_line'


def used(side, doc, exclude=None):
    """{original line id: {'returned': qty, 'credited': value, 'debited': value}}
    over notes against `doc` that aren't cancelled."""
    field = _line_field(side)
    lines = CreditDebitNoteLine.objects.filter(**{f'{field}__in': list(_doc_lines(side, doc))}).exclude(note__status=Status.CANCELLED)
    if exclude is not None and exclude.pk:
        lines = lines.exclude(note=exclude)
    out = {}
    for row in lines.values(f'{field}_id', 'note__kind', 'note__reason').annotate(qty=Sum('quantity'), value=Sum('taxable_value')):
        entry = out.setdefault(row[f'{field}_id'], {'returned': ZERO, 'credited': ZERO, 'debited': ZERO})
        if row['note__kind'] == Kind.DEBIT:
            entry['debited'] += row['value']
        else:
            entry['credited'] += row['value']
            if row['note__reason'] == Reason.GOODS_RETURNED:
                entry['returned'] += row['qty']
    return out


def room(side, doc, exclude=None):
    """What each original line still allows: [{line, returnable, creditable}]."""
    taken = used(side, doc, exclude)
    out = []
    for line in _doc_lines(side, doc).values():
        t = taken.get(line.pk, {'returned': ZERO, 'credited': ZERO, 'debited': ZERO})
        out.append({'line': line, 'returnable': line.quantity - t['returned'],
                    'creditable': line.taxable_value + t['debited'] - t['credited']})
    return out


def adjustments_for(side, document_ids):
    """{document id: (debit notes total, credit notes total)} over posted notes."""
    field = 'sales_invoice_id' if side == Side.SALES else 'purchase_bill_id'
    rows = (Note.objects.filter(side=side, status=Status.POSTED, **{f'{field}__in': list(document_ids)})
            .values(field, 'kind').annotate(total=Sum('total')))
    out = {}
    for row in rows:
        debit, credit = out.get(row[field], (ZERO, ZERO))
        if row['kind'] == Kind.DEBIT:
            debit += row['total']
        else:
            credit += row['total']
        out[row[field]] = (debit, credit)
    return out


def has_live_notes(document):
    return document.adjustment_notes.exclude(status=Status.CANCELLED).exists()


# --- Saving ------------------------------------------------------------------------------

def _totals(note, lines):
    taxable = tax = ZERO
    for l in lines:
        l.tax_amount = _money(l.taxable_value * l.gst_rate / 100)
        taxable += l.taxable_value
        tax += l.tax_amount
    note.taxable_value = taxable
    if note.is_interstate:
        note.igst, note.cgst, note.sgst = tax, ZERO, ZERO
    else:
        note.cgst = _money(tax / 2)
        note.sgst, note.igst = tax - note.cgst, ZERO
    gross = taxable + tax
    note.total = gross.quantize(RUPEE, rounding=ROUND_HALF_UP)
    note.round_off = note.total - gross


@transaction.atomic
def save_note(membership, note, data):
    """Creates a draft note, or updates one that's still a draft."""
    company = membership.company
    if note.pk:
        note = Note.objects.select_for_update().get(pk=note.pk)
        if note.status != Status.DRAFT:
            raise NoteError("Only a draft note can be changed.")
        side, kind = note.side, note.kind
        doc = _document(company, side, note.document.pk, lock=True)
    else:
        side, kind = data.get('side'), data.get('kind')
        if side not in Side.values or kind not in Kind.values:
            raise NoteError("Say whether it's a credit or debit note, and which side.")
        doc = _document(company, side, data.get('document_id'), lock=True)

    reason = data.get('reason')
    if reason not in (CREDIT_REASONS if kind == Kind.CREDIT else DEBIT_REASONS):
        raise NoteError("Choose a reason that fits this note.")
    note_date = data.get('note_date') or timezone.localdate()
    if note_date > timezone.localdate():
        raise NoteError("The note's date can't be in the future.")
    if note_date < doc.invoice_date:
        raise NoteError("A note can't be dated before the document it adjusts.")
    if side == Side.SALES and kind == Kind.CREDIT and note_date > credit_note_deadline(doc.invoice_date):
        raise NoteError(f"A credit note for an invoice dated {doc.invoice_date:%d %b %Y} had to be issued by "
                        f"{credit_note_deadline(doc.invoice_date):%d %b %Y} (CGST Act s.34). Settle it some other way.")
    party_number = (data.get('party_note_number') or '').strip().upper()[:30]
    if side == Side.PURCHASE and not party_number:
        raise NoteError("Enter the supplier's credit or debit note number.")
    if side == Side.PURCHASE and Note.objects.filter(company=company, side=side, party=doc.supplier, party_note_number__iexact=party_number) \
            .exclude(status=Status.CANCELLED).exclude(pk=note.pk).exists():
        raise NoteError(f"Note {party_number} from this supplier is already recorded.")

    is_return = reason == Reason.GOODS_RETURNED
    warehouse = None
    if is_return:
        # A purchase return may go back from the rejected-goods location; a sales return comes into a store.
        kinds = [Warehouse.Kind.STORE] + ([Warehouse.Kind.REJECTED] if side == Side.PURCHASE else [])
        warehouse = Warehouse.objects.filter(company=company, pk=data.get('warehouse_id'), is_active=True, kind__in=kinds).first()
        if warehouse is None:
            raise NoteError("Choose the warehouse the goods come back into." if side == Side.SALES
                            else "Choose the warehouse the goods go back from.")

    limits = {r['line'].pk: r for r in room(side, doc, exclude=note)}
    lines = []
    for row in data.get('lines') or []:
        limit = limits.get(row.get('line_id'))
        if limit is None:
            raise NoteError("A line isn't on the original document.")
        original = limit['line']
        quantity = _decimal(row.get('quantity') or 0, 'the quantity')
        value = _decimal(row.get('taxable_value') or 0, 'the amount')
        if quantity < 0 or value < 0:
            raise NoteError(f"{original.item.name}: quantities and amounts can't be negative.")
        if is_return:
            if quantity == 0:
                continue
            if quantity > limit['returnable']:
                raise NoteError(f"{original.item.name}: only {limit['returnable'].normalize():f} {original_unit(original)} can still be returned.")
            value = _money(quantity * original.rate)
        else:
            quantity = ZERO
            if value == 0:
                continue
            value = _money(value)
        if kind == Kind.CREDIT and value > limit['creditable']:
            raise NoteError(f"{original.item.name}: at most ₹{limit['creditable']:,.2f} can still be credited.")
        lines.append(CreditDebitNoteLine(**{_line_field(side): original}, item=original.item,
                                         hsn_code=getattr(original, 'hsn_code', '') or original.item.hsn_code,
                                         quantity=quantity, unit=original_unit(original),
                                         rate=original.rate if is_return else ZERO, gst_rate=original.gst_rate, taxable_value=value))
    if not lines:
        raise NoteError("Enter a quantity or an amount for at least one line.")

    note.company, note.side, note.kind, note.reason = company, side, kind, reason
    note.party = doc.customer if side == Side.SALES else doc.supplier
    note.sales_invoice = doc if side == Side.SALES else None
    note.purchase_bill = doc if side == Side.PURCHASE else None
    note.note_date, note.warehouse, note.party_note_number = note_date, warehouse, party_number
    note.remarks = (data.get('remarks') or '').strip()
    note.place_of_supply, note.is_interstate = doc.place_of_supply, doc.is_interstate
    if note.pk is None:
        note.created_by = membership.user
    _totals(note, lines)
    note.save()
    note.lines.all().delete()
    for line in lines:
        line.note = note
    CreditDebitNoteLine.objects.bulk_create(lines)
    return note


def original_unit(line):
    return line.unit if hasattr(line, 'unit') else line.order_line.unit


def _locked(note, *statuses):
    note = Note.objects.select_for_update().get(pk=note.pk)
    if statuses and note.status not in statuses:
        raise NoteError(f"This note is {note.get_status_display().lower()}.")
    return note


def _return_batches(note, doc, line, warehouse):
    """[(batch, quantity)] for returned goods of a tracked item: back into
    the batches sent against the invoice's order (latest first), or out of
    the batches received against the bill's order when they're still in
    the warehouse."""
    item, quantity = line.item, line.quantity
    if not item.tracked:
        return [(None, quantity)]
    if note.side == Side.SALES:
        references = list(doc.order.dispatches.order_by('-dispatch_date', '-id').values_list('number', flat=True))
        sent = batches.by_document(note.company, references, -1)
        rows, left = [], quantity
        for reference in references:
            for batch, sent_quantity in sent.get((reference, item.pk), []):
                if left == 0:
                    break
                if item.by_serial and batches.batch_stock(batch) > 0:
                    continue  # this unit is already back
                take = min(sent_quantity, left)
                rows.append((batch, take))
                left -= take
        if left > 0:
            raise NoteError(f"{item.name}: the batches sent against this order don't cover {quantity.normalize():f} {line.unit}.")
        return rows
    references = list(doc.order.receipts.values_list('number', flat=True))
    received = [b.pk for (_ref, item_id), rows in batches.by_document(note.company, references, 1).items()
                if item_id == item.pk for b, _q in rows]
    try:
        return batches.outgoing(note.company, item, warehouse, quantity, within=received, allow_expired=True)
    except batches.BatchError:
        return batches.outgoing(note.company, item, warehouse, quantity, allow_expired=True)


@transaction.atomic
def post_note(membership, note):
    """Posts a draft: numbers it and, for a return, moves the goods."""
    note = _locked(note, Status.DRAFT)
    doc = _document(note.company, note.side, note.document.pk, lock=True)
    limits = {r['line'].pk: r for r in room(note.side, doc, exclude=note)}
    for line in note.lines.select_related('item'):
        limit = limits[getattr(line, f'{_line_field(note.side)}_id')]
        if note.is_return and line.quantity > limit['returnable']:
            raise NoteError(f"{line.item.name}: more than can still be returned. Edit the draft.")
        if note.kind == Kind.CREDIT and line.taxable_value > limit['creditable']:
            raise NoteError(f"{line.item.name}: more than can still be credited. Edit the draft.")

    # Numbered first so the stock entries can carry it; if anything below
    # fails, the whole transaction (the number too) rolls back, leaving no gap.
    prefix = {(Side.SALES, Kind.CREDIT): 'CN', (Side.SALES, Kind.DEBIT): 'DN'}.get((note.side, note.kind), 'SN')
    note.number = next_number(note.company, f"erp_{prefix.lower()}", prefix, note.note_date)
    if note.is_return:
        warehouse = Warehouse.objects.select_for_update().get(pk=note.warehouse_id)
        for line in note.lines.select_related('item'):
            if not line.item.tracks_stock:
                continue
            if note.side == Side.PURCHASE:
                on_hand, _value = inventory.on_hand(note.company, line.item, warehouse)
                if line.quantity > on_hand:
                    raise NoteError(f"{line.item.name}: only {on_hand.normalize():f} {line.unit} in {warehouse.name} to send back.")
            sign = 1 if note.side == Side.SALES else -1
            batches.write(note.company, line.item, warehouse,
                          StockEntry.Kind.SALES_RETURN if note.side == Side.SALES else StockEntry.Kind.PURCHASE_RETURN,
                          _return_batches(note, doc, line, warehouse), sign=sign,
                          rate=inventory.average_cost(note.company, line.item, warehouse), entry_date=note.note_date,
                          reference=note.number, note=f"{note.get_kind_display()} against {doc.number}", user=membership.user)
    note.status = Status.POSTED
    note.posted_by, note.posted_at = membership.user, timezone.now()
    note.save()
    return note


@transaction.atomic
def cancel_note(membership, note, reason):
    """Cancels a posted note. A return's stock movement is reversed; the
    number stays used."""
    reason = (reason or '').strip()
    if not reason:
        raise NoteError("Say why the note is being cancelled.")
    note = _locked(note, Status.POSTED)
    if note.is_return and note.warehouse_id:
        warehouse = Warehouse.objects.select_for_update().get(pk=note.warehouse_id)
        moved = list(StockEntry.objects.filter(company=note.company, reference=note.number,
                                               kind__in=[StockEntry.Kind.SALES_RETURN, StockEntry.Kind.PURCHASE_RETURN])
                     .select_related('item', 'warehouse', 'batch'))
        for entry in moved:
            if entry.quantity > 0:  # goods that came back in must still be there to take out again
                on_hand, _value = inventory.on_hand(note.company, entry.item, warehouse)
                if entry.quantity > on_hand:
                    raise NoteError(f"{entry.item.name}: the returned goods have already been used, so this can't be undone.")
        try:
            batches.still_on_hand(moved)
        except batches.BatchError as exc:
            raise NoteError(f"{exc} This can't be undone.")
        batches.reverse(moved, entry_date=timezone.localdate(), note=f"Cancelled: {reason}", user=membership.user)
    note.status = Status.CANCELLED
    note.cancelled_at, note.cancel_reason = timezone.now(), reason[:300]
    note.save()
    return note


@transaction.atomic
def delete_note(note):
    note = _locked(note, Status.DRAFT)
    note.delete()
