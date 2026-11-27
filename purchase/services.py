"""Every change to purchase orders, goods receipts and bills goes through
here. Callers (purchase.api) check the plain permission; this checks the
rest (the supplier relationship, document status, quantities) and raises
PurchaseError with a message to show.

The flow:
  order: draft -> (submit) issued, or waiting for approval -> issued
         issued -> partly received -> received; partly received -> closed;
         issued (nothing received or billed) -> cancelled
  receipt: posted at once; adds the goods to stock at the order's rate
  bill: draft -> (submit) posted, or waiting for approval -> posted -> cancelled

Approval steps apply when the plan includes approvals (Growth and Pro) and
the person submitting can't approve themselves.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import Company
from inventory import batches
from inventory.models import GST_RATE_VALUES, Item, StockEntry, Warehouse
from marketplace.documents import financial_year
from marketplace.models import DocumentSequence
from organizations import access
from organizations.models import BusinessRelationship
from plans import erp

from .models import GoodsReceipt, GoodsReceiptLine, PurchaseBill, PurchaseBillLine, PurchaseOrder, PurchaseOrderLine

Order, Bill = PurchaseOrder, PurchaseBill
CENT, RUPEE = Decimal('0.01'), Decimal('1')
MAX_LINES = 200


class PurchaseError(Exception):
    """A change that isn't allowed; the message says why."""


# --- Helpers ----------------------------------------------------------------------

def _decimal(value, what):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise PurchaseError(f"Enter a number for {what}.")


def _money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def next_number(company, kind, prefix, when):
    """PO-2627-0001: per organization, per document kind, per financial
    year, without gaps. Call inside the transaction that saves the document."""
    fy = financial_year(when)
    key = f"org:{company.pk}"
    DocumentSequence.objects.get_or_create(issuer_key=key, kind=kind, financial_year=fy)
    sequence = DocumentSequence.objects.select_for_update().get(issuer_key=key, kind=kind, financial_year=fy)
    sequence.last_number += 1
    sequence.save(update_fields=['last_number'])
    return f"{prefix}-{fy}-{sequence.last_number:04d}"


def _state_of(company):
    return ((company.state_code or '') or (company.gstin or '')[:2])[:2]


def place_of_supply(company, warehouse):
    """Where the goods are delivered: the ship-to warehouse's state, else ours."""
    address = warehouse.address if warehouse and warehouse.address_id else None
    return ((address.state_code if address else '') or _state_of(company))[:2]


def is_interstate(supplier, place):
    supplier_state = _state_of(supplier)
    return bool(supplier_state and place and supplier_state != place)


def apply_totals(document, lines):
    """Works out each line's taxable value and GST, and the document's
    totals: CGST + SGST within a state, IGST across states, rounded to the
    rupee with the difference shown as round-off."""
    taxable = tax = Decimal('0')
    for line in lines:
        line.taxable_value = _money(line.quantity * line.rate)
        line.tax_amount = _money(line.taxable_value * line.gst_rate / 100)
        taxable += line.taxable_value
        tax += line.tax_amount
    document.taxable_value = taxable
    if document.is_interstate:
        document.igst, document.cgst, document.sgst = tax, Decimal('0'), Decimal('0')
    else:
        document.cgst = _money(tax / 2)
        document.sgst = tax - document.cgst
        document.igst = Decimal('0')
    gross = taxable + tax
    document.total = gross.quantize(RUPEE, rounding=ROUND_HALF_UP)
    document.round_off = document.total - gross


def approvals_on(company):
    return erp.includes(company.erp_plan, erp.APPROVALS)


def _can(membership, code, counterparty=None):
    return bool(access.check(membership, code, counterparty))


def _not_future(day, what):
    if day > timezone.localdate():
        raise PurchaseError(f"The {what} can't be in the future.")


# --- Purchase orders ---------------------------------------------------------------

def suppliers(company):
    """Organizations we can order from: active relationships where we buy."""
    return (BusinessRelationship.objects.filter(buyer=company, status=BusinessRelationship.Status.ACTIVE)
            .select_related('supplier').order_by('supplier__name'))


