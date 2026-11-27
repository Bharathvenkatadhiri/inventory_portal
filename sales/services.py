"""Every change to sales documents goes through here. Callers (sales.api)
check the plain permission; this checks the customer relationship, the
document's status, quantities and stock, and raises SalesError with a
message to show.

  customer's PO (on MakeSetu) -> accept: a confirmed sales order at the
      PO's quantities and rates; or decline, with a reason the buyer sees
  sales order: draft -> (submit) confirmed, or waiting for approval
      -> confirmed -> partly dispatched -> dispatched; partly -> closed;
      confirmed with nothing dispatched or invoiced -> cancelled
  dispatch: posted at once; takes the goods out of stock at average cost
  invoice: draft -> posted (numbered then, so the series has no gaps)
      -> cancelled (the number stays used)
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from inventory import batches
from inventory import services as inventory
from inventory.models import GST_RATE_VALUES, Item, StockEntry, Warehouse
from organizations import access
from organizations.models import BusinessRelationship
from purchase.models import PurchaseOrder
from purchase.services import _decimal, _state_of, apply_totals, approvals_on, is_interstate, next_number

from .models import Dispatch, DispatchLine, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderLine

Order, Invoice, PO = SalesOrder, SalesInvoice, PurchaseOrder
MAX_LINES = 200


class SalesError(Exception):
    """A change that isn't allowed; the message says why."""


def _check(membership, code, counterparty=None):
    decision = access.check(membership, code, counterparty)
    if not decision:
        raise SalesError(decision.reason)


def _not_future(day, what):
    if day > timezone.localdate():
        raise SalesError(f"The {what} can't be in the future.")


def customers(company):
    """Organizations we sell to: active relationships where we're the supplier."""
    return (BusinessRelationship.objects.filter(supplier=company, status=BusinessRelationship.Status.ACTIVE)
            .select_related('buyer').order_by('buyer__name'))


def _relationship(company, customer):
    return BusinessRelationship.objects.filter(buyer=customer, supplier=company).first()


# --- Replying to a customer's purchase order ---------------------------------------------

def reply_permission(company):
    """Accepting or declining commits the company, so on plans with
    approvals it needs someone who can approve sales orders."""
    return 'so.approve' if approvals_on(company) else 'so.create'


def suggested_items(company, purchase_order):
    """{PO line id: our item id} from what we matched the customer's item to last time."""
    suggestions = {}
    for line in purchase_order.lines.all():
        previous = (SalesOrderLine.objects.filter(order__company=company, order__customer=purchase_order.company,
                                                  customer_item_id=line.item_id, item__is_active=True)
                    .order_by('-id').values_list('item_id', flat=True).first())
        if previous:
            suggestions[line.pk] = previous
    return suggestions


def _received_po(membership, purchase_order):
    # of=('self',): lock just the order; the warehouse address join is nullable, which Postgres won't lock.
    po = PO.objects.select_for_update(of=('self',)).select_related('company', 'ship_to__address').get(pk=purchase_order.pk)
    if po.supplier_id != membership.company.pk:
        raise SalesError("This order wasn't sent to you.")
    if po.supplier_response:
        raise SalesError(f"You've already {po.get_supplier_response_display().lower()} this order.")
    if po.status not in (PO.Status.ISSUED, PO.Status.PARTIALLY_RECEIVED):
        raise SalesError(f"The customer's order is {po.get_status_display().lower()}.")
    _check(membership, reply_permission(membership.company), counterparty=po.company)
    return po


