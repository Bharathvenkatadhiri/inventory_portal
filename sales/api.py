"""/api/v1 endpoints for selling, on the organization the request is for.
Sales documents can be seen by anyone whose role does any sales work
(VIEW_PERMISSIONS); each change needs its own permission and a
subscription that isn't lapsed."""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from finance.services import balance
from organizations import access
from organizations.app_api import membership_for
from purchase.api import DocRefOut, OrderLineOut, OrderSummaryOut, PartyOut, _name, _party, _summary, _totals, _writes_allowed
from purchase.models import PurchaseOrder
from purchase.services import approvals_on, orders_received

from . import services
from .models import Dispatch, SalesInvoice, SalesOrder

router = Router(tags=['sales'])
Order, Invoice, PO = SalesOrder, SalesInvoice, PurchaseOrder

VIEW_PERMISSIONS = ('so.create', 'so.approve', 'quotation.create', 'invoice.create', 'dispatch.post', 'payment.record', 'ledger.view')
LIST_LIMIT = 500


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.SalesError as exc:
        raise HttpError(400, str(exc))


def _can(membership, code):
    return bool(access.check(membership, code))


def viewer(request):
    membership = membership_for(request)
    if not any(_can(membership, code) for code in VIEW_PERMISSIONS):
        raise HttpError(403, "Your role doesn't include sales.")
    return membership


def _actor(request, code):
    """The membership, if the person may do `code` and changes are allowed."""
    return membership_for(request, code, write=True)


# --- Customers ------------------------------------------------------------------------

class CustomerOut(PartyOut):
    payment_terms_days: Optional[int]
    customer_code: str
    address: str


@router.get('/sales/customers', response=List[CustomerOut])
def list_customers(request):
    membership = viewer(request)
    return [{**_party(r.buyer), 'payment_terms_days': r.payment_terms_days, 'customer_code': r.customer_code,
             'address': r.buyer.principal_address} for r in services.customers(membership.company)]


# --- Orders received (customers' purchase orders) -------------------------------------------

class ReceivedOrderOut(OrderSummaryOut):
    buyer: PartyOut
    ship_to_address: str
    payment_terms_days: Optional[int]
    notes: str
    lines: List[OrderLineOut]
    supplier_response: str
    supplier_response_label: str
    decline_reason: str
    responded_by: str
    sales_order_id: Optional[int]
    sales_order_number: str
    can_reply: bool
    # {PO line id: our item id} from what we matched last time.
    suggested_items: dict


def _received_out(po, membership, can_reply):
    address = po.ship_to.address
    sales_order = getattr(po, 'sales_order', None)
    replying = can_reply and not po.supplier_response and po.status in (PO.Status.ISSUED, PO.Status.PARTIALLY_RECEIVED)
    return {
        **_summary(po), 'buyer': _party(po.company),
        'ship_to_address': ', '.join(p for p in (address.line1, address.city, address.pincode) if p) if address else '',
        'payment_terms_days': po.payment_terms_days, 'notes': po.notes,
        'lines': [{
            'id': l.pk, 'item_id': l.item_id, 'item_code': l.item.code, 'item_name': l.item.name, 'description': l.description,
            'quantity': l.quantity, 'unit': l.unit, 'rate': l.rate, 'gst_rate': l.gst_rate, 'taxable_value': l.taxable_value,
            'tax_amount': l.tax_amount, 'received_quantity': l.received_quantity, 'billed_quantity': Decimal('0'),
            'billable_quantity': Decimal('0'),
        } for l in po.lines.select_related('item')],
        'supplier_response': po.supplier_response, 'supplier_response_label': po.get_supplier_response_display(),
        'decline_reason': po.decline_reason, 'responded_by': _name(po.supplier_responded_by),
        'sales_order_id': sales_order.pk if sales_order else None, 'sales_order_number': sales_order.number if sales_order else '',
        'can_reply': replying,
        'suggested_items': {str(k): v for k, v in services.suggested_items(membership.company, po).items()} if replying else {},
    }


def _may_reply(membership):
    return _writes_allowed(membership) and _can(membership, services.reply_permission(membership.company))


@router.get('/sales/orders-received', response=List[ReceivedOrderOut])
def list_orders_received(request):
    """Purchase orders customers on MakeSetu have issued to this organization:
    the same rows they see under Purchase, nothing copied."""
    membership = viewer(request)
    can_reply = _may_reply(membership)
    orders = orders_received(membership.company).select_related(
        'ship_to__address', 'supplier', 'supplier_responded_by', 'sales_order')[:LIST_LIMIT]
    return [_received_out(po, membership, can_reply) for po in orders]


