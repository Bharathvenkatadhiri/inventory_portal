"""/api/v1 endpoints for items, warehouses and stock, on the organization
the request is for (organizations.workspace). Any member can read items
and warehouses (every module picks from them); stock needs stock.view.
Changes need items.manage, warehouses.manage or stock.adjust, and a
subscription that isn't lapsed."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from organizations.app_api import membership_for

from . import batches, services
from .models import GST_RATES, UNITS, Batch, Item, StockEntry, Warehouse

router = Router(tags=['inventory'])
ITEM_LIST_LIMIT = 1000


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.InventoryError as exc:
        raise HttpError(400, str(exc))


# --- Options ----------------------------------------------------------------------

class ChoiceOut(Schema):
    value: str
    label: str


class InventoryOptionsOut(Schema):
    kinds: List[ChoiceOut]
    units: List[ChoiceOut]
    gst_rates: List[str]
    tracking: List[ChoiceOut]
    # Whether the plan includes batch/serial tracking.
    batches_available: bool


@router.get('/inventory/options', response=InventoryOptionsOut)
def inventory_options(request):
    """The choices item forms offer."""
    membership = membership_for(request)
    return {
        'kinds': [{'value': v, 'label': l} for v, l in Item.Kind.choices],
        'units': [{'value': v, 'label': f'{v} ({l})'} for v, l in UNITS],
        'gst_rates': GST_RATES,
        'tracking': [{'value': v, 'label': l} for v, l in Item._meta.get_field('tracking').choices],
        'batches_available': batches.available(membership.company),
    }


# --- Items ------------------------------------------------------------------------

class ItemIn(Schema):
    code: str
    name: str
    kind: str = Item.Kind.RAW_MATERIAL
    unit: str = 'NOS'
    hsn_code: str = ''
    gst_rate: Decimal = Decimal('18')
    purchase_price: Optional[Decimal] = None
    sale_price: Optional[Decimal] = None
    reorder_level: Optional[Decimal] = None
    description: str = ''
    is_active: bool = True
    inspection_required: bool = False
    tracking: str = 'none'
    shelf_life_days: Optional[int] = None


class ItemOut(Schema):
    id: int
    code: str
    name: str
    kind: str
    kind_label: str
    unit: str
    hsn_code: str
    gst_rate: Decimal
    purchase_price: Optional[Decimal]
    sale_price: Optional[Decimal]
    reorder_level: Optional[Decimal]
    description: str
    is_active: bool
    inspection_required: bool
    # 'none', 'batch' or 'serial'.
    tracking: str
    shelf_life_days: Optional[int]
    tracks_stock: bool
    # Total across warehouses; null when the person can't see stock.
    on_hand: Optional[Decimal]
    below_reorder: bool


def _item_out(item, on_hand=None):
    quantity = on_hand.get(item.pk, Decimal('0')) if on_hand is not None and item.tracks_stock else None
    return {
        'id': item.pk, 'code': item.code, 'name': item.name, 'kind': item.kind, 'kind_label': item.get_kind_display(),
        'unit': item.unit, 'hsn_code': item.hsn_code, 'gst_rate': item.gst_rate,
        'purchase_price': item.purchase_price, 'sale_price': item.sale_price, 'reorder_level': item.reorder_level,
        'description': item.description, 'is_active': item.is_active, 'inspection_required': item.inspection_required,
        'tracking': item.tracking if item.tracks_stock else 'none', 'shelf_life_days': item.shelf_life_days,
        'tracks_stock': item.tracks_stock,
        'on_hand': quantity,
        'below_reorder': quantity is not None and item.reorder_level is not None and quantity <= item.reorder_level,
    }


def _stock_visible(membership):
    from organizations import access
    return bool(access.check(membership, 'stock.view'))


@router.get('/items', response=List[ItemOut])
def list_items(request, q: str = '', kind: str = '', active: Optional[bool] = None):
    # `q`, not `search`: core.middleware.GlobalSearchMiddleware redirects any ?search= request.
    membership = membership_for(request)
    company = membership.company
    items = Item.objects.filter(company=company)
    q = q.strip()
    if q:
        items = items.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(hsn_code__startswith=q))
    if kind:
        items = items.filter(kind=kind)
    if active is not None:
        items = items.filter(is_active=active)
    on_hand = services.on_hand_by_item(company) if _stock_visible(membership) else None
    return [_item_out(i, on_hand) for i in items[:ITEM_LIST_LIMIT]]


@router.post('/items', response=ItemOut)
def create_item(request, payload: ItemIn):
    membership = membership_for(request, 'items.manage', write=True)
    item = _run(services.save_item, membership.company, request.user, Item(), payload.dict())
    return _item_out(item)


@router.put('/items/{item_id}', response=ItemOut)
def update_item(request, item_id: int, payload: ItemIn):
    membership = membership_for(request, 'items.manage', write=True)
    item = get_object_or_404(Item, pk=item_id, company=membership.company)
    item = _run(services.save_item, membership.company, request.user, item, payload.dict())
    on_hand = services.on_hand_by_item(membership.company) if _stock_visible(membership) else None
    return _item_out(item, on_hand)


@router.delete('/items/{item_id}', response={204: None})
def delete_item(request, item_id: int):
    membership = membership_for(request, 'items.manage', write=True)
    _run(services.delete_item, get_object_or_404(Item, pk=item_id, company=membership.company))
    return 204, None


# --- Warehouses -------------------------------------------------------------------

class WarehouseIn(Schema):
    name: str
    code: str = ''
    address_id: Optional[int] = None
    is_default: Optional[bool] = None
    is_active: bool = True


class WarehouseOut(Schema):
    id: int
    name: str
    code: str
    address_id: Optional[int]
    address: str
    is_default: bool
    is_active: bool
    has_stock: bool
    # 'store', or a system QC location: 'qc_hold' (under inspection) or 'rejected'.
    kind: str


class WarehousesOut(Schema):
    warehouses: List[WarehouseOut]
    used: int
    included: Optional[int]


def _warehouse_out(w):
    address = ''
    if w.address_id:
        a = w.address
        address = ', '.join(p for p in (a.label, a.line1, a.city, a.pincode) if p)
    return {
        'id': w.pk, 'name': w.name, 'code': w.code, 'address_id': w.address_id, 'address': address,
        'is_default': w.is_default, 'is_active': w.is_active, 'has_stock': w.stock_entries.exists(), 'kind': w.kind,
    }


@router.get('/warehouses', response=WarehousesOut)
def list_warehouses(request, include_system: bool = False):
    """The company's stores; include_system adds the QC locations (for stock views)."""
    company = membership_for(request).company
    warehouses = Warehouse.objects.filter(company=company).select_related('address')
    if not include_system:
        warehouses = warehouses.filter(kind=Warehouse.Kind.STORE)
    return {
        'warehouses': [_warehouse_out(w) for w in warehouses],
        'used': services.warehouse_usage(company),
        'included': services.warehouse_limit(company),
    }