@transaction.atomic
def accept(membership, purchase_order, data):
    """Accepts a customer's PO: a confirmed sales order at its quantities
    and rates, each line matched to one of our own items."""
    company = membership.company
    po = _received_po(membership, purchase_order)
    warehouse = inventory.store(company, data.get('dispatch_from_id'))
    if warehouse is None:
        raise SalesError("Choose the warehouse you'll dispatch from.")
    mapping = {row['purchase_order_line_id']: row.get('item_id') for row in data.get('lines') or []}
    items = {i.pk: i for i in Item.objects.filter(company=company, pk__in=[v for v in mapping.values() if v])}

    address = po.ship_to.address
    order = Order(
        company=company, customer=po.company, number=next_number(company, 'erp_so', 'SO', timezone.localdate()),
        status=Order.Status.CONFIRMED, order_date=timezone.localdate(), delivery_date=po.expected_date,
        purchase_order=po, customer_po_number=po.number, dispatch_from=warehouse,
        ship_to=', '.join(p for p in (po.ship_to.name, address.line1 if address else '', address.city if address else '',
                                      address.pincode if address else '') if p),
        payment_terms_days=po.payment_terms_days, notes=po.notes,
        place_of_supply=po.place_of_supply, created_by=membership.user,
        approved_by=membership.user, approved_at=timezone.now(),
    )
    order.is_interstate = is_interstate(company, order.place_of_supply)
    lines = []
    for position, po_line in enumerate(po.lines.select_related('item'), start=1):
        item = items.get(mapping.get(po_line.pk))
        if item is None:
            raise SalesError(f"Choose your own item for {po_line.item.name}.")
        if not item.is_active:
            raise SalesError(f"{item.name} is inactive.")
        if item.unit != po_line.unit:
            raise SalesError(f"{item.name} is kept in {item.unit}, but the customer ordered {po_line.item.name} in {po_line.unit}.")
        lines.append(SalesOrderLine(
            position=position, item=item, customer_item=po_line.item, purchase_order_line=po_line,
            description=po_line.description or po_line.item.name, quantity=po_line.quantity, unit=po_line.unit,
            rate=po_line.rate, gst_rate=item.gst_rate,
        ))
    apply_totals(order, lines)
    order.save()
    for line in lines:
        line.order = order
    SalesOrderLine.objects.bulk_create(lines)

    po.supplier_response = PO.SupplierResponse.ACCEPTED
    po.supplier_responded_at, po.supplier_responded_by = timezone.now(), membership.user
    po.save(update_fields=['supplier_response', 'supplier_responded_at', 'supplier_responded_by', 'updated_at'])
    return order


@transaction.atomic
def decline(membership, purchase_order, reason):
    reason = (reason or '').strip()
    if not reason:
        raise SalesError("Tell the customer why, so they can order elsewhere or change the order.")
    po = _received_po(membership, purchase_order)
    po.supplier_response = PO.SupplierResponse.DECLINED
    po.supplier_responded_at, po.supplier_responded_by = timezone.now(), membership.user
    po.decline_reason = reason[:300]
    po.save(update_fields=['supplier_response', 'supplier_responded_at', 'supplier_responded_by', 'decline_reason', 'updated_at'])
    return po


# --- Sales orders -------------------------------------------------------------------------

def _order_lines(company, rows):
    if not rows:
        raise SalesError("Add at least one item.")
    if len(rows) > MAX_LINES:
        raise SalesError(f"An order can have up to {MAX_LINES} lines.")
    items = {i.pk: i for i in Item.objects.filter(company=company, pk__in=[r.get('item_id') for r in rows])}
    lines = []
    for position, row in enumerate(rows, start=1):
        item = items.get(row.get('item_id'))
        if item is None:
            raise SalesError(f"Line {position}: choose one of your items.")
        if not item.is_active:
            raise SalesError(f"Line {position}: {item.name} is inactive.")
        quantity = _decimal(row.get('quantity'), 'the quantity')
        rate = _decimal(row.get('rate') if row.get('rate') not in (None, '') else (item.sale_price or 0), 'the rate')
        gst_rate = _decimal(row.get('gst_rate') if row.get('gst_rate') not in (None, '') else item.gst_rate, 'the GST rate')
        if quantity <= 0:
            raise SalesError(f"Line {position}: the quantity must be more than 0.")
        if rate < 0:
            raise SalesError(f"Line {position}: the rate can't be negative.")
        if gst_rate not in GST_RATE_VALUES:
            raise SalesError(f"Line {position}: that isn't a GST rate.")
        lines.append(SalesOrderLine(position=position, item=item, description=(row.get('description') or '').strip()[:200],
                                    quantity=quantity, unit=item.unit, rate=rate, gst_rate=gst_rate))
    return lines


