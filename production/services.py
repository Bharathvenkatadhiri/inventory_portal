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

from inventory import batches
from inventory import services as inventory
from inventory.models import Item, StockEntry, Warehouse
from purchase.services import _decimal, next_number

from . import costing
from .models import BillOfMaterials, BomComponent, ProductionConsumption, ProductionEntry, ProductionScrap, WorkOrder

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

    for c in components:
        path = _path_to(c.item, item.pk)
        if path:
            raise ProductionError(f"That would make a loop: {item.name} needs {' → '.join(i.name for i in path)}, "
                                  f"which needs {item.name}.")
    bom.company, bom.item, bom.name, bom.output_quantity = company, item, name, output
    bom.notes = (data.get('notes') or '').strip()
    try:
        costing.apply_routing(company, bom, data)
    except costing.CostingError as exc:
        raise ProductionError(str(exc))
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
    work_order = None
    if data.get('work_order_id'):
        work_order = WorkOrder.objects.select_for_update().filter(company=company, pk=data['work_order_id']).first()
        if work_order is None:
            raise ProductionError("Choose one of your work orders.")
        if work_order.status not in (WorkOrder.Status.RELEASED, WorkOrder.Status.IN_PROGRESS):
            raise ProductionError(f"This work order is {work_order.get_status_display().lower()}; release it first.")
        data = {**data, 'bom_id': work_order.bom_id,
                'source_warehouse_id': data.get('source_warehouse_id') or work_order.source_warehouse_id,
                'target_warehouse_id': data.get('target_warehouse_id') or work_order.target_warehouse_id}
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
        w = inventory.store(company, data.get(key), lock=True)
        if w is None:
            raise ProductionError(f"Choose the warehouse {what}.")
        return w
    source = warehouse('source_warehouse_id', 'the materials come from')
    target = warehouse('target_warehouse_id', 'the finished items go to') if data.get('target_warehouse_id') else source

    consumption = data.get('consumption') or []
    overrides = {row['item_id']: _decimal(row.get('quantity'), 'a quantity used') for row in consumption
                 if row.get('quantity') not in (None, '')}
    picks = {row['item_id']: row.get('batches') or [] for row in consumption}
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
    unknown = (set(overrides) | {k for k, v in picks.items() if v}) - planned_items
    if unknown:
        raise ProductionError("Consumption can only be for this BOM's components.")
    try:
        costs = costing.conversion(company, bom, good + rejected, data)
    except costing.CostingError as exc:
        raise ProductionError(str(exc))
    scrap_value = sum((costing.money(q * r) for _i, q, r in costs['scrap']), ZERO)
    total = material_cost + costs['labour_cost'] + costs['machine_cost'] + costs['overhead_cost'] - scrap_value
    if total < 0:
        raise ProductionError("The scrap is worth more than everything that went in; check its quantity and rate.")

    entry = ProductionEntry.objects.create(
        company=company, number=next_number(company, 'erp_pe', 'PE', entry_date), bom=bom, item=bom.item,
        entry_date=entry_date, quantity_produced=good, quantity_rejected=rejected, source_warehouse=source,
        target_warehouse=target, material_cost=material_cost,
        unit_cost=(total / good).quantize(CENT, rounding=ROUND_HALF_UP) if good else ZERO,
        machine=costs['machine'], machine_hours=costs['machine_hours'], labour_hours=costs['labour_hours'],
        labour_cost=costs['labour_cost'], machine_cost=costs['machine_cost'], overhead_cost=costs['overhead_cost'],
        scrap_value=scrap_value,
        shift=(data.get('shift') or '').strip()[:20], notes=(data.get('notes') or '').strip()[:300], created_by=membership.user,
        work_order=work_order,
    )
    for item, planned, used, rate in lines:
        ProductionConsumption.objects.create(entry=entry, item=item, planned_quantity=planned, quantity=used, rate=rate)
        if used:
            rows = batches.outgoing(company, item, source, used, picks.get(item.pk), day=entry_date)
            batches.write(company, item, source, StockEntry.Kind.PRODUCTION_ISSUE, rows, sign=-1, rate=rate,
                          entry_date=entry_date, reference=entry.number,
                          note=f"For {good.normalize():f} {bom.item.unit} {bom.item.code}", user=membership.user)
    for item, quantity, rate in costs['scrap']:
        ProductionScrap.objects.create(entry=entry, item=item, quantity=quantity, rate=rate)
        rows = batches.incoming(company, item, quantity, origin='production', reference=entry.number, day=entry_date)
        batches.write(company, item, target, StockEntry.Kind.SCRAP, rows, sign=1, rate=rate, entry_date=entry_date,
                      reference=entry.number, note=f"Scrap from {bom.item.code}", user=membership.user)
    if good:
        from quality import services as quality
        inspect = quality.needs_inspection(company, bom.item)
        # A new batch (numbered from the entry unless given), or the next serial numbers.
        rows = batches.incoming(company, bom.item, good, data.get('output_batches'), origin='production',
                                reference=entry.number, day=entry_date)
        batches.write(company, bom.item, quality.hold(company) if inspect else target, StockEntry.Kind.PRODUCTION_RECEIPT,
                      rows, sign=1, rate=entry.unit_cost, entry_date=entry_date, reference=entry.number,
                      note=f"BOM {bom.name}" + (f", {rejected.normalize():f} rejected" if rejected else ''),
                      user=membership.user)
        if inspect:
            quality.open_lots(company, item=bom.item, rows=rows, rate=entry.unit_cost, destination=target,
                              source='production', reference=entry.number, received_on=entry_date, production_entry=entry)
    if work_order is not None:
        _update_progress(work_order)
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
    from quality.services import QualityError, cancel_for_production
    try:
        cancel_for_production(entry)
    except QualityError as exc:
        raise ProductionError(str(exc))
    # The good units went into the target store, or under inspection when QC applies.
    received = StockEntry.objects.filter(company=entry.company, reference=entry.number,
                                         kind=StockEntry.Kind.PRODUCTION_RECEIPT).select_related('warehouse').first()
    target = Warehouse.objects.select_for_update().get(pk=received.warehouse_id if received else entry.target_warehouse_id)
    if entry.quantity_produced:
        on_hand = inventory.on_hand(entry.company, entry.item, target)[0]
        if on_hand < entry.quantity_produced:
            raise ProductionError(f"Only {on_hand.normalize():f} {entry.item.unit} of {entry.item.name} is left in {target.name}; "
                                  "the rest has been used or sent, so this can't be undone.")
    moved = list(StockEntry.objects.filter(company=entry.company, reference=entry.number,
                                           kind__in=[StockEntry.Kind.PRODUCTION_ISSUE, StockEntry.Kind.PRODUCTION_RECEIPT,
                                                     StockEntry.Kind.SCRAP])
                 .select_related('item', 'warehouse', 'batch'))
    for stock in moved:
        if stock.kind == StockEntry.Kind.SCRAP and inventory.on_hand(entry.company, stock.item, stock.warehouse)[0] < stock.quantity:
            raise ProductionError(f"The {stock.item.name} scrap from this entry has been sold or used, so it can't be undone.")
    try:
        batches.still_on_hand(moved)
    except batches.BatchError as exc:
        raise ProductionError(f"{exc} This can't be undone.")
    batches.reverse(moved, entry_date=timezone.localdate(), note=f"Cancelled: {reason}", user=membership.user)
    entry.status = ProductionEntry.Status.CANCELLED
    entry.cancel_reason, entry.cancelled_at = reason[:300], timezone.now()
    entry.save()
    if entry.work_order_id:
        _update_progress(WorkOrder.objects.select_for_update().get(pk=entry.work_order_id))
    return entry



