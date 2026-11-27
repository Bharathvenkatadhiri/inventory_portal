"""Every change to items, warehouses and stock goes through here. Callers
(inventory.api) have already checked the person's permission and that the
subscription allows changes; this applies the business rules and raises
InventoryError with a message to show when something isn't allowed."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, F, ProtectedError, Sum, Value
from django.db.models.functions import Coalesce

from plans import erp

from .models import Item, StockEntry, Warehouse


class InventoryError(Exception):
    """A change that isn't allowed; the message says why."""


def _messages(exc):
    if hasattr(exc, 'message_dict'):
        return ' '.join(m for msgs in exc.message_dict.values() for m in msgs)
    return ' '.join(exc.messages)


# --- Items ------------------------------------------------------------------------

ITEM_FIELDS = ('code', 'name', 'kind', 'unit', 'hsn_code', 'gst_rate', 'purchase_price', 'sale_price',
               'reorder_level', 'description', 'is_active', 'inspection_required', 'tracking', 'shelf_life_days')


def save_item(company, user, item, data):
    """Creates (item without pk) or updates `item` from `data`."""
    from . import batches

    was_tracking = Item.objects.filter(pk=item.pk).values_list('tracking', flat=True).first() if item.pk else 'none'
    for field in ITEM_FIELDS:
        if field in data:
            value = data[field]
            if isinstance(value, str):
                value = value.strip()
            setattr(item, field, value)
    item.code = (item.code or '').upper()
    item.hsn_code = (item.hsn_code or '').replace(' ', '')
    if not item.code or not item.name:
        raise InventoryError("Enter the item's code and name.")
    if item.pk and item.kind == Item.Kind.SERVICE and item.stock_entries.exists():
        raise InventoryError("This item has stock, so it can't become a service.")
    item.tracking = item.tracking or 'none'
    if item.tracking not in batches.TRACKING:
        raise InventoryError("Choose how the item is tracked: not tracked, by batch or by serial number.")
    if item.kind == Item.Kind.SERVICE:
        item.tracking = 'none'
    if item.tracking != was_tracking:
        if item.tracking != 'none' and not batches.available(company):
            raise InventoryError("Batch and serial number tracking is on the Growth and Pro plans.")
        if item.pk and any(q for q in on_hand_by_item(company, item.pk).values()):
            raise InventoryError("This item has stock, so how it's tracked can't change. Bring its stock to zero first.")
    if item.tracking == 'none':
        item.shelf_life_days = None
    if item.shelf_life_days is not None and not 0 < item.shelf_life_days <= 36500:
        raise InventoryError("Enter the shelf life in days, e.g. 180.")
    if Item.objects.filter(company=company, code__iexact=item.code).exclude(pk=item.pk).exists():
        raise InventoryError(f"Another item already uses the code {item.code}.")
    item.company = company
    if item.pk is None:
        item.created_by = user
    try:
        item.full_clean(exclude=['company', 'created_by'])
    except ValidationError as exc:
        raise InventoryError(_messages(exc))
    try:
        with transaction.atomic():
            item.save()
    except IntegrityError:
        raise InventoryError(f"Another item already uses the code {item.code}.")
    return item


def delete_item(item):
    """Deletes an item nothing refers to yet; one with stock is marked
    inactive instead, so its history stays."""
    if item.stock_entries.exists():
        raise InventoryError("This item has stock entries, so it can't be deleted. Mark it inactive instead.")
    try:
        with transaction.atomic():
            item.delete()
    except ProtectedError:
        raise InventoryError("This item is on orders, bills, BOMs or other documents, so it can't be deleted. "
                             "Mark it inactive instead.")


# --- Warehouses -------------------------------------------------------------------

def warehouse_limit(company):
    return (erp.PLANS.get(company.erp_plan) or {}).get('warehouses', 0)


def warehouse_usage(company):
    """Active warehouses, the way the plan counts them (system QC locations don't count)."""
    return Warehouse.objects.filter(company=company, is_active=True, kind=Warehouse.Kind.STORE).count()


def store(company, warehouse_id, lock=False):
    """One of the company's own active stores (never a system QC location), or None."""
    warehouses = Warehouse.objects.filter(company=company, pk=warehouse_id, is_active=True, kind=Warehouse.Kind.STORE)
    if lock:
        warehouses = warehouses.select_for_update()
    return warehouses.first()


def system_location(company, kind):
    """A system location (under inspection, rejected goods, at job workers), made the first time it's needed."""
    warehouse = Warehouse.objects.filter(company=company, kind=kind).first()
    if warehouse is None:
        label = Warehouse.Kind(kind).label
        name = label if not Warehouse.objects.filter(company=company, name__iexact=label).exists() else f"{label} (QC)"
        codes = {Warehouse.Kind.QC_HOLD: 'QC', Warehouse.Kind.REJECTED: 'REJ', Warehouse.Kind.JOB_WORK: 'JW'}
        warehouse = Warehouse.objects.create(company=company, kind=kind, name=name, code=codes.get(kind, ''))
    return warehouse