@transaction.atomic
def save_order(membership, order, data):
    """Creates or updates a draft sales order entered by hand."""
    from accounts.models import Company

    company = membership.company
    if order.pk:
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status != Order.Status.DRAFT:
            raise SalesError("Only a draft order can be changed.")
    customer = Company.objects.filter(pk=data.get('customer_id')).first()
    if customer is None or customer.pk == company.pk:
        raise SalesError("Choose a customer.")
    _check(membership, 'so.create', counterparty=customer)
    warehouse = inventory.store(company, data.get('dispatch_from_id'))
    if warehouse is None:
        raise SalesError("Choose the warehouse you'll dispatch from.")
    order_date = data.get('order_date') or timezone.localdate()
    if data.get('delivery_date') and data['delivery_date'] < order_date:
        raise SalesError("The delivery date can't be before the order date.")
    lines = _order_lines(company, data.get('lines') or [])

    order.company, order.customer, order.dispatch_from = company, customer, warehouse
    order.order_date, order.delivery_date = order_date, data.get('delivery_date')
    order.customer_po_number = (data.get('customer_po_number') or '').strip()[:40]
    order.ship_to = (data.get('ship_to') or '').strip() or customer.principal_address
    terms = data.get('payment_terms_days')
    relationship = _relationship(company, customer)
    order.payment_terms_days = terms if terms is not None else (relationship.payment_terms_days if relationship else None)
    order.notes = (data.get('notes') or '').strip()
    # Goods go to the customer: place of supply is their state.
    order.place_of_supply = _state_of(customer)
    order.is_interstate = is_interstate(company, order.place_of_supply)
    if order.pk is None:
        order.number = next_number(company, 'erp_so', 'SO', order_date)
        order.created_by = membership.user
    apply_totals(order, lines)
    order.save()
    order.lines.all().delete()
    for line in lines:
        line.order = order
    SalesOrderLine.objects.bulk_create(lines)
    return order


def _locked(order, *statuses):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if statuses and order.status not in statuses:
        raise SalesError(f"This order is {order.get_status_display().lower()}.")
    return order


def _confirm(order, user):
    order.status = Order.Status.CONFIRMED
    order.approved_by, order.approved_at = user, timezone.now()
    order.rejection_reason = ''


@transaction.atomic
def submit_order(membership, order):
    order = _locked(order, Order.Status.DRAFT)
    _check(membership, 'so.create', counterparty=order.customer)
    if approvals_on(order.company) and not access.check(membership, 'so.approve'):
        order.status = Order.Status.PENDING_APPROVAL
        order.rejection_reason = ''
    else:
        _confirm(order, membership.user)
    order.save()
    return order


@transaction.atomic
def approve_order(membership, order):
    order = _locked(order, Order.Status.PENDING_APPROVAL)
    _confirm(order, membership.user)
    order.save()
    return order


@transaction.atomic
def reject_order(membership, order, reason):
    reason = (reason or '').strip()
    if not reason:
        raise SalesError("Say why, so the person who raised it can fix it.")
    order = _locked(order, Order.Status.PENDING_APPROVAL)
    order.status = Order.Status.DRAFT
    order.rejection_reason = reason[:300]
    order.save()
    return order


@transaction.atomic
def cancel_order(membership, order, reason=''):
    """Cancels an order nothing has been dispatched or invoiced against. If
    it came from a customer's PO, the customer sees it as declined."""
    order = _locked(order)
    if order.dispatches.exists() or order.invoices.exclude(status=Invoice.Status.CANCELLED).exists():
        raise SalesError("Goods have been dispatched or invoiced against this order. Close it instead.")
    if order.status not in (Order.Status.PENDING_APPROVAL, Order.Status.CONFIRMED):
        raise SalesError(f"This order is {order.get_status_display().lower()}.")
    reason = (reason or '').strip()
    if order.purchase_order_id and not reason:
        raise SalesError("Tell the customer why you're cancelling their order.")
    order.status = Order.Status.CANCELLED
    order.closed_at = timezone.now()
    order.save()
    if order.purchase_order_id:
        po = PO.objects.select_for_update().get(pk=order.purchase_order_id)
        po.supplier_response = PO.SupplierResponse.DECLINED
        po.supplier_responded_at, po.supplier_responded_by = timezone.now(), membership.user
        po.decline_reason = f"Cancelled after accepting: {reason}"[:300]
        po.save(update_fields=['supplier_response', 'supplier_responded_at', 'supplier_responded_by', 'decline_reason', 'updated_at'])
    return order


@transaction.atomic
def close_order(membership, order):
    order = _locked(order, Order.Status.PARTIALLY_DISPATCHED)
    order.status = Order.Status.CLOSED
    order.closed_at = timezone.now()
    order.save()
    return order


@transaction.atomic
def delete_order(order):
    order = _locked(order, Order.Status.DRAFT)
    order.delete()