def _received_po(membership, po_id):
    return get_object_or_404(orders_received(membership.company), pk=po_id)


class AcceptLineIn(Schema):
    purchase_order_line_id: int
    item_id: Optional[int] = None


class AcceptIn(Schema):
    dispatch_from_id: int
    lines: List[AcceptLineIn]


class ReasonIn(Schema):
    reason: str = ''


class AcceptOut(Schema):
    sales_order_id: int
    sales_order_number: str


@router.post('/sales/orders-received/{po_id}/accept', response=AcceptOut)
def accept_order(request, po_id: int, payload: AcceptIn):
    membership = _actor(request, services.reply_permission(membership_for(request).company))
    order = _run(services.accept, membership, _received_po(membership, po_id), payload.dict())
    return {'sales_order_id': order.pk, 'sales_order_number': order.number}


@router.post('/sales/orders-received/{po_id}/decline', response={204: None})
def decline_order(request, po_id: int, payload: ReasonIn):
    membership = _actor(request, services.reply_permission(membership_for(request).company))
    _run(services.decline, membership, _received_po(membership, po_id), payload.reason)
    return 204, None


# --- Sales orders -----------------------------------------------------------------------------

class SalesLineIn(Schema):
    item_id: int
    quantity: Decimal
    rate: Optional[Decimal] = None
    gst_rate: Optional[Decimal] = None
    description: str = ''


class SalesOrderIn(Schema):
    customer_id: int
    dispatch_from_id: int
    order_date: Optional[date] = None
    delivery_date: Optional[date] = None
    customer_po_number: str = ''
    ship_to: str = ''
    payment_terms_days: Optional[int] = None
    notes: str = ''
    lines: List[SalesLineIn]


class SalesLineOut(Schema):
    id: int
    item_id: int
    item_code: str
    item_name: str
    hsn_code: str
    description: str
    customer_item: str
    quantity: Decimal
    unit: str
    rate: Decimal
    gst_rate: Decimal
    taxable_value: Decimal
    tax_amount: Decimal
    dispatched_quantity: Decimal
    invoiced_quantity: Decimal
    invoiceable_quantity: Decimal
    # On hand in the order's dispatch-from warehouse (null for services or without stock.view).
    on_hand: Optional[Decimal]


class SalesSummaryOut(Schema):
    id: int
    number: str
    status: str
    status_label: str
    order_date: date
    delivery_date: Optional[date]
    customer: PartyOut
    customer_po_number: str
    from_purchase_order: bool
    dispatch_from: str
    taxable_value: Decimal
    total: Decimal


class SalesActionsOut(Schema):
    edit: bool
    submit: bool
    approve: bool
    cancel: bool
    close: bool
    dispatch: bool
    invoice: bool


class SalesOrderOut(SalesSummaryOut):
    place_of_supply: str
    is_interstate: bool
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal
    dispatch_from_id: int
    ship_to: str
    payment_terms_days: Optional[int]
    notes: str
    rejection_reason: str
    created_by: str
    approved_by: str
    lines: List[SalesLineOut]
    dispatches: List[DocRefOut]
    invoices: List[DocRefOut]
    actions: SalesActionsOut
    submit_needs_approval: bool
    cancel_needs_reason: bool


def _sales_summary(o):
    return {
        'id': o.pk, 'number': o.number, 'status': o.status, 'status_label': o.get_status_display(), 'order_date': o.order_date,
        'delivery_date': o.delivery_date, 'customer': _party(o.customer), 'customer_po_number': o.customer_po_number,
        'from_purchase_order': bool(o.purchase_order_id), 'dispatch_from': o.dispatch_from.name,
        'taxable_value': o.taxable_value, 'total': o.total,
    }


