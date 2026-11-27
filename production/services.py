"""Bills of materials and production entries. Callers (production.api)
check the permission; this checks items, quantities and stock, and raises
ProductionError with a message to show.

Posting an entry (all in one transaction, warehouses locked):
- each component leaves the source warehouse at its moving average cost;
  never more than is on hand;
- the good units enter the target warehouse at material cost / good
  units, so rejected units' materials are part of the good units' cost.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from inventory import services as inventory
from inventory.models import Item, StockEntry, Warehouse
from purchase.services import _decimal, next_number

from .models import BillOfMaterials, BomComponent, ProductionConsumption, ProductionEntry

ZERO, CENT = Decimal('0'), Decimal('0.01')
QTY = Decimal('0.001')
OUTPUT_KINDS = {Item.Kind.FINISHED_GOOD, Item.Kind.SEMI_FINISHED, Item.Kind.COMPONENT}
MAX_COMPONENTS = 100


class ProductionError(Exception):
    """A change that isn't allowed; the message says why."""


# --- Bills of materials ----------------------------------------------------------------------

@transaction.atomic
def save_bom(membership, bom, data):
    company = membership.company
    item = Item.objects.filter(company=company, pk=data.get('item_id')).first()
    if item is None:
        raise ProductionError("Choose the item this BOM makes.")
    if item.kind not in OUTPUT_KINDS or not item.tracks_stock:
        raise ProductionError(f"{item.name} is a {item.get_kind_display().lower()}; a BOM makes a finished good, "
                              "semi-finished item or part.")
    if bom.pk and bom.entries.exists() and bom.item_id != item.pk:
        raise ProductionError("Production has been recorded against this BOM, so its item can't change.")
    name = (data.get('name') or '').strip()[:80] or 'Standard'
    output = _decimal(data.get('output_quantity') or 1, 'the quantity it makes')
    if output <= 0:
        raise ProductionError("The quantity a BOM makes must be more than 0.")

    rows = data.get('components') or []
    if not rows:
        raise ProductionError("Add at least one component.")
    if len(rows) > MAX_COMPONENTS:
        raise ProductionError(f"A BOM can have up to {MAX_COMPONENTS} components.")
    items = {i.pk: i for i in Item.objects.filter(company=company, pk__in=[r.get('item_id') for r in rows])}
    components, seen = [], set()
    for position, row in enumerate(rows, start=1):
        part = items.get(row.get('item_id'))
        if part is None:
            raise ProductionError(f"Component {position}: choose one of your items.")
        if part.pk == item.pk:
            raise ProductionError(f"{item.name} can't be a component of itself.")
        if not part.tracks_stock:
            raise ProductionError(f"{part.name} is a service; components come out of stock.")
        if not part.is_active:
            raise ProductionError(f"{part.name} is inactive.")
        if part.pk in seen:
            raise ProductionError(f"{part.name} is listed twice; put the total on one line.")
        seen.add(part.pk)
        quantity = _decimal(row.get('quantity'), 'a quantity')
        if quantity <= 0:
            raise ProductionError(f"{part.name}: the quantity must be more than 0.")
        components.append(BomComponent(item=part, quantity=quantity, position=position))

    bom.company, bom.item, bom.name, bom.output_quantity = company, item, name, output
    bom.notes = (data.get('notes') or '').strip()
    if 'is_active' in data and data['is_active'] is not None:
        bom.is_active = bool(data['is_active'])
    if bom.pk is None:
        bom.created_by = membership.user
    others = BillOfMaterials.objects.select_for_update().filter(item=item).exclude(pk=bom.pk)
    if data.get('is_default') or not others.filter(is_default=True).exists():
        others.filter(is_default=True).update(is_default=False)
        bom.is_default = True
    if not bom.is_active and bom.is_default and others.filter(is_active=True).exists():
        raise ProductionError("Make another BOM the default before making this one inactive.")
    try:
        with transaction.atomic():
            bom.save()
    except IntegrityError:
        raise ProductionError(f"{item.name} already has a BOM called {name}.")
    bom.components.all().delete()
    for c in components:
        c.bom = bom
    BomComponent.objects.bulk_create(components)
    return bom


@transaction.atomic
def delete_bom(bom):
    if bom.entries.exists():
        raise ProductionError("Production has been recorded against this BOM. Make it inactive instead.")
    bom.delete()


# --- Planning an entry --------------------------------------------------------------------------

def plan(bom, quantity_made, warehouse):
    """What `quantity_made` units (good + rejected) need, against what the
    warehouse holds: [{item, planned, on_hand, short}]."""
    factor = Decimal(quantity_made) / bom.output_quantity
    out = []
    for c in bom.components.select_related('item'):
        planned = (c.quantity * factor).quantize(QTY, rounding=ROUND_HALF_UP)
        on_hand = inventory.on_hand(bom.company, c.item, warehouse)[0] if warehouse else ZERO
        out.append({'item': c.item, 'planned': planned, 'on_hand': on_hand, 'short': max(ZERO, planned - on_hand)})
    return out


# --- Recording production ---------------------------------------------------------------------