# --- Dispatches ----------------------------------------------------------------------------

@transaction.atomic
def post_dispatch(membership, order, data):
    """Sends goods against a confirmed order and takes them out of stock.
    Each line can be at most what's still to send and what's on hand."""
    order = _locked(order, Order.Status.CONFIRMED, Order.Status.PARTIALLY_DISPATCHED)
    company = membership.company
    # Locking the warehouse serializes stock going out of it, so two
    # dispatches can't both take the last of something.
    warehouse = inventory.store(company, data.get('warehouse_id') or order.dispatch_from_id, lock=True)
    if warehouse is None:
        raise SalesError("Choose an active warehouse to dispatch from.")
    dispatch_date = data.get('dispatch_date') or timezone.localdate()
    _not_future(dispatch_date, 'dispatch date')
    if dispatch_date < order.order_date:
        raise SalesError("Goods can't be dispatched before the order date.")

    order_lines = {line.pk: line for line in order.lines.select_for_update().select_related('item')}
    wanted = []
    for row in data.get('lines') or []:
        line = order_lines.get(row.get('order_line_id'))
        if line is None:
            raise SalesError("A line isn't on this order.")
        quantity = _decimal(row.get('quantity'), 'the quantity')
        if quantity == 0:
            continue
        if quantity < 0:
            raise SalesError(f"{line.item.name}: the quantity can't be negative.")
        open_quantity = line.quantity - line.dispatched_quantity
        if quantity > open_quantity:
            raise SalesError(f"{line.item.name}: only {open_quantity.normalize():f} {line.unit} is still to send on this order.")
        if line.item.tracks_stock:
            on_hand, _value = inventory.on_hand(company, line.item, warehouse)
            already = sum((q for l, q, _p in wanted if l.item_id == line.item_id), Decimal('0'))
            if quantity + already > on_hand:
                raise SalesError(f"{line.item.name}: only {on_hand.normalize():f} {line.unit} in {warehouse.name}.")
        wanted.append((line, quantity, row.get('batches') or []))
    if not wanted:
        raise SalesError("Enter the quantity sent for at least one line.")

    dispatch = Dispatch.objects.create(
        company=company, order=order, number=next_number(company, 'erp_dc', 'DC', dispatch_date), warehouse=warehouse,
        dispatch_date=dispatch_date, vehicle_number=(data.get('vehicle_number') or '').strip().upper()[:20],
        transporter=(data.get('transporter') or '').strip()[:100], notes=(data.get('notes') or '').strip()[:300],
        created_by=membership.user,
    )
    customer_name = order.customer.name or order.customer.legal_name
    for line, quantity, picks in wanted:
        DispatchLine.objects.create(dispatch=dispatch, order_line=line, quantity=quantity)
        if line.item.tracks_stock:
            # The batches picked, or else the first to expire; expired ones can't be sent.
            rows = batches.outgoing(company, line.item, warehouse, quantity, picks, day=dispatch_date)
            batches.write(company, line.item, warehouse, StockEntry.Kind.DISPATCH, rows, sign=-1,
                          rate=inventory.average_cost(company, line.item, warehouse), entry_date=dispatch_date,
                          reference=dispatch.number, note=f"To {customer_name} against {order.number}", user=membership.user)
        line.dispatched_quantity += quantity
        line.save(update_fields=['dispatched_quantity'])
    complete = all(l.dispatched_quantity >= l.quantity for l in order_lines.values())
    order.status = Order.Status.DISPATCHED if complete else Order.Status.PARTIALLY_DISPATCHED
    order.save(update_fields=['status', 'updated_at'])
    return dispatch


# --- Invoices -------------------------------------------------------------------------------

def invoiced_quantities(order, exclude_invoice=None):
    lines = SalesInvoiceLine.objects.filter(invoice__order=order).exclude(invoice__status=Invoice.Status.CANCELLED)
    if exclude_invoice is not None and exclude_invoice.pk:
        lines = lines.exclude(invoice=exclude_invoice)
    return {r['order_line_id']: r['quantity'] for r in lines.values('order_line_id').annotate(quantity=Sum('quantity'))}


def invoiceable(order, exclude_invoice=None):
    """{order line id: dispatched but not invoiced yet}."""
    invoiced = invoiced_quantities(order, exclude_invoice)
    return {line.pk: line.dispatched_quantity - invoiced.get(line.pk, Decimal('0')) for line in order.lines.all()}