def _sales_out(order, membership):
    from inventory import services as inventory

    invoiced = services.invoiced_quantities(order)
    see_stock = _can(membership, 'stock.view')
    lines = []
    for l in order.lines.select_related('item', 'customer_item'):
        done = invoiced.get(l.pk, Decimal('0'))
        lines.append({
            'id': l.pk, 'item_id': l.item_id, 'item_code': l.item.code, 'item_name': l.item.name, 'hsn_code': l.item.hsn_code,
            'description': l.description, 'customer_item': f"{l.customer_item.code} {l.customer_item.name}" if l.customer_item else '',
            'quantity': l.quantity, 'unit': l.unit, 'rate': l.rate, 'gst_rate': l.gst_rate, 'taxable_value': l.taxable_value,
            'tax_amount': l.tax_amount, 'dispatched_quantity': l.dispatched_quantity, 'invoiced_quantity': done,
            'invoiceable_quantity': l.dispatched_quantity - done,
            'on_hand': inventory.on_hand(order.company, l.item, order.dispatch_from)[0] if see_stock and l.item.tracks_stock else None,
        })
    S = Order.Status
    writable = _writes_allowed(membership)
    can = lambda code: writable and _can(membership, code)  # noqa: E731
    nothing_sent = not order.dispatches.exists()
    return {
        **_sales_summary(order), **_totals(order),
        'dispatch_from_id': order.dispatch_from_id, 'ship_to': order.ship_to, 'payment_terms_days': order.payment_terms_days,
        'notes': order.notes, 'rejection_reason': order.rejection_reason, 'created_by': _name(order.created_by),
        'approved_by': _name(order.approved_by), 'lines': lines,
        'dispatches': [{
            'id': d.pk, 'number': d.number, 'date': d.dispatch_date, 'status': 'posted', 'status_label': 'Posted',
            'detail': f"From {d.warehouse.name}" + (f" · {d.vehicle_number}" if d.vehicle_number else ''), 'total': None,
        } for d in order.dispatches.select_related('warehouse')],
        'invoices': [{
            'id': i.pk, 'number': i.number or 'Draft', 'date': i.invoice_date, 'status': i.status, 'status_label': i.get_status_display(),
            'detail': f"Due {i.due_date:%d %b %Y}" if i.due_date else '', 'total': i.total,
        } for i in order.invoices.all()],
        'actions': {
            'edit': order.status == S.DRAFT and can('so.create'),
            'submit': order.status == S.DRAFT and can('so.create'),
            'approve': order.status == S.PENDING_APPROVAL and can('so.approve'),
            'cancel': order.status in (S.PENDING_APPROVAL, S.CONFIRMED) and nothing_sent and (can('so.approve') or can('so.create')),
            'close': order.status == S.PARTIALLY_DISPATCHED and (can('so.approve') or can('so.create')),
            'dispatch': order.status in (S.CONFIRMED, S.PARTIALLY_DISPATCHED) and can('dispatch.post'),
            'invoice': order.status not in (S.DRAFT, S.PENDING_APPROVAL, S.CANCELLED)
                       and any(l['invoiceable_quantity'] > 0 for l in lines) and can('invoice.create'),
        },
        'submit_needs_approval': approvals_on(order.company) and not _can(membership, 'so.approve'),
        'cancel_needs_reason': bool(order.purchase_order_id),
    }


def _order(membership, order_id):
    return get_object_or_404(Order.objects.select_related('customer', 'dispatch_from', 'company', 'created_by', 'approved_by'),
                             pk=order_id, company=membership.company)


@router.get('/sales-orders', response=List[SalesSummaryOut])
def list_sales_orders(request, status: str = '', q: str = ''):
    membership = viewer(request)
    orders = Order.objects.filter(company=membership.company).select_related('customer', 'dispatch_from')
    if status == 'open':
        orders = orders.filter(status__in=[Order.Status.CONFIRMED, Order.Status.PARTIALLY_DISPATCHED])
    elif status:
        orders = orders.filter(status=status)
    q = q.strip()
    if q:
        orders = orders.filter(Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(customer_po_number__icontains=q))
    return [_sales_summary(o) for o in orders[:LIST_LIMIT]]


@router.get('/sales-orders/{order_id}', response=SalesOrderOut)
def get_sales_order(request, order_id: int):
    membership = viewer(request)
    return _sales_out(_order(membership, order_id), membership)


@router.post('/sales-orders', response=SalesOrderOut)
def create_sales_order(request, payload: SalesOrderIn):
    membership = _actor(request, 'so.create')
    order = _run(services.save_order, membership, Order(), payload.dict())
    return _sales_out(_order(membership, order.pk), membership)


@router.put('/sales-orders/{order_id}', response=SalesOrderOut)
def update_sales_order(request, order_id: int, payload: SalesOrderIn):
    membership = _actor(request, 'so.create')
    order = _run(services.save_order, membership, _order(membership, order_id), payload.dict())
    return _sales_out(_order(membership, order.pk), membership)


@router.delete('/sales-orders/{order_id}', response={204: None})
def delete_sales_order(request, order_id: int):
    membership = _actor(request, 'so.create')
    _run(services.delete_order, _order(membership, order_id))
    return 204, None


def _order_action(request, order_id, code, action, *args):
    membership = _actor(request, code)
    order = _run(action, membership, _order(membership, order_id), *args)
    return _sales_out(_order(membership, order.pk), membership)


