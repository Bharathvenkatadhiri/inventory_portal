"""/api/v1 endpoints for items, warehouses and stock, on the organization
the request is for (organizations.workspace). Any member can read items
and warehouses (every module picks from them); stock needs stock.view.
Changes need items.manage, warehouses.manage or stock.adjust, and a
subscription that isn't lapsed."""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from organizations.app_api import membership_for

from . import services
from .models import GST_RATES, UNITS, Item, StockEntry, Warehouse

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


@router.get('/inventory/options', response=InventoryOptionsOut)
def inventory_options(request):
    """The choices item forms offer."""
    membership_for(request)
    return {
        'kinds': [{'value': v, 'label': l} for v, l in Item.Kind.choices],
        'units': [{'value': v, 'label': f'{v} ({l})'} for v, l in UNITS],
        'gst_rates': GST_RATES,
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
        'description': item.description, 'is_active': item.is_active, 'tracks_stock': item.tracks_stock,
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
        'is_default': w.is_default, 'is_active': w.is_active, 'has_stock': w.stock_entries.exists(),
    }


@router.get('/warehouses', response=WarehousesOut)
def list_warehouses(request):
    company = membership_for(request).company
    warehouses = Warehouse.objects.filter(company=company).select_related('address')
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


def _opening_out(e):
    return {
        'id': e.pk, 'item_id': e.item_id, 'item_code': e.item.code, 'item_name': e.item.name, 'unit': e.item.unit,
        'warehouse_id': e.warehouse_id, 'warehouse_name': e.warehouse.name, 'quantity': e.quantity, 'rate': e.rate,
        'value': e.value, 'entry_date': e.entry_date, 'note': e.note,
    }


@router.get('/stock/opening', response=List[OpeningOut])
def list_opening(request):
    company = membership_for(request, 'stock.view').company
    entries = StockEntry.objects.filter(company=company, kind=StockEntry.Kind.OPENING).select_related('item', 'warehouse')
    return [_opening_out(e) for e in entries.order_by('item__code', 'warehouse__name')]


@router.put('/stock/opening', response={200: OpeningOut, 204: None})
def set_opening(request, payload: OpeningIn):
    """Sets an item's opening stock in a warehouse; a quantity of 0 removes it."""
    membership = membership_for(request, 'stock.adjust', write=True)
    entry = _run(services.set_opening_stock, membership.company, request.user, **payload.dict())
    if entry is None:
        return 204, None
    return 200, _opening_out(entry)
