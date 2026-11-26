"""/api/v1 endpoints for purchasing, on the organization the request is for
(organizations.workspace). Purchase documents can be seen by anyone whose
role does any purchasing work (VIEW_PERMISSIONS); each change needs its own
permission and a subscription that isn't lapsed. The supplier's read-only
view of orders it received is under /sales/orders-received."""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from organizations import access
from organizations.app_api import membership_for

from . import services
from .models import GoodsReceipt, PurchaseBill, PurchaseOrder

router = Router(tags=['purchase'])
Order, Bill = PurchaseOrder, PurchaseBill

VIEW_PERMISSIONS = ('po.create', 'po.approve', 'grn.post', 'purchase_bill.create', 'purchase_bill.approve',
                    'payment.record', 'ledger.view')
LIST_LIMIT = 500


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.PurchaseError as exc:
        raise HttpError(400, str(exc))


def _can(membership, code):
    return bool(access.check(membership, code))


def viewer(request, permissions=VIEW_PERMISSIONS):
    """The membership, if the person's role does any purchasing work."""
    membership = membership_for(request)
    if not any(_can(membership, code) for code in permissions):
        raise HttpError(403, "Your role doesn't include purchasing.")
    return membership


# --- Shared schemas ------------------------------------------------------------------

class PartyOut(Schema):
    id: int
    name: str
    gstin: str
    state: str


def _party(company):
    return {'id': company.pk, 'name': company.name or company.trade_name or company.legal_name,
            'gstin': company.gstin or '', 'state': company.state}


class TotalsOut(Schema):
    place_of_supply: str
    is_interstate: bool
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal
    total: Decimal


def _totals(doc):
    return {f: getattr(doc, f) for f in ('place_of_supply', 'is_interstate', 'taxable_value', 'cgst', 'sgst', 'igst',
                                         'round_off', 'total')}


def _name(user):
    return (user.get_full_name() or user.email) if user else ''


# --- Suppliers -----------------------------------------------------------------------

class SupplierOut(PartyOut):
    payment_terms_days: Optional[int]
    vendor_code: str


@router.get('/purchase/suppliers', response=List[SupplierOut])
def list_suppliers(request):
    """Organizations this one can order from (active relationships where it buys)."""
    membership = viewer(request)
    return [{**_party(r.supplier), 'payment_terms_days': r.payment_terms_days, 'vendor_code': r.vendor_code}
            for r in services.suppliers(membership.company)]


# --- Purchase orders -----------------------------------------------------------------

class OrderLineIn(Schema):
    item_id: int
    quantity: Decimal
    rate: Optional[Decimal] = None
    gst_rate: Optional[Decimal] = None
    description: str = ''


class OrderIn(Schema):
    supplier_id: int
    ship_to_id: int
    order_date: Optional[date] = None
    expected_date: Optional[date] = None
    payment_terms_days: Optional[int] = None
    supplier_reference: str = ''
    notes: str = ''
    lines: List[OrderLineIn]


class OrderLineOut(Schema):
    id: int
    item_id: int
    item_code: str
    item_name: str
    description: str
    quantity: Decimal
    unit: str
    rate: Decimal
    gst_rate: Decimal
    taxable_value: Decimal
    tax_amount: Decimal
    received_quantity: Decimal
    billed_quantity: Decimal
    # Received but not billed yet.
    billable_quantity: Decimal


class OrderSummaryOut(TotalsOut):
    id: int
    number: str
    status: str
    status_label: str
    order_date: date
    expected_date: Optional[date]
    supplier: PartyOut
    ship_to: str
    # The supplier's reply; they only reply when they're on MakeSetu.
    supplier_on_network: bool
    supplier_response: str
    supplier_response_label: str


class DocRefOut(Schema):
    id: int
    number: str
    date: date
    status: str
    status_label: str
    detail: str
    total: Optional[Decimal]


class OrderActionsOut(Schema):
    edit: bool
    submit: bool
    approve: bool
    cancel: bool
    close: bool
    receive: bool
    bill: bool


class OrderOut(OrderSummaryOut):
    ship_to_id: int
    payment_terms_days: Optional[int]
    supplier_reference: str
    notes: str
    rejection_reason: str
    created_by: str
    approved_by: str
    issued_at: Optional[str]
    lines: List[OrderLineOut]
    receipts: List[DocRefOut]
    bills: List[DocRefOut]
    actions: OrderActionsOut
    # What happens on submit for this person: issue now, or ask for approval.
    submit_needs_approval: bool
    decline_reason: str
    # The supplier's dispatches and posted invoices against this order (same records they see).
    supplier_dispatches: List[DocRefOut]
    supplier_invoices: List[DocRefOut]