def _check_room(company):
    limit = warehouse_limit(company)
    if limit is not None and warehouse_usage(company) >= limit:
        bigger = next((p for p in erp.PLAN_ORDER if (erp.PLANS[p]['warehouses'] or 0) > limit
                       or erp.PLANS[p]['warehouses'] is None), None)
        more = f" The {erp.PLANS[bigger]['label']} plan allows {erp.PLANS[bigger]['warehouses']}." if bigger else ''
        raise InventoryError(f"Your plan includes {limit} warehouse{'s' if limit != 1 else ''}.{more}")


@transaction.atomic
def save_warehouse(company, warehouse, data):
    """Creates or updates a warehouse. The first one becomes the default;
    making another the default moves it there."""
    from organizations.models import Address

    if warehouse.pk and warehouse.kind != Warehouse.Kind.STORE:
        raise InventoryError("This is a system location for QC; it can't be changed.")
    creating = warehouse.pk is None
    reactivating = not creating and data.get('is_active') is True and not warehouse.is_active
    if creating or reactivating:
        _check_room(company)
    if 'name' in data:
        warehouse.name = (data['name'] or '').strip()
    if 'code' in data:
        warehouse.code = (data['code'] or '').strip().upper()
    if 'is_active' in data:
        warehouse.is_active = bool(data['is_active'])
    if 'address_id' in data:
        address_id = data['address_id']
        warehouse.address = Address.objects.filter(company=company, pk=address_id).first() if address_id else None
        if address_id and warehouse.address is None:
            raise InventoryError("Choose one of your company's addresses.")
    if not warehouse.name:
        raise InventoryError("Enter the warehouse's name.")
    if Warehouse.objects.filter(company=company, name__iexact=warehouse.name).exclude(pk=warehouse.pk).exists():
        raise InventoryError(f"You already have a warehouse called {warehouse.name}.")
    warehouse.company = company

    if not warehouse.is_active and warehouse.is_default:
        raise InventoryError("This is your default warehouse. Make another one the default first.")
    if data.get('is_default') is False and warehouse.is_default:
        raise InventoryError("Make another warehouse the default instead.")
    others = Warehouse.objects.select_for_update().filter(company=company, kind=Warehouse.Kind.STORE).exclude(pk=warehouse.pk)
    make_default = data.get('is_default') is True or not others.filter(is_default=True, is_active=True).exists()
    if make_default and not warehouse.is_active:
        raise InventoryError("An inactive warehouse can't be the default.")
    if make_default:
        others.filter(is_default=True).update(is_default=False)
        warehouse.is_default = True
    warehouse.save()
    return warehouse


def delete_warehouse(warehouse):
    if warehouse.kind != Warehouse.Kind.STORE:
        raise InventoryError("This is a system location for QC; it can't be deleted.")
    if warehouse.stock_entries.exists():
        raise InventoryError("This warehouse has stock entries, so it can't be deleted. Mark it inactive instead.")
    others = Warehouse.objects.filter(company=warehouse.company, kind=Warehouse.Kind.STORE).exclude(pk=warehouse.pk)
    if warehouse.is_default and others.exists():
        raise InventoryError("This is your default warehouse. Make another one the default first.")
    try:
        with transaction.atomic():
            warehouse.delete()
    except ProtectedError:
        raise InventoryError("This warehouse is on orders, dispatches or other documents, so it can't be deleted. "
                             "Mark it inactive instead.")


# --- Stock ------------------------------------------------------------------------