@transaction.atomic
def record(membership, data):
    company = membership.company
    bom = BillOfMaterials.objects.filter(company=company, pk=data.get('bom_id')).select_related('item').first()
    if bom is None:
        raise ProductionError("Choose what was made (its BOM).")
    if not bom.is_active:
        raise ProductionError("That BOM is inactive.")
    good = _decimal(data.get('quantity_produced') or 0, 'the good quantity')
    rejected = _decimal(data.get('quantity_rejected') or 0, 'the rejected quantity')
    if good < 0 or rejected < 0:
        raise ProductionError("Quantities can't be negative.")
    if good + rejected <= 0:
        raise ProductionError("Enter how many were made.")
    entry_date = data.get('entry_date') or timezone.localdate()
    if entry_date > timezone.localdate():
        raise ProductionError("The date can't be in the future.")

    def warehouse(key, what):
        w = Warehouse.objects.select_for_update().filter(company=company, pk=data.get(key), is_active=True).first()
        if w is None:
            raise ProductionError(f"Choose the warehouse {what}.")
        return w
    source = warehouse('source_warehouse_id', 'the materials come from')
    target = warehouse('target_warehouse_id', 'the finished items go to') if data.get('target_warehouse_id') else source

    overrides = {row['item_id']: _decimal(row.get('quantity'), 'a quantity used') for row in data.get('consumption') or []}
    lines, material_cost = [], ZERO
    planned_items = set()
    for row in plan(bom, good + rejected, source):
        item = row['item']
        planned_items.add(item.pk)
        used = overrides.get(item.pk, row['planned'])
        if used < 0:
            raise ProductionError(f"{item.name}: the quantity used can't be negative.")
        if used > row['on_hand']:
            raise ProductionError(f"{item.name}: needs {used.normalize():f} {item.unit} but {source.name} has "
                                  f"{row['on_hand'].normalize():f}.")
        rate = inventory.average_cost(company, item, source)
        lines.append((item, row['planned'], used, rate))
        material_cost += (used * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    unknown = set(overrides) - planned_items
    if unknown:
        raise ProductionError("Consumption can only be for this BOM's components.")

    entry = ProductionEntry.objects.create(
        company=company, number=next_number(company, 'erp_pe', 'PE', entry_date), bom=bom, item=bom.item,
        entry_date=entry_date, quantity_produced=good, quantity_rejected=rejected, source_warehouse=source,
        target_warehouse=target, material_cost=material_cost,
        unit_cost=(material_cost / good).quantize(CENT, rounding=ROUND_HALF_UP) if good else ZERO,
        shift=(data.get('shift') or '').strip()[:20], notes=(data.get('notes') or '').strip()[:300], created_by=membership.user,
    )
    for item, planned, used, rate in lines:
        ProductionConsumption.objects.create(entry=entry, item=item, planned_quantity=planned, quantity=used, rate=rate)
        if used:
            StockEntry.objects.create(company=company, item=item, warehouse=source, kind=StockEntry.Kind.PRODUCTION_ISSUE,
                                      quantity=-used, rate=rate, entry_date=entry_date, reference=entry.number,
                                      note=f"For {good.normalize():f} {bom.item.unit} {bom.item.code}"[:200], created_by=membership.user)
    if good:
        StockEntry.objects.create(company=company, item=bom.item, warehouse=target, kind=StockEntry.Kind.PRODUCTION_RECEIPT,
                                  quantity=good, rate=entry.unit_cost, entry_date=entry_date, reference=entry.number,
                                  note=f"BOM {bom.name}" + (f", {rejected.normalize():f} rejected" if rejected else ''),
                                  created_by=membership.user)
    return entry


@transaction.atomic
def cancel(membership, entry, reason):
    """Reverses the stock: components back in, good units out (only while
    they're still in the target warehouse)."""
    reason = (reason or '').strip()
    if not reason:
        raise ProductionError("Say why it's being cancelled.")
    entry = ProductionEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != ProductionEntry.Status.POSTED:
        raise ProductionError("This entry is already cancelled.")
    target = Warehouse.objects.select_for_update().get(pk=entry.target_warehouse_id)
    if entry.quantity_produced:
        on_hand = inventory.on_hand(entry.company, entry.item, target)[0]
        if on_hand < entry.quantity_produced:
            raise ProductionError(f"Only {on_hand.normalize():f} {entry.item.unit} of {entry.item.name} is left in {target.name}; "
                                  "the rest has been used or sent, so this can't be undone.")
    for stock in StockEntry.objects.filter(company=entry.company, reference=entry.number,
                                           kind__in=[StockEntry.Kind.PRODUCTION_ISSUE, StockEntry.Kind.PRODUCTION_RECEIPT]):
        StockEntry.objects.create(company=entry.company, item=stock.item, warehouse=stock.warehouse, kind=stock.kind,
                                  quantity=-stock.quantity, rate=stock.rate, entry_date=timezone.localdate(),
                                  reference=entry.number, note=f"Cancelled: {reason}"[:200], created_by=membership.user)
    entry.status = ProductionEntry.Status.CANCELLED
    entry.cancel_reason, entry.cancelled_at = reason[:300], timezone.now()
    entry.save()
    return entry