@router.post('/sales-orders/{order_id}/submit', response=SalesOrderOut)
def submit_sales_order(request, order_id: int):
    return _order_action(request, order_id, 'so.create', services.submit_order)


@router.post('/sales-orders/{order_id}/approve', response=SalesOrderOut)
def approve_sales_order(request, order_id: int):
    return _order_action(request, order_id, 'so.approve', services.approve_order)


@router.post('/sales-orders/{order_id}/reject', response=SalesOrderOut)
def reject_sales_order(request, order_id: int, payload: ReasonIn):
    return _order_action(request, order_id, 'so.approve', services.reject_order, payload.reason)


def _create_or_approve(request):
    membership = membership_for(request, write=True)
    if not (_can(membership, 'so.create') or _can(membership, 'so.approve')):
        raise HttpError(403, "Your role doesn't include sales orders.")
    return membership


@router.post('/sales-orders/{order_id}/cancel', response=SalesOrderOut)
def cancel_sales_order(request, order_id: int, payload: ReasonIn):
    membership = _create_or_approve(request)
    order = _run(services.cancel_order, membership, _order(membership, order_id), payload.reason)
    return _sales_out(_order(membership, order.pk), membership)


@router.post('/sales-orders/{order_id}/close', response=SalesOrderOut)
def close_sales_order(request, order_id: int):
    membership = _create_or_approve(request)
    order = _run(services.close_order, membership, _order(membership, order_id))
    return _sales_out(_order(membership, order.pk), membership)


# --- Dispatches ---------------------------------------------------------------------------------

class DispatchLineIn(Schema):
    order_line_id: int
    quantity: Decimal


class DispatchIn(Schema):
    warehouse_id: Optional[int] = None
    dispatch_date: Optional[date] = None
    vehicle_number: str = ''
    transporter: str = ''
    notes: str = ''
    lines: List[DispatchLineIn]


class DispatchLineOut(Schema):
    item_code: str
    item_name: str
    quantity: Decimal
    unit: str


class DispatchOut(Schema):
    id: int
    number: str
    order_id: int
    order_number: str
    customer: PartyOut
    warehouse: str
    dispatch_date: date
    vehicle_number: str
    transporter: str
    notes: str
    dispatched_by: str
    lines: List[DispatchLineOut]


def _dispatch_out(d):
    return {
        'id': d.pk, 'number': d.number, 'order_id': d.order_id, 'order_number': d.order.number, 'customer': _party(d.order.customer),
        'warehouse': d.warehouse.name, 'dispatch_date': d.dispatch_date, 'vehicle_number': d.vehicle_number,
        'transporter': d.transporter, 'notes': d.notes, 'dispatched_by': _name(d.created_by),
        'lines': [{'item_code': l.order_line.item.code, 'item_name': l.order_line.item.name, 'quantity': l.quantity,
                   'unit': l.order_line.unit} for l in d.lines.select_related('order_line__item')],
    }


@router.post('/sales-orders/{order_id}/dispatches', response=DispatchOut)
def dispatch(request, order_id: int, payload: DispatchIn):
    membership = _actor(request, 'dispatch.post')
    return _dispatch_out(_run(services.post_dispatch, membership, _order(membership, order_id), payload.dict()))


@router.get('/dispatches', response=List[DispatchOut])
def list_dispatches(request):
    membership = viewer(request)
    dispatches = (Dispatch.objects.filter(company=membership.company)
                  .select_related('order__customer', 'warehouse', 'created_by')[:LIST_LIMIT])
    return [_dispatch_out(d) for d in dispatches]


# --- Invoices --------------------------------------------------------------------------------------

class InvoiceLineIn(Schema):
    order_line_id: int
    quantity: Decimal


class InvoiceIn(Schema):
    order_id: int
    invoice_date: Optional[date] = None
    notes: str = ''
    lines: List[InvoiceLineIn]


class InvoiceLineOut(Schema):
    order_line_id: int
    item_code: str
    item_name: str
    hsn_code: str
    quantity: Decimal
    unit: str
    rate: Decimal
    gst_rate: Decimal
    taxable_value: Decimal
    tax_amount: Decimal


class InvoiceActionsOut(Schema):
    edit: bool
    post: bool
    cancel: bool