@router.post('/warehouses', response=WarehouseOut)
def create_warehouse(request, payload: WarehouseIn):
    company = membership_for(request, 'warehouses.manage', write=True).company
    return _warehouse_out(_run(services.save_warehouse, company, Warehouse(), payload.dict()))


@router.put('/warehouses/{warehouse_id}', response=WarehouseOut)
def update_warehouse(request, warehouse_id: int, payload: WarehouseIn):
    company = membership_for(request, 'warehouses.manage', write=True).company
    warehouse = get_object_or_404(Warehouse, pk=warehouse_id, company=company)
    return _warehouse_out(_run(services.save_warehouse, company, warehouse, payload.dict()))


@router.delete('/warehouses/{warehouse_id}', response={204: None})
def delete_warehouse(request, warehouse_id: int):
    company = membership_for(request, 'warehouses.manage', write=True).company
    _run(services.delete_warehouse, get_object_or_404(Warehouse, pk=warehouse_id, company=company))
    return 204, None


# --- Stock ------------------------------------------------------------------------

class StockRowOut(Schema):
    item_id: int
    item_code: str
    item_name: str
    unit: str
    warehouse_id: int
    warehouse_name: str
    quantity: Decimal
    value: Decimal


@router.get('/stock', response=List[StockRowOut])
def stock(request, warehouse_id: Optional[int] = None, item_id: Optional[int] = None):
    company = membership_for(request, 'stock.view').company
    rows = services.balances(company, warehouse_id=warehouse_id, item_id=item_id)
    items = {i.pk: i for i in Item.objects.filter(company=company, pk__in={r['item_id'] for r in rows})}
    warehouses = {w.pk: w for w in Warehouse.objects.filter(company=company, pk__in={r['warehouse_id'] for r in rows})}
    return [{
        'item_id': r['item_id'], 'item_code': items[r['item_id']].code, 'item_name': items[r['item_id']].name,
        'unit': items[r['item_id']].unit, 'warehouse_id': r['warehouse_id'],
        'warehouse_name': warehouses[r['warehouse_id']].name, 'quantity': r['quantity'], 'value': r['value'],
    } for r in rows]