@transaction.atomic
def save_invoice(membership, invoice, order, data):
    """Creates or updates a draft invoice against what's been dispatched."""
    company = membership.company
    if invoice.pk:
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status != Invoice.Status.DRAFT:
            raise SalesError("Only a draft invoice can be changed.")
        order = invoice.order
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status in (Order.Status.DRAFT, Order.Status.PENDING_APPROVAL, Order.Status.CANCELLED):
        raise SalesError("Invoices can only be raised against a confirmed order.")
    invoice_date = data.get('invoice_date') or timezone.localdate()
    _not_future(invoice_date, 'invoice date')

    open_quantities = invoiceable(order, exclude_invoice=invoice)
    order_lines = {line.pk: line for line in order.lines.select_related('item')}
    lines = []
    for row in data.get('lines') or []:
        line = order_lines.get(row.get('order_line_id'))
        if line is None:
            raise SalesError("A line isn't on this order.")
        quantity = _decimal(row.get('quantity'), 'the quantity')
        if quantity == 0:
            continue
        if quantity < 0:
            raise SalesError(f"{line.item.name}: the quantity can't be negative.")
        if quantity > open_quantities[line.pk]:
            raise SalesError(f"{line.item.name}: only {open_quantities[line.pk].normalize():f} {line.unit} has been "
                             "dispatched and not invoiced yet.")
        lines.append(SalesInvoiceLine(order_line=line, item=line.item, hsn_code=line.item.hsn_code, quantity=quantity,
                                      unit=line.unit, rate=line.rate, gst_rate=line.gst_rate))
    if not lines:
        raise SalesError("Enter the quantity to invoice for at least one line.")

    invoice.company, invoice.customer, invoice.order = company, order.customer, order
    invoice.invoice_date = invoice_date
    invoice.due_date = invoice_date + timedelta(days=order.payment_terms_days) if order.payment_terms_days is not None else None
    invoice.notes = (data.get('notes') or '').strip()
    invoice.place_of_supply, invoice.is_interstate = order.place_of_supply, order.is_interstate
    if invoice.pk is None:
        invoice.created_by = membership.user
    apply_totals(invoice, lines)
    invoice.save()
    invoice.lines.all().delete()
    for line in lines:
        line.invoice = invoice
    SalesInvoiceLine.objects.bulk_create(lines)
    return invoice


def _locked_invoice(invoice, *statuses):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if statuses and invoice.status not in statuses:
        raise SalesError(f"This invoice is {invoice.get_status_display().lower()}.")
    return invoice


@transaction.atomic
def post_invoice(membership, invoice):
    """Posts a draft and gives it the next number in the series."""
    invoice = _locked_invoice(invoice, Invoice.Status.DRAFT)
    # Quantities may have been invoiced elsewhere since the draft was saved.
    open_quantities = invoiceable(invoice.order, exclude_invoice=invoice)
    for line in invoice.lines.select_related('item'):
        if line.quantity > open_quantities[line.order_line_id]:
            raise SalesError(f"{line.item.name}: more than what's dispatched and not invoiced. Edit the draft.")
    invoice.number = next_number(invoice.company, 'erp_inv', 'INV', invoice.invoice_date)
    invoice.status = Invoice.Status.POSTED
    invoice.posted_by, invoice.posted_at = membership.user, timezone.now()
    invoice.save()
    return invoice


@transaction.atomic
def cancel_invoice(membership, invoice, reason):
    reason = (reason or '').strip()
    if not reason:
        raise SalesError("Say why the invoice is being cancelled.")
    invoice = _locked_invoice(invoice, Invoice.Status.POSTED)
    from finance.services import has_live_payments
    if has_live_payments(invoice):
        raise SalesError("Money has been received against this invoice. Cancel that receipt first.")
    from adjustments.services import has_live_notes
    if has_live_notes(invoice):
        raise SalesError("There are credit or debit notes against this invoice. Cancel them first.")
    invoice.status = Invoice.Status.CANCELLED
    invoice.cancelled_at, invoice.cancel_reason = timezone.now(), reason[:300]
    invoice.save()
    return invoice


@transaction.atomic
def delete_invoice(invoice):
    invoice = _locked_invoice(invoice, Invoice.Status.DRAFT)
    invoice.delete()