class InvoiceOut(Schema):
    id: int
    number: str
    status: str
    status_label: str
    order_id: int
    order_number: str
    customer_po_number: str
    seller: PartyOut
    customer: PartyOut
    ship_to: str
    invoice_date: date
    due_date: Optional[date]
    notes: str
    cancel_reason: str
    place_of_supply: str
    is_interstate: bool
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal
    total: Decimal
    created_by: str
    posted_by: str
    lines: List[InvoiceLineOut]
    actions: InvoiceActionsOut
    # Money settled by payments (finance): paid by posted payments, pending in ones waiting for approval.
    # The total after posted credit and debit notes; outstanding is this less paid
    # (negative when a credit note leaves money due back).
    adjusted_total: Decimal
    paid: Decimal
    payment_pending: Decimal
    outstanding: Decimal
    payment_status: str
    payment_status_label: str


def _invoice_out(invoice, membership):
    S = Invoice.Status
    can = _writes_allowed(membership) and _can(membership, 'invoice.create')
    return {
        **_totals(invoice), 'id': invoice.pk, 'number': invoice.number, 'status': invoice.status,
        'status_label': invoice.get_status_display(), 'order_id': invoice.order_id, 'order_number': invoice.order.number,
        'customer_po_number': invoice.order.customer_po_number, 'seller': _party(invoice.company),
        'customer': _party(invoice.customer), 'ship_to': invoice.order.ship_to, 'invoice_date': invoice.invoice_date,
        'due_date': invoice.due_date, 'notes': invoice.notes, 'cancel_reason': invoice.cancel_reason,
        'created_by': _name(invoice.created_by), 'posted_by': _name(invoice.posted_by),
        'lines': [{
            'order_line_id': l.order_line_id, 'item_code': l.item.code, 'item_name': l.item.name, 'hsn_code': l.hsn_code,
            'quantity': l.quantity, 'unit': l.unit, 'rate': l.rate, 'gst_rate': l.gst_rate, 'taxable_value': l.taxable_value,
            'tax_amount': l.tax_amount,
        } for l in invoice.lines.select_related('item')],
        'actions': {'edit': invoice.status == S.DRAFT and can, 'post': invoice.status == S.DRAFT and can,
                    'cancel': invoice.status == S.POSTED and can},
        **balance(invoice),
    }


def _invoice(membership, invoice_id):
    return get_object_or_404(Invoice.objects.select_related('customer', 'company', 'order', 'created_by', 'posted_by'),
                             pk=invoice_id, company=membership.company)


@router.get('/sales-invoices', response=List[InvoiceOut])
def list_invoices(request, status: str = ''):
    membership = viewer(request)
    invoices = Invoice.objects.filter(company=membership.company).select_related('customer', 'company', 'order', 'created_by', 'posted_by')
    if status:
        invoices = invoices.filter(status=status)
    return [_invoice_out(i, membership) for i in invoices[:LIST_LIMIT]]


@router.get('/sales-invoices/{invoice_id}', response=InvoiceOut)
def get_invoice(request, invoice_id: int):
    membership = viewer(request)
    return _invoice_out(_invoice(membership, invoice_id), membership)


@router.post('/sales-invoices', response=InvoiceOut)
def create_invoice(request, payload: InvoiceIn):
    membership = _actor(request, 'invoice.create')
    invoice = _run(services.save_invoice, membership, Invoice(), _order(membership, payload.order_id), payload.dict())
    return _invoice_out(_invoice(membership, invoice.pk), membership)


@router.put('/sales-invoices/{invoice_id}', response=InvoiceOut)
def update_invoice(request, invoice_id: int, payload: InvoiceIn):
    membership = _actor(request, 'invoice.create')
    invoice = _invoice(membership, invoice_id)
    invoice = _run(services.save_invoice, membership, invoice, invoice.order, payload.dict())
    return _invoice_out(_invoice(membership, invoice.pk), membership)


@router.delete('/sales-invoices/{invoice_id}', response={204: None})
def delete_invoice(request, invoice_id: int):
    membership = _actor(request, 'invoice.create')
    _run(services.delete_invoice, _invoice(membership, invoice_id))
    return 204, None


@router.post('/sales-invoices/{invoice_id}/post', response=InvoiceOut)
def post_invoice(request, invoice_id: int):
    membership = _actor(request, 'invoice.create')
    invoice = _run(services.post_invoice, membership, _invoice(membership, invoice_id))
    return _invoice_out(_invoice(membership, invoice.pk), membership)


@router.post('/sales-invoices/{invoice_id}/cancel', response=InvoiceOut)
def cancel_invoice(request, invoice_id: int, payload: ReasonIn):
    membership = _actor(request, 'invoice.create')
    invoice = _run(services.cancel_invoice, membership, _invoice(membership, invoice_id), payload.reason)
    return _invoice_out(_invoice(membership, invoice.pk), membership)