class OpeningIn(Schema):
    item_id: int
    warehouse_id: int
    quantity: Decimal
    rate: Decimal = Decimal('0')
    entry_date: Optional[date] = None
    note: str = ''
    # Batch-tracked items: which batch (opening stock is per batch).
    batch_number: str = ''
    supplier_batch: str = ''
    manufactured_on: Optional[date] = None
    expires_on: Optional[date] = None
    # Serial-tracked items: the units' numbers (left empty, they're numbered from the item code).
    serials: List[str] = []


class OpeningOut(Schema):
    id: int
    item_id: int
    item_code: str
    item_name: str
    unit: str
    warehouse_id: int
    warehouse_name: str
    quantity: Decimal
    rate: Decimal
    value: Decimal
    entry_date: date
    note: str
    batch_number: str
    expires_on: Optional[date]


def _opening_out(e):
    return {
        'id': e.pk, 'item_id': e.item_id, 'item_code': e.item.code, 'item_name': e.item.name, 'unit': e.item.unit,
        'warehouse_id': e.warehouse_id, 'warehouse_name': e.warehouse.name, 'quantity': e.quantity, 'rate': e.rate,
        'value': e.value, 'entry_date': e.entry_date, 'note': e.note,
        'batch_number': e.batch.number if e.batch_id else '', 'expires_on': e.batch.expires_on if e.batch_id else None,
    }


@router.get('/stock/opening', response=List[OpeningOut])
def list_opening(request):
    company = membership_for(request, 'stock.view').company
    entries = StockEntry.objects.filter(company=company, kind=StockEntry.Kind.OPENING).select_related('item', 'warehouse', 'batch')
    return [_opening_out(e) for e in entries.order_by('item__code', 'warehouse__name', 'batch__number')]


@router.put('/stock/opening', response={200: OpeningOut, 204: None})
def set_opening(request, payload: OpeningIn):
    """Sets an item's opening stock in a warehouse; a quantity of 0 removes it."""
    membership = membership_for(request, 'stock.adjust', write=True)
    entry = _run(services.set_opening_stock, membership.company, request.user, **payload.dict())
    if entry is None:
        return 204, None
    return 200, _opening_out(entry)


# --- Batches and serial numbers ---------------------------------------------------------------

class BatchIn(Schema):
    """A batch (or serial number) coming in on a document. Leave the
    quantity out for a serial number, or when one batch takes it all."""
    number: str = ''
    quantity: Optional[Decimal] = None
    supplier_batch: str = ''
    manufactured_on: Optional[date] = None
    expires_on: Optional[date] = None


class BatchPickIn(Schema):
    """A batch (or serial number) picked for stock going out; by id or number."""
    batch_id: Optional[int] = None
    number: str = ''
    quantity: Optional[Decimal] = None


class BatchQtyOut(Schema):
    batch_id: int
    number: str
    quantity: Decimal
    expires_on: Optional[date]


def batch_rows(rows):
    """BatchQtyOut for [(batch, quantity)] from inventory.batches."""
    return [{'batch_id': b.pk, 'number': b.number, 'quantity': q, 'expires_on': b.expires_on} for b, q in rows or [] if b]


class BatchStockOut(Schema):
    warehouse_id: int
    warehouse_name: str
    warehouse_kind: str
    quantity: Decimal


class BatchOut(Schema):
    id: int
    number: str
    item_id: int
    item_code: str
    item_name: str
    unit: str
    tracking: str
    supplier_batch: str
    manufactured_on: Optional[date]
    expires_on: Optional[date]
    expired: bool
    origin: str
    origin_label: str
    reference: str
    supplier_name: str
    on_hand: Decimal
    stock: List[BatchStockOut]
    # What its barcode label encodes.
    label_code: str


def _batches_out(company, batch_list):
    batch_list = list(batch_list)
    stock = defaultdict(list)
    warehouses = {w.pk: w for w in Warehouse.objects.filter(company=company)}
    rows = (StockEntry.objects.filter(company=company, batch__in=batch_list).values('batch_id', 'warehouse_id')
            .annotate(q=Sum('quantity')))
    for r in rows:
        if r['q']:
            stock[r['batch_id']].append(r)
    today = timezone.localdate()
    return [{
        'id': b.pk, 'number': b.number, 'item_id': b.item_id, 'item_code': b.item.code, 'item_name': b.item.name,
        'unit': b.item.unit, 'tracking': b.item.tracking, 'supplier_batch': b.supplier_batch,
        'manufactured_on': b.manufactured_on, 'expires_on': b.expires_on,
        'expired': bool(b.expires_on and b.expires_on < today), 'origin': b.origin, 'origin_label': b.get_origin_display(),
        'reference': b.reference, 'supplier_name': (b.supplier.name or b.supplier.legal_name) if b.supplier_id else '',
        'on_hand': sum((r['q'] for r in stock[b.pk]), Decimal('0')),
        'stock': [{'warehouse_id': r['warehouse_id'], 'warehouse_name': warehouses[r['warehouse_id']].name,
                   'warehouse_kind': warehouses[r['warehouse_id']].kind, 'quantity': r['q']}
                  for r in sorted(stock[b.pk], key=lambda r: warehouses[r['warehouse_id']].name)],
        'label_code': b.label_code,
    } for b in batch_list]