def _summary(order):
    from organizations.partners import on_network
    return {
        **_totals(order), 'id': order.pk, 'number': order.number, 'status': order.status,
        'status_label': order.get_status_display(), 'order_date': order.order_date, 'expected_date': order.expected_date,
        'supplier': _party(order.supplier), 'ship_to': order.ship_to.name,
        'supplier_on_network': on_network(order.supplier), 'supplier_response': order.supplier_response,
        'supplier_response_label': order.get_supplier_response_display(),
    }


def _order_out(order, membership):
    billed = services.billed_quantities(order)
    lines = []
    for line in order.lines.select_related('item'):
        billed_quantity = billed.get(line.pk, Decimal('0'))
        lines.append({
            'id': line.pk, 'item_id': line.item_id, 'item_code': line.item.code, 'item_name': line.item.name,
            'description': line.description, 'quantity': line.quantity, 'unit': line.unit, 'rate': line.rate,
            'gst_rate': line.gst_rate, 'taxable_value': line.taxable_value, 'tax_amount': line.tax_amount,
            'received_quantity': line.received_quantity, 'billed_quantity': billed_quantity,
            'billable_quantity': line.received_quantity - billed_quantity,
        })
    S = Order.Status
    writable = _writes_allowed(membership)
    can = lambda code: writable and _can(membership, code)  # noqa: E731
    something_to_bill = any(l['billable_quantity'] > 0 for l in lines)
    return {
        **_summary(order),
        'ship_to_id': order.ship_to_id, 'payment_terms_days': order.payment_terms_days,
        'supplier_reference': order.supplier_reference, 'notes': order.notes, 'rejection_reason': order.rejection_reason,
        'created_by': _name(order.created_by), 'approved_by': _name(order.approved_by),
        'issued_at': order.issued_at.isoformat() if order.issued_at else None,
        'lines': lines,
        'receipts': [{
            'id': r.pk, 'number': r.number, 'date': r.received_date, 'status': 'posted', 'status_label': 'Posted',
            'detail': f"Into {r.warehouse.name}" + (f" · challan {r.supplier_challan}" if r.supplier_challan else ''),
            'total': None,
        } for r in order.receipts.select_related('warehouse')],
        'bills': [{
            'id': b.pk, 'number': b.number, 'date': b.invoice_date, 'status': b.status, 'status_label': b.get_status_display(),
            'detail': f"Invoice {b.supplier_invoice_number}", 'total': b.total,
        } for b in order.bills.all()],
        'actions': {
            'edit': order.status == S.DRAFT and can('po.create'),
            'submit': order.status == S.DRAFT and can('po.create'),
            'approve': order.status == S.PENDING_APPROVAL and can('po.approve'),
            'cancel': order.status in (S.PENDING_APPROVAL, S.ISSUED) and not order.receipts.exists()
                      and (can('po.approve') or can('po.create')),
            'close': order.status == S.PARTIALLY_RECEIVED and (can('po.approve') or can('po.create')),
            'receive': order.status in (S.ISSUED, S.PARTIALLY_RECEIVED) and can('grn.post')
                       and order.supplier_response != Order.SupplierResponse.DECLINED,
            'bill': order.status in (S.ISSUED, S.PARTIALLY_RECEIVED, S.RECEIVED, S.CLOSED) and something_to_bill
                    and can('purchase_bill.create'),
        },
        'submit_needs_approval': services.approvals_on(order.company) and not _can(membership, 'po.approve'),
        'decline_reason': order.decline_reason,
        **_supplier_documents(order),
    }