# --- Multi-level BOMs ----------------------------------------------------------------------------

MAX_DEPTH = 10


def default_bom(item):
    return BillOfMaterials.objects.filter(item=item, is_default=True, is_active=True).first()


def _path_to(item, target_id, depth=0):
    """Items from `item` down its default BOMs to `target_id`, or [] if it isn't there."""
    if item.pk == target_id:
        return [item]
    if depth >= MAX_DEPTH:
        return []
    bom = default_bom(item)
    if bom is None:
        return []
    for c in bom.components.select_related('item'):
        path = _path_to(c.item, target_id, depth + 1)
        if path:
            return [item] + path
    return []


def explode(bom, quantity, warehouse=None, level=1, depth=0):
    """The full tree under `bom` for `quantity` made: one row per component at
    each level, with made-in-house components expanded under their own default BOM."""
    rows = []
    factor = Decimal(quantity) / bom.output_quantity
    for c in bom.components.select_related('item'):
        needed = (c.quantity * factor).quantize(QTY, rounding=ROUND_HALF_UP)
        sub = default_bom(c.item) if depth < MAX_DEPTH else None
        on_hand = inventory.on_hand(bom.company, c.item, warehouse)[0] if warehouse else None
        rows.append({'level': level, 'item': c.item, 'quantity': needed, 'made': sub is not None, 'bom': sub,
                     'on_hand': on_hand, 'short': max(ZERO, needed - on_hand) if on_hand is not None else None})
        if sub is not None:
            rows += explode(sub, needed, warehouse, level + 1, depth + 1)
    return rows