BATCH_LIST_LIMIT = 500


@router.get('/batches', response=List[BatchOut])
def list_batches(request, item_id: Optional[int] = None, warehouse_id: Optional[int] = None, q: str = '',
                 in_stock: bool = True, expiring_within: Optional[int] = None):
    """Batches and serial numbers, the first to expire first. in_stock
    leaves out ones with nothing on hand (in `warehouse_id`, when given);
    expiring_within (days) keeps only those expiring by then, expired included."""
    company = membership_for(request, 'stock.view').company
    found = Batch.objects.filter(company=company).select_related('item', 'supplier')
    if item_id:
        found = found.filter(item_id=item_id)
    q = q.strip()
    if q:
        found = found.filter(Q(number__icontains=q) | Q(supplier_batch__icontains=q) | Q(item__code__icontains=q)
                             | Q(item__name__icontains=q))
    if expiring_within is not None:
        found = found.filter(expires_on__lte=timezone.localdate() + timedelta(days=max(0, expiring_within)))
    if in_stock or warehouse_id:
        entries = StockEntry.objects.filter(company=company, batch__isnull=False)
        if warehouse_id:
            entries = entries.filter(warehouse_id=warehouse_id)
        held = {r['batch_id'] for r in entries.values('batch_id').annotate(q=Sum('quantity')) if r['q'] > 0}
        found = found.filter(pk__in=held)
    found = found.order_by('item__code', 'expires_on', 'created_at', 'id')
    return _batches_out(company, found[:BATCH_LIST_LIMIT])


@router.get('/batches/lookup', response=List[BatchOut])
def lookup_batch(request, code: str):
    """What a scanned label (or a typed batch, heat or serial number) is."""
    company = membership_for(request, 'stock.view').company
    code = code.strip()
    found = Batch.objects.none()
    if code[:1] in ('B', 'b') and code[1:].isdigit():
        found = Batch.objects.filter(company=company, pk=int(code[1:]))
    if not found.exists():
        found = Batch.objects.filter(company=company).filter(Q(number__iexact=code) | Q(supplier_batch__iexact=code))
    return _batches_out(company, found.select_related('item', 'supplier')[:20])


class BatchMovementOut(Schema):
    entry_date: date
    kind: str
    kind_label: str
    warehouse_name: str
    quantity: Decimal
    reference: str
    note: str


class BatchDetailOut(Schema):
    batch: BatchOut
    movements: List[BatchMovementOut]
    made_from: List[BatchOut]
    made_into: List[BatchOut]


@router.get('/batches/{batch_id}', response=BatchDetailOut)
def batch_detail(request, batch_id: int):
    """A batch's movements, and its trace: what it was made from and into."""
    company = membership_for(request, 'stock.view').company
    batch = get_object_or_404(Batch.objects.select_related('item', 'supplier'), pk=batch_id, company=company)
    entries = batch.entries.select_related('warehouse').order_by('entry_date', 'id')
    traced = batches.trace(batch)
    return {
        'batch': _batches_out(company, [batch])[0],
        'movements': [{'entry_date': e.entry_date, 'kind': e.kind, 'kind_label': e.get_kind_display(),
                       'warehouse_name': e.warehouse.name, 'quantity': e.quantity, 'reference': e.reference, 'note': e.note}
                      for e in entries],
        'made_from': _batches_out(company, traced['made_from']),
        'made_into': _batches_out(company, traced['made_into']),
    }


@router.get('/batch-labels/pdf')
def batch_labels(request, ids: str, copies: int = 1):
    """A sheet of barcode labels for batches or serial numbers (ids comma-separated)."""
    from printouts.api import _pdf
    from printouts.render import batch_labels as render

    company = membership_for(request, 'stock.view').company
    wanted = [int(i) for i in ids.split(',') if i.strip().isdigit()][:500]
    found = {b.pk: b for b in Batch.objects.filter(company=company, pk__in=wanted).select_related('item')}
    if not found:
        raise HttpError(404, "Choose batches or serial numbers to print.")
    return _pdf(render(company, [found[i] for i in wanted if i in found], copies), 'labels.pdf')