def _supplier_documents(order):
    """What the supplier has sent and invoiced against this order, if they
    accepted it on MakeSetu."""
    sales_order = getattr(order, 'sales_order', None)
    if sales_order is None:
        return {'supplier_dispatches': [], 'supplier_invoices': []}
    return {
        'supplier_dispatches': [{
            'id': d.pk, 'number': d.number, 'date': d.dispatch_date, 'status': 'posted', 'status_label': 'Dispatched',
            'detail': ', '.join(f"{l.quantity.normalize():f} {l.order_line.unit} {l.order_line.description or l.order_line.item.name}"
                                for l in d.lines.select_related('order_line__item'))
                      + (f" · {d.vehicle_number}" if d.vehicle_number else ''),
            'total': None,
        } for d in sales_order.dispatches.all()],
        'supplier_invoices': [{
            'id': i.pk, 'number': i.number, 'date': i.invoice_date, 'status': i.status, 'status_label': i.get_status_display(),
            'detail': f"Due {i.due_date:%d %b %Y}" if i.due_date else '', 'total': i.total,
        } for i in sales_order.invoices.exclude(status='draft')],
    }


def _writes_allowed(membership):
    from billing import erp as erp_billing
    return erp_billing.writes_allowed(membership.company)


def _order(membership, order_id):
    return get_object_or_404(Order.objects.select_related('supplier', 'ship_to', 'company', 'created_by', 'approved_by'),
                             pk=order_id, company=membership.company)


@router.get('/purchase-orders', response=List[OrderSummaryOut])
def list_orders(request, status: str = '', q: str = ''):
    membership = viewer(request)
    orders = Order.objects.filter(company=membership.company).select_related('supplier', 'ship_to')
    if status == 'open':
        orders = orders.filter(status__in=[Order.Status.ISSUED, Order.Status.PARTIALLY_RECEIVED])
    elif status:
        orders = orders.filter(status=status)
    q = q.strip()
    if q:
        orders = orders.filter(Q(number__icontains=q) | Q(supplier__name__icontains=q) | Q(supplier__legal_name__icontains=q))
    return [_summary(o) for o in orders[:LIST_LIMIT]]


@router.get('/purchase-orders/{order_id}', response=OrderOut)
def get_order(request, order_id: int):
    membership = viewer(request)
    return _order_out(_order(membership, order_id), membership)


@router.post('/purchase-orders', response=OrderOut)
def create_order(request, payload: OrderIn):
    membership = membership_for(request, 'po.create', write=True)
    order = _run(services.save_order, membership, Order(), payload.dict())
    return _order_out(_order(membership, order.pk), membership)


@router.put('/purchase-orders/{order_id}', response=OrderOut)
def update_order(request, order_id: int, payload: OrderIn):
    membership = membership_for(request, 'po.create', write=True)
    order = _run(services.save_order, membership, _order(membership, order_id), payload.dict())
    return _order_out(_order(membership, order.pk), membership)


@router.delete('/purchase-orders/{order_id}', response={204: None})
def delete_order(request, order_id: int):
    membership = membership_for(request, 'po.create', write=True)
    _run(services.delete_order, _order(membership, order_id))
    return 204, None


class ReasonIn(Schema):
    reason: str = ''


def _order_action(request, order_id, permission, action, *args):
    membership = membership_for(request, permission, write=True)
    order = _run(action, membership, _order(membership, order_id), *args)
    return _order_out(_order(membership, order.pk), membership)


@router.post('/purchase-orders/{order_id}/submit', response=OrderOut)
def submit_order(request, order_id: int):
    return _order_action(request, order_id, 'po.create', services.submit_order)


@router.post('/purchase-orders/{order_id}/approve', response=OrderOut)
def approve_order(request, order_id: int):
    return _order_action(request, order_id, 'po.approve', services.approve_order)


@router.post('/purchase-orders/{order_id}/reject', response=OrderOut)
def reject_order(request, order_id: int, payload: ReasonIn):
    return _order_action(request, order_id, 'po.approve', services.reject_order, payload.reason)


def _create_or_approve(request):
    membership = membership_for(request, write=True)
    if not (_can(membership, 'po.create') or _can(membership, 'po.approve')):
        raise HttpError(403, "Your role doesn't include purchase orders.")
    return membership


@router.post('/purchase-orders/{order_id}/cancel', response=OrderOut)
def cancel_order(request, order_id: int):
    membership = _create_or_approve(request)
    order = _run(services.cancel_order, membership, _order(membership, order_id))
    return _order_out(_order(membership, order.pk), membership)


@router.post('/purchase-orders/{order_id}/close', response=OrderOut)
def close_order(request, order_id: int):
    membership = _create_or_approve(request)
    order = _run(services.close_order, membership, _order(membership, order_id))
    return _order_out(_order(membership, order.pk), membership)