def _order_lines(company, rows):
    if not rows:
        raise PurchaseError("Add at least one item.")
    if len(rows) > MAX_LINES:
        raise PurchaseError(f"An order can have up to {MAX_LINES} lines.")
    items = {i.pk: i for i in Item.objects.filter(company=company, pk__in=[r.get('item_id') for r in rows])}
    lines = []
    for position, row in enumerate(rows, start=1):
        item = items.get(row.get('item_id'))
        if item is None:
            raise PurchaseError(f"Line {position}: choose one of your items.")
        if not item.is_active:
            raise PurchaseError(f"Line {position}: {item.name} is inactive.")
        quantity = _decimal(row.get('quantity'), 'the quantity')
        rate = _decimal(row.get('rate') if row.get('rate') not in (None, '') else (item.purchase_price or 0), 'the rate')
        gst_rate = _decimal(row.get('gst_rate') if row.get('gst_rate') not in (None, '') else item.gst_rate, 'the GST rate')
        if quantity <= 0:
            raise PurchaseError(f"Line {position}: the quantity must be more than 0.")
        if rate < 0:
            raise PurchaseError(f"Line {position}: the rate can't be negative.")
        if gst_rate not in GST_RATE_VALUES:
            raise PurchaseError(f"Line {position}: that isn't a GST rate.")
        lines.append(PurchaseOrderLine(
            position=position, item=item, description=(row.get('description') or '').strip()[:200],
            quantity=quantity, unit=item.unit, rate=rate, gst_rate=gst_rate,
        ))
    return lines


@transaction.atomic
def save_order(membership, order, data):
    """Creates a draft order, or updates one that's still a draft."""
    company = membership.company
    if order.pk:
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status != Order.Status.DRAFT:
            raise PurchaseError("Only a draft order can be changed.")
    supplier = Company.objects.filter(pk=data.get('supplier_id')).first()
    if supplier is None or supplier.pk == company.pk:
        raise PurchaseError("Choose a supplier.")
    decision = access.check(membership, 'po.create', counterparty=supplier)
    if not decision:
        raise PurchaseError(decision.reason)
    relationship = BusinessRelationship.objects.get(buyer=company, supplier=supplier)
    warehouse = Warehouse.objects.filter(company=company, pk=data.get('ship_to_id'), is_active=True,
                                         kind=Warehouse.Kind.STORE).select_related('address').first()
    if warehouse is None:
        raise PurchaseError("Choose the warehouse the goods should go to.")
    order_date = data.get('order_date') or timezone.localdate()
    expected = data.get('expected_date')
    if expected and expected < order_date:
        raise PurchaseError("The expected date can't be before the order date.")
    lines = _order_lines(company, data.get('lines') or [])

    order.company, order.supplier, order.ship_to = company, supplier, warehouse
    order.order_date, order.expected_date = order_date, expected
    terms = data.get('payment_terms_days')
    order.payment_terms_days = terms if terms is not None else relationship.payment_terms_days
    order.supplier_reference = (data.get('supplier_reference') or '').strip()[:100]
    order.notes = (data.get('notes') or '').strip()
    order.place_of_supply = place_of_supply(company, warehouse)
    order.is_interstate = is_interstate(supplier, order.place_of_supply)
    if order.pk is None:
        order.number = next_number(company, 'erp_po', 'PO', order_date)
        order.created_by = membership.user
    apply_totals(order, lines)
    order.save()
    order.lines.all().delete()
    for line in lines:
        line.order = order
    PurchaseOrderLine.objects.bulk_create(lines)
    return order


def _locked_order(order, *statuses):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if statuses and order.status not in statuses:
        raise PurchaseError(f"This order is {order.get_status_display().lower()}.")
    return order


def _issue(order, user):
    now = timezone.now()
    order.status = Order.Status.ISSUED
    order.approved_by, order.approved_at, order.issued_at = user, now, now
    order.rejection_reason = ''


@transaction.atomic
def submit_order(membership, order):
    """Issues a draft, or sends it for approval when the plan has approvals
    and the person can't approve purchase orders."""
    order = _locked_order(order, Order.Status.DRAFT)
    decision = access.check(membership, 'po.create', counterparty=order.supplier)
    if not decision:
        raise PurchaseError(decision.reason)
    order.submitted_by = membership.user
    if approvals_on(order.company) and not _can(membership, 'po.approve'):
        order.status = Order.Status.PENDING_APPROVAL
        order.rejection_reason = ''
    else:
        _issue(order, membership.user)
    order.save()
    return order