def rolled_unit_cost(bom, depth=0):
    """Material cost of one unit: bought components at their purchase price,
    made ones at their own BOM's rolled cost."""
    total = ZERO
    for c in bom.components.select_related('item'):
        sub = default_bom(c.item) if depth < MAX_DEPTH else None
        rate = rolled_unit_cost(sub, depth + 1) if sub else (c.item.purchase_price or ZERO)
        total += c.quantity * rate
    return (total / bom.output_quantity).quantize(CENT, rounding=ROUND_HALF_UP)


# --- Work orders -------------------------------------------------------------------------------

WO = WorkOrder


def _update_progress(wo):
    """In progress once anything's made; completed once the planned quantity is."""
    if wo.status in (WO.Status.CLOSED, WO.Status.CANCELLED):
        return
    made = sum((e.quantity_produced for e in wo.entries.filter(status=ProductionEntry.Status.POSTED)), ZERO)
    if made >= wo.quantity:
        wo.status, wo.finished_at = WO.Status.COMPLETED, timezone.now()
    elif made > 0 or wo.entries.filter(status=ProductionEntry.Status.POSTED).exists():
        wo.status, wo.finished_at = WO.Status.IN_PROGRESS, None
    elif wo.status != WO.Status.DRAFT:
        wo.status, wo.finished_at = WO.Status.RELEASED, None
    wo.save(update_fields=['status', 'finished_at'])


def _wo_fields(company, wo, data):
    item = Item.objects.filter(company=company, pk=data.get('item_id')).first()
    if item is None:
        raise ProductionError("Choose what to make.")
    bom = (BillOfMaterials.objects.filter(company=company, item=item, pk=data['bom_id']).first() if data.get('bom_id')
           else default_bom(item))
    if bom is None or not bom.is_active:
        raise ProductionError(f"{item.name} has no active bill of materials. Add one first.")
    quantity = _decimal(data.get('quantity'), 'the quantity')
    if quantity <= 0:
        raise ProductionError("The quantity must be more than 0.")
    source = inventory.store(company, data.get('source_warehouse_id'))
    if source is None:
        raise ProductionError("Choose the store materials come from.")
    target = inventory.store(company, data.get('target_warehouse_id')) if data.get('target_warehouse_id') else source
    if target is None:
        raise ProductionError("Choose the store finished goods go to.")
    wo.company, wo.item, wo.bom, wo.quantity = company, item, bom, quantity
    wo.due_date, wo.source_warehouse, wo.target_warehouse = data.get('due_date'), source, target
    wo.notes = (data.get('notes') or '').strip()[:300]
    return wo


@transaction.atomic
def save_work_order(membership, wo, data):
    """Creates a draft work order, or updates one that's still a draft."""
    if wo.pk:
        wo = WO.objects.select_for_update().get(pk=wo.pk)
        if wo.status != WO.Status.DRAFT:
            raise ProductionError("Only a draft work order can be changed.")
    wo = _wo_fields(membership.company, wo, data)
    if wo.pk is None:
        wo.number = next_number(membership.company, 'erp_wo', 'WO', timezone.localdate())
        wo.created_by = membership.user
    wo.save()
    return wo