# --- Goods receipts --------------------------------------------------------------------

class ReceiptLineIn(Schema):
    order_line_id: int
    quantity: Decimal


class ReceiptIn(Schema):
    warehouse_id: Optional[int] = None
    received_date: Optional[date] = None
    supplier_challan: str = ''
    vehicle_number: str = ''
    notes: str = ''
    lines: List[ReceiptLineIn]


class ReceiptLineOut(Schema):
    item_code: str
    item_name: str
    quantity: Decimal
    unit: str


class ReceiptOut(Schema):
    id: int
    number: str
    order_id: int
    order_number: str
    supplier: PartyOut
    warehouse: str
    received_date: date
    supplier_challan: str
    vehicle_number: str
    notes: str
    received_by: str
    lines: List[ReceiptLineOut]


def _receipt_out(r):
    return {
        'id': r.pk, 'number': r.number, 'order_id': r.order_id, 'order_number': r.order.number,
        'supplier': _party(r.order.supplier), 'warehouse': r.warehouse.name, 'received_date': r.received_date,
        'supplier_challan': r.supplier_challan, 'vehicle_number': r.vehicle_number, 'notes': r.notes,
        'received_by': _name(r.created_by),
        'lines': [{'item_code': l.order_line.item.code, 'item_name': l.order_line.item.name, 'quantity': l.quantity,
                   'unit': l.order_line.unit} for l in r.lines.select_related('order_line__item')],
    }


@router.post('/purchase-orders/{order_id}/receipts', response=ReceiptOut)
def receive(request, order_id: int, payload: ReceiptIn):
    membership = membership_for(request, 'grn.post', write=True)
    receipt = _run(services.post_receipt, membership, _order(membership, order_id), payload.dict())
    return _receipt_out(receipt)


@router.get('/goods-receipts', response=List[ReceiptOut])
def list_receipts(request):
    membership = viewer(request)
    receipts = (GoodsReceipt.objects.filter(company=membership.company)
                .select_related('order__supplier', 'warehouse', 'created_by')[:LIST_LIMIT])
    return [_receipt_out(r) for r in receipts]


# --- Bills ---------------------------------------------------------------------------

class BillLineIn(Schema):
    order_line_id: int
    quantity: Decimal
    rate: Optional[Decimal] = None
    gst_rate: Optional[Decimal] = None


class BillIn(Schema):
    order_id: int
    supplier_invoice_number: str
    invoice_date: date
    notes: str = ''
    lines: List[BillLineIn]


class BillLineOut(Schema):
    order_line_id: int
    item_code: str
    item_name: str
    quantity: Decimal
    unit: str
    rate: Decimal
    order_rate: Decimal
    rate_differs: bool
    gst_rate: Decimal
    taxable_value: Decimal
    tax_amount: Decimal


class BillActionsOut(Schema):
    edit: bool
    submit: bool
    approve: bool
    cancel: bool


class BillOut(TotalsOut):
    id: int
    number: str
    status: str
    status_label: str
    order_id: int
    order_number: str
    supplier: PartyOut
    supplier_invoice_number: str
    invoice_date: date
    due_date: Optional[date]
    notes: str
    rejection_reason: str
    created_by: str
    approved_by: str
    lines: List[BillLineOut]
    actions: BillActionsOut
    submit_needs_approval: bool
    # Money settled by payments (finance): paid by posted payments, pending in ones waiting for approval.
    paid: Decimal
    payment_pending: Decimal
    outstanding: Decimal
    payment_status: str
    payment_status_label: str