@transaction.atomic
def approve_order(membership, order):
    order = _locked_order(order, Order.Status.PENDING_APPROVAL)
    _issue(order, membership.user)
    order.save()
    return order


@transaction.atomic
def reject_order(membership, order, reason):
    reason = (reason or '').strip()
    if not reason:
        raise PurchaseError("Say why, so the person who raised it can fix it.")
    order = _locked_order(order, Order.Status.PENDING_APPROVAL)
    order.status = Order.Status.DRAFT
    order.rejection_reason = reason[:300]
    order.save()
    return order


@transaction.atomic
def cancel_order(membership, order):
    order = _locked_order(order)
    if order.receipts.exists() or order.bills.exclude(status=Bill.Status.CANCELLED).exists():
        raise PurchaseError("Goods have been received or billed against this order. Close it instead.")
    if order.status not in (Order.Status.PENDING_APPROVAL, Order.Status.ISSUED):
        raise PurchaseError(f"This order is {order.get_status_display().lower()}.")
    if order.supplier_response == Order.SupplierResponse.ACCEPTED:
        raise PurchaseError(f"{order.supplier.name or order.supplier.legal_name} has accepted this order and may be making "
                            "the goods. Ask them to cancel their sales order; this order then shows as declined and can be cancelled.")
    order.status = Order.Status.CANCELLED
    order.closed_at = timezone.now()
    order.save()
    return order


@transaction.atomic
def close_order(membership, order):
    """Stops a partly received order: the rest won't come."""
    order = _locked_order(order, Order.Status.PARTIALLY_RECEIVED)
    order.status = Order.Status.CLOSED
    order.closed_at = timezone.now()
    order.save()
    return order


@transaction.atomic
def delete_order(order):
    order = _locked_order(order, Order.Status.DRAFT)
    order.delete()


# --- Goods receipts ------------------------------------------------------------------

@transaction.atomic
def post_receipt(membership, order, data):
    """Records goods received against an issued order and adds them to
    stock. Each line can be at most what's still open on the order."""
    order = _locked_order(order, Order.Status.ISSUED, Order.Status.PARTIALLY_RECEIVED)
    if order.supplier_response == Order.SupplierResponse.DECLINED:
        raise PurchaseError("The supplier declined this order. Cancel it, or raise a new one.")
    company = membership.company
    warehouse_id = data.get('warehouse_id') or order.ship_to_id
    warehouse = Warehouse.objects.filter(company=company, pk=warehouse_id, is_active=True, kind=Warehouse.Kind.STORE).first()
    if warehouse is None:
        raise PurchaseError("Choose an active warehouse to receive into.")
    received_date = data.get('received_date') or timezone.localdate()
    _not_future(received_date, 'date received')
    if received_date < order.order_date:
        raise PurchaseError("Goods can't be received before the order date.")

    order_lines = {line.pk: line for line in order.lines.select_for_update().select_related('item')}
    wanted = []
    for row in data.get('lines') or []:
        line = order_lines.get(row.get('order_line_id'))
        if line is None:
            raise PurchaseError("A line isn't on this order.")
        quantity = _decimal(row.get('quantity'), 'the quantity received')
        if quantity == 0:
            continue
        if quantity < 0:
            raise PurchaseError(f"{line.item.name}: the quantity can't be negative.")
        open_quantity = line.quantity - line.received_quantity
        if quantity > open_quantity:
            raise PurchaseError(f"{line.item.name}: only {open_quantity.normalize():f} {line.unit} is still to come on this order.")
        wanted.append((line, quantity, row.get('batches') or []))
    if not wanted:
        raise PurchaseError("Enter the quantity received for at least one line.")

    receipt = GoodsReceipt.objects.create(
        company=company, order=order, number=next_number(company, 'erp_grn', 'GRN', received_date),
        warehouse=warehouse, received_date=received_date,
        supplier_challan=(data.get('supplier_challan') or '').strip()[:60],
        vehicle_number=(data.get('vehicle_number') or '').strip().upper()[:20],
        notes=(data.get('notes') or '').strip()[:300], created_by=membership.user,
    )
    from quality import services as quality
    for line, quantity, given in wanted:
        receipt_line = GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, quantity=quantity)
        line.received_quantity += quantity
        line.save(update_fields=['received_quantity'])
        if line.item.tracks_stock:
            # Items that need QC wait under inspection; the lot remembers the store they're for.
            inspect = quality.needs_inspection(company, line.item)
            rows = batches.incoming(company, line.item, quantity, given, origin='receipt', reference=receipt.number,
                                    day=received_date, supplier=order.supplier)
            batches.write(company, line.item, quality.hold(company) if inspect else warehouse, StockEntry.Kind.RECEIPT, rows,
                          sign=1, rate=line.rate, entry_date=received_date, reference=receipt.number,
                          note=f"From {order.supplier.name or order.supplier.legal_name} against {order.number}",
                          user=membership.user)
            if inspect:
                quality.open_lots(company, item=line.item, rows=rows, rate=line.rate, destination=warehouse,
                                  source='receipt', reference=receipt.number, received_on=received_date,
                                  receipt_line=receipt_line, supplier=order.supplier)
    complete = all(l.received_quantity >= l.quantity for l in order_lines.values())
    order.status = Order.Status.RECEIVED if complete else Order.Status.PARTIALLY_RECEIVED
    order.save(update_fields=['status', 'updated_at'])
    return receipt