def _create_child(membership, parent, item, bom, quantity):
    child = WO.objects.create(
        company=parent.company, number=next_number(parent.company, 'erp_wo', 'WO', timezone.localdate()), item=item, bom=bom,
        quantity=quantity, due_date=parent.due_date, source_warehouse=parent.source_warehouse,
        target_warehouse=parent.source_warehouse,  # sub-assemblies go where the parent takes its materials from
        parent=parent, status=WO.Status.RELEASED, notes=f"For {parent.number}", created_by=membership.user,
        released_at=timezone.now(),
    )
    _make_children(membership, child)
    return child


def _make_children(membership, wo, depth=0):
    """Child work orders for the made-in-house components this order is short of."""
    if depth >= MAX_DEPTH:
        return
    for row in plan(wo.bom, wo.quantity, wo.source_warehouse):
        sub = default_bom(row['item'])
        if sub is not None and row['short'] > 0:
            _create_child(membership, wo, row['item'], sub, row['short'])


@transaction.atomic
def release(membership, wo, make_subassemblies=False):
    wo = WO.objects.select_for_update().get(pk=wo.pk)
    if wo.status != WO.Status.DRAFT:
        raise ProductionError("Only a draft work order can be released.")
    if not wo.bom.is_active:
        raise ProductionError("Its BOM is inactive.")
    wo.status, wo.released_at = WO.Status.RELEASED, timezone.now()
    wo.save(update_fields=['status', 'released_at'])
    if make_subassemblies:
        _make_children(membership, wo)
    return wo


@transaction.atomic
def close(membership, wo, reason):
    """Stops an order that's been started: the rest won't be made."""
    reason = (reason or '').strip()
    if not reason:
        raise ProductionError("Say why it's being closed short.")
    wo = WO.objects.select_for_update().get(pk=wo.pk)
    if wo.status != WO.Status.IN_PROGRESS:
        raise ProductionError("Only a work order in progress can be closed short.")
    wo.status, wo.close_reason, wo.finished_at = WO.Status.CLOSED, reason[:300], timezone.now()
    wo.save(update_fields=['status', 'close_reason', 'finished_at'])
    return wo


@transaction.atomic
def cancel_work_order(membership, wo, reason):
    """Cancels an order nothing has been made against, with its unstarted child orders."""
    reason = (reason or '').strip()
    if not reason:
        raise ProductionError("Say why it's being cancelled.")
    wo = WO.objects.select_for_update().get(pk=wo.pk)
    if wo.status not in (WO.Status.DRAFT, WO.Status.RELEASED):
        raise ProductionError("Production has been recorded against this work order. Close it short instead.")
    for child in wo.children.filter(status__in=[WO.Status.DRAFT, WO.Status.RELEASED]):
        cancel_work_order(membership, child, f"Parent {wo.number} cancelled")
    wo.status, wo.close_reason, wo.finished_at = WO.Status.CANCELLED, reason[:300], timezone.now()
    wo.save(update_fields=['status', 'close_reason', 'finished_at'])
    return wo


def progress(wo):
    """Planned vs actual: output, and material per component."""
    entries = list(wo.entries.filter(status=ProductionEntry.Status.POSTED))
    made = sum((e.quantity_produced for e in entries), ZERO)
    rejected = sum((e.quantity_rejected for e in entries), ZERO)
    cost = sum((costing.total_cost(e) for e in entries), ZERO)
    used = {}
    for c in ProductionConsumption.objects.filter(entry__in=entries).values('item_id', 'quantity'):
        used[c['item_id']] = used.get(c['item_id'], ZERO) + c['quantity']
    materials = []
    for row in plan(wo.bom, wo.quantity, wo.source_warehouse):
        materials.append({'item': row['item'], 'planned': row['planned'], 'used': used.get(row['item'].pk, ZERO),
                          'on_hand': row['on_hand'], 'short': row['short']})
    return {'made': made, 'rejected': rejected, 'remaining': max(ZERO, wo.quantity - made), 'material_cost': cost,
            'actual_unit_cost': (cost / made).quantize(CENT, rounding=ROUND_HALF_UP) if made else ZERO,
            'planned_unit_cost': costing.standard_cost(wo.bom)['total'], 'materials': materials}