def _bill_out(bill, membership):
    S = Bill.Status
    writable = _writes_allowed(membership)
    can = lambda code: writable and _can(membership, code)  # noqa: E731
    lines = [{
        'order_line_id': l.order_line_id, 'item_code': l.item.code, 'item_name': l.item.name, 'quantity': l.quantity,
        'unit': l.order_line.unit, 'rate': l.rate, 'order_rate': l.order_line.rate, 'rate_differs': l.rate != l.order_line.rate,
        'gst_rate': l.gst_rate, 'taxable_value': l.taxable_value, 'tax_amount': l.tax_amount,
    } for l in bill.lines.select_related('item', 'order_line')]
    approve_code = 'purchase_bill.approve' if services.approvals_on(bill.company) else 'purchase_bill.create'
    return {
        **_totals(bill), 'id': bill.pk, 'number': bill.number, 'status': bill.status, 'status_label': bill.get_status_display(),
        'order_id': bill.order_id, 'order_number': bill.order.number, 'supplier': _party(bill.supplier),
        'supplier_invoice_number': bill.supplier_invoice_number, 'invoice_date': bill.invoice_date, 'due_date': bill.due_date,
        'notes': bill.notes, 'rejection_reason': bill.rejection_reason, 'created_by': _name(bill.created_by),
        'approved_by': _name(bill.approved_by), 'lines': lines,
        'actions': {
            'edit': bill.status == S.DRAFT and can('purchase_bill.create'),
            'submit': bill.status == S.DRAFT and can('purchase_bill.create'),
            'approve': bill.status == S.PENDING_APPROVAL and can('purchase_bill.approve'),
            'cancel': bill.status in (S.PENDING_APPROVAL, S.POSTED) and can(approve_code),
        },
        'submit_needs_approval': services.approvals_on(bill.company) and not _can(membership, 'purchase_bill.approve'),
        **_balance(bill),
    }


def _balance(bill):
    from finance.services import balance
    return balance(bill)


def _bill(membership, bill_id):
    return get_object_or_404(Bill.objects.select_related('supplier', 'order', 'company', 'created_by', 'approved_by'),
                             pk=bill_id, company=membership.company)


@router.get('/purchase-bills', response=List[BillOut])
def list_bills(request, status: str = ''):
    membership = viewer(request)
    bills = Bill.objects.filter(company=membership.company).select_related('supplier', 'order', 'company', 'created_by', 'approved_by')
    if status:
        bills = bills.filter(status=status)
    return [_bill_out(b, membership) for b in bills[:LIST_LIMIT]]


@router.get('/purchase-bills/{bill_id}', response=BillOut)
def get_bill(request, bill_id: int):
    membership = viewer(request)
    return _bill_out(_bill(membership, bill_id), membership)


@router.post('/purchase-bills', response=BillOut)
def create_bill(request, payload: BillIn):
    membership = membership_for(request, 'purchase_bill.create', write=True)
    order = _order(membership, payload.order_id)
    bill = _run(services.save_bill, membership, Bill(), order, payload.dict())
    return _bill_out(_bill(membership, bill.pk), membership)


@router.put('/purchase-bills/{bill_id}', response=BillOut)
def update_bill(request, bill_id: int, payload: BillIn):
    membership = membership_for(request, 'purchase_bill.create', write=True)
    bill = _bill(membership, bill_id)
    bill = _run(services.save_bill, membership, bill, bill.order, payload.dict())
    return _bill_out(_bill(membership, bill.pk), membership)


@router.delete('/purchase-bills/{bill_id}', response={204: None})
def delete_bill(request, bill_id: int):
    membership = membership_for(request, 'purchase_bill.create', write=True)
    _run(services.delete_bill, _bill(membership, bill_id))
    return 204, None


def _bill_action(request, bill_id, permission, action, *args):
    membership = membership_for(request, permission, write=True)
    bill = _run(action, membership, _bill(membership, bill_id), *args)
    return _bill_out(_bill(membership, bill.pk), membership)


@router.post('/purchase-bills/{bill_id}/submit', response=BillOut)
def submit_bill(request, bill_id: int):
    return _bill_action(request, bill_id, 'purchase_bill.create', services.submit_bill)


@router.post('/purchase-bills/{bill_id}/approve', response=BillOut)
def approve_bill(request, bill_id: int):
    return _bill_action(request, bill_id, 'purchase_bill.approve', services.approve_bill)


@router.post('/purchase-bills/{bill_id}/reject', response=BillOut)
def reject_bill(request, bill_id: int, payload: ReasonIn):
    return _bill_action(request, bill_id, 'purchase_bill.approve', services.reject_bill, payload.reason)


@router.post('/purchase-bills/{bill_id}/cancel', response=BillOut)
def cancel_bill(request, bill_id: int):
    membership = membership_for(request, write=True)
    bill = _bill(membership, bill_id)
    code = 'purchase_bill.approve' if services.approvals_on(membership.company) else 'purchase_bill.create'
    if not _can(membership, code):
        raise HttpError(403, "Your role can't cancel bills.")
    bill = _run(services.cancel_bill, membership, bill)
    return _bill_out(_bill(membership, bill.pk), membership)