# --- Bills --------------------------------------------------------------------------

def billed_quantities(order, exclude_bill=None):
    """{order line id: quantity on bills that aren't cancelled}."""
    lines = PurchaseBillLine.objects.filter(bill__order=order).exclude(bill__status=Bill.Status.CANCELLED)
    if exclude_bill is not None and exclude_bill.pk:
        lines = lines.exclude(bill=exclude_bill)
    return {r['order_line_id']: r['quantity'] for r in lines.values('order_line_id').annotate(quantity=Sum('quantity'))}


def billable(order, exclude_bill=None):
    """{order line id: received but not yet billed}."""
    billed = billed_quantities(order, exclude_bill)
    return {line.pk: line.received_quantity - billed.get(line.pk, Decimal('0')) for line in order.lines.all()}


@transaction.atomic
def save_bill(membership, bill, order, data):
    """Creates a draft bill against `order`, or updates a draft. Quantities
    can't exceed what was received and not yet billed."""
    company = membership.company
    if bill.pk:
        bill = Bill.objects.select_for_update().get(pk=bill.pk)
        if bill.status != Bill.Status.DRAFT:
            raise PurchaseError("Only a draft bill can be changed.")
        order = bill.order
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status in (Order.Status.DRAFT, Order.Status.PENDING_APPROVAL, Order.Status.CANCELLED):
        raise PurchaseError("Bills can only be entered against an issued order.")
    invoice_number = (data.get('supplier_invoice_number') or '').strip().upper()
    if not invoice_number:
        raise PurchaseError("Enter the supplier's invoice number.")
    taken = Bill.objects.filter(company=company, supplier=order.supplier, supplier_invoice_number__iexact=invoice_number) \
        .exclude(status=Bill.Status.CANCELLED).exclude(pk=bill.pk).first()
    if taken:
        raise PurchaseError(f"Invoice {invoice_number} from this supplier is already entered ({taken.number}).")
    invoice_date = data.get('invoice_date')
    if not invoice_date:
        raise PurchaseError("Enter the invoice date.")
    _not_future(invoice_date, 'invoice date')

    open_quantities = billable(order, exclude_bill=bill)
    order_lines = {line.pk: line for line in order.lines.select_related('item')}
    lines = []
    for row in data.get('lines') or []:
        line = order_lines.get(row.get('order_line_id'))
        if line is None:
            raise PurchaseError("A line isn't on this order.")
        quantity = _decimal(row.get('quantity'), 'the quantity')
        if quantity == 0:
            continue
        if quantity < 0:
            raise PurchaseError(f"{line.item.name}: the quantity can't be negative.")
        if quantity > open_quantities[line.pk]:
            raise PurchaseError(f"{line.item.name}: only {open_quantities[line.pk].normalize():f} {line.unit} has been "
                                "received and not billed yet.")
        rate = _decimal(row.get('rate') if row.get('rate') not in (None, '') else line.rate, 'the rate')
        gst_rate = _decimal(row.get('gst_rate') if row.get('gst_rate') not in (None, '') else line.gst_rate, 'the GST rate')
        if rate < 0:
            raise PurchaseError(f"{line.item.name}: the rate can't be negative.")
        if gst_rate not in GST_RATE_VALUES:
            raise PurchaseError(f"{line.item.name}: that isn't a GST rate.")
        lines.append(PurchaseBillLine(order_line=line, item=line.item, quantity=quantity, rate=rate, gst_rate=gst_rate))
    if not lines:
        raise PurchaseError("Enter the quantity billed for at least one line.")

    bill.company, bill.supplier, bill.order = company, order.supplier, order
    bill.supplier_invoice_number, bill.invoice_date = invoice_number, invoice_date
    bill.due_date = invoice_date + timedelta(days=order.payment_terms_days) if order.payment_terms_days is not None else None
    bill.notes = (data.get('notes') or '').strip()
    bill.place_of_supply, bill.is_interstate = order.place_of_supply, order.is_interstate
    if bill.pk is None:
        bill.number = next_number(company, 'erp_pb', 'PB', timezone.localdate())
        bill.created_by = membership.user
    apply_totals(bill, lines)
    try:
        with transaction.atomic():
            bill.save()
    except IntegrityError:
        raise PurchaseError(f"Invoice {invoice_number} from this supplier is already entered.")
    bill.lines.all().delete()
    for line in lines:
        line.bill = bill
    PurchaseBillLine.objects.bulk_create(lines)
    return bill