@transaction.atomic
def set_opening_stock(company, user, *, item_id, warehouse_id, quantity, rate, entry_date=None, note='',
                      batch_number='', supplier_batch='', manufactured_on=None, expires_on=None, serials=None):
    """Sets (or replaces) an item's opening stock in a warehouse. A
    quantity of 0 removes it. Returns the entry, or None if removed.

    A batch-tracked item has opening stock per batch (`batch_number`); a
    serial-tracked one is given its serial numbers (or they're numbered
    from the item code), which replace any it had in that warehouse."""
    from . import batches

    item = Item.objects.filter(company=company, pk=item_id).first()
    warehouse = Warehouse.objects.filter(company=company, pk=warehouse_id, kind=Warehouse.Kind.STORE).first()
    if item is None or warehouse is None:
        raise InventoryError("Choose one of your items and warehouses.")
    if not item.tracks_stock:
        raise InventoryError(f"{item.name} is a service, so it has no stock.")
    serials = [n for n in (serials or []) if str(n).strip()]
    if item.by_serial and serials:
        quantity = len(serials)
    quantity, rate = Decimal(quantity), Decimal(rate or 0)
    if quantity < 0 or rate < 0:
        raise InventoryError("Quantity and rate can't be negative.")
    openings = StockEntry.objects.select_for_update(of=('self',)).filter(item=item, warehouse=warehouse, kind=StockEntry.Kind.OPENING)
    batch = None
    if item.by_batch:
        batch = batches.find(item, batch_number)
        if not (batch_number or '').strip():
            raise InventoryError(f"{item.name} is tracked by batch: enter the batch number.")
        openings = openings.filter(batch=batch) if batch else openings.none()
    elif not item.by_serial:
        openings = openings.filter(batch__isnull=True)
    existing = list(openings.select_related('batch'))
    for entry in existing:
        if entry.batch_id and entry.batch.entries.exclude(pk=entry.pk).exists():
            raise InventoryError(f"{item.name}: {entry.batch.number} has moved since, so its opening stock can't change.")
    if quantity == 0 or item.by_serial:
        for entry in existing:
            entry.delete()
            if entry.batch_id and not StockEntry.objects.filter(batch_id=entry.batch_id).exists():
                entry.batch.delete()
        existing = []
        if quantity == 0:
            return None
    if not warehouse.is_active:
        raise InventoryError(f"{warehouse.name} is inactive.")
    if not item.is_active:
        raise InventoryError(f"{item.name} is inactive.")
    day = entry_date or date.today()
    note = (note or '').strip()[:200]
    if item.by_serial:
        rows = batches.incoming(company, item, quantity, [{'number': n} for n in serials], origin='opening',
                                reference='Opening', day=day)
        entries = batches.write(company, item, warehouse, StockEntry.Kind.OPENING, rows, sign=1, rate=rate,
                                entry_date=day, reference='', note=note, user=user)
        return entries[0]
    if item.by_batch and batch is None:
        batch = batches.incoming(company, item, quantity, [{
            'number': batch_number, 'quantity': quantity, 'supplier_batch': supplier_batch,
            'manufactured_on': manufactured_on, 'expires_on': expires_on}], origin='opening', reference='Opening', day=day)[0][0]
    elif batch is not None and (expires_on or manufactured_on):
        batch.expires_on, batch.manufactured_on = expires_on or batch.expires_on, manufactured_on or batch.manufactured_on
        batch.save(update_fields=['expires_on', 'manufactured_on'])
    entry = existing[0] if existing else StockEntry(company=company, item=item, warehouse=warehouse,
                                                    kind=StockEntry.Kind.OPENING, created_by=user, batch=batch)
    entry.quantity, entry.rate = quantity, rate
    entry.entry_date = day
    entry.note = note
    entry.save()
    return entry


def balances(company, warehouse_id=None, item_id=None):
    """Stock on hand per item and warehouse, with its value at cost.
    Rows with nothing on hand are left out."""
    entries = StockEntry.objects.filter(company=company)
    if warehouse_id:
        entries = entries.filter(warehouse_id=warehouse_id)
    if item_id:
        entries = entries.filter(item_id=item_id)
    zero = Value(Decimal('0'), output_field=DecimalField(max_digits=20, decimal_places=3))
    # Annotation names differ from the fields, or F('quantity') would mean the sum.
    rows = (entries.values('item_id', 'warehouse_id')
            .annotate(on_hand=Coalesce(Sum('quantity'), zero),
                      at_cost=Coalesce(Sum(F('quantity') * F('rate'), output_field=DecimalField(max_digits=24, decimal_places=2)), zero))
            .order_by('item__code', 'warehouse__name'))
    return [{'item_id': r['item_id'], 'warehouse_id': r['warehouse_id'], 'quantity': r['on_hand'], 'value': r['at_cost']}
            for r in rows if r['on_hand'] != 0]


def on_hand(company, item, warehouse):
    """(quantity on hand, value at cost) of one item in one warehouse."""
    totals = StockEntry.objects.filter(company=company, item=item, warehouse=warehouse).aggregate(
        on_hand=Sum('quantity'),
        at_cost=Sum(F('quantity') * F('rate'), output_field=DecimalField(max_digits=24, decimal_places=2)),
    )
    return totals['on_hand'] or Decimal('0'), totals['at_cost'] or Decimal('0')


def average_cost(company, item, warehouse):
    """Moving average cost per unit in a warehouse; stock going out is
    valued at this, so the ledger's value stays right."""
    quantity, value = on_hand(company, item, warehouse)
    if quantity <= 0:
        return item.purchase_price or Decimal('0')
    return (value / quantity).quantize(Decimal('0.01'))


def on_hand_by_item(company, item_id=None):
    """Total stock on hand per item across warehouses: {item_id: quantity}."""
    entries = StockEntry.objects.filter(company=company)
    if item_id:
        entries = entries.filter(item_id=item_id)
    rows = entries.values('item_id').annotate(quantity=Sum('quantity'))
    return {r['item_id']: r['quantity'] for r in rows}