def _locked_bill(bill, *statuses):
    bill = Bill.objects.select_for_update().get(pk=bill.pk)
    if statuses and bill.status not in statuses:
        raise PurchaseError(f"This bill is {bill.get_status_display().lower()}.")
    return bill


def _post(bill, user):
    from tds.services import apply_to_bill
    now = timezone.now()
    bill.status = Bill.Status.POSTED
    bill.approved_by, bill.approved_at, bill.posted_at = user, now, now
    bill.rejection_reason = ''
    apply_to_bill(bill)  # TDS on this bill, now that it counts
    from finance import msme
    msme.apply_to_bill(bill)  # the 45-day due date for micro and small suppliers


@transaction.atomic
def submit_bill(membership, bill):
    bill = _locked_bill(bill, Bill.Status.DRAFT)
    if approvals_on(bill.company) and not _can(membership, 'purchase_bill.approve'):
        bill.status = Bill.Status.PENDING_APPROVAL
        bill.rejection_reason = ''
    else:
        _post(bill, membership.user)
    bill.save()
    return bill


@transaction.atomic
def approve_bill(membership, bill):
    bill = _locked_bill(bill, Bill.Status.PENDING_APPROVAL)
    _post(bill, membership.user)
    bill.save()
    return bill


@transaction.atomic
def reject_bill(membership, bill, reason):
    reason = (reason or '').strip()
    if not reason:
        raise PurchaseError("Say why, so the person who entered it can fix it.")
    bill = _locked_bill(bill, Bill.Status.PENDING_APPROVAL)
    bill.status = Bill.Status.DRAFT
    bill.rejection_reason = reason[:300]
    bill.save()
    return bill


@transaction.atomic
def cancel_bill(membership, bill):
    """Cancels a posted bill or one waiting for approval; its quantities
    can be billed again. (Once payments exist, a paid bill won't cancel.)"""
    bill = _locked_bill(bill, Bill.Status.PENDING_APPROVAL, Bill.Status.POSTED)
    from finance.services import has_live_payments
    if has_live_payments(bill):
        raise PurchaseError("A payment has been made against this bill. Cancel that payment first.")
    from adjustments.services import has_live_notes
    if has_live_notes(bill):
        raise PurchaseError("The supplier's credit or debit notes are recorded against this bill. Cancel them first.")
    bill.status = Bill.Status.CANCELLED
    bill.cancelled_at = timezone.now()
    bill.save()
    return bill


@transaction.atomic
def delete_bill(bill):
    bill = _locked_bill(bill, Bill.Status.DRAFT)
    bill.delete()


# --- The supplier's side -------------------------------------------------------------

def orders_received(company):
    """Purchase orders other organizations have issued to `company`."""
    return (Order.objects.filter(supplier=company)
            .exclude(status__in=[Order.Status.DRAFT, Order.Status.PENDING_APPROVAL])
            .select_related('company'))
