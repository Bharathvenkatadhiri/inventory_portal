"""Production costing and scrap (plans with production costing).

A good unit's cost is what went into making it:

    materials (at moving average cost)
  + labour   (person-hours x the company's labour hour rate)
  + machine  (machine hours x the machine's hour rate)
  + overhead (a % of labour and machine)
  - scrap recovered (into stock at what it fetches)
  ---------------------------------------------------
  / good units                                     = unit cost

That's the cost the units go into stock at (absorption costing, as AS 2
values inventory). Rejected units' cost is carried by the good ones.

A BOM holds the standard: its routing (machine, machine and labour hours)
and expected scrap, for its output quantity. A production entry can say
the actual hours and scrap; left blank, they're the BOM's, scaled to what
was made. Without the plan feature only materials are costed.

standard_cost() is a unit's cost at standard: materials at purchase price
(made components at their own standard), standard hours and scrap at
today's rates. Work order cost sheets and the cost report compare actual
with it.
"""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction

from inventory.models import Item
from plans import erp

from .models import BillOfMaterials, CostSettings, Machine, ProductionEntry

ZERO = Decimal('0')
CENT = Decimal('0.01')
MAX_DEPTH = 10


class CostingError(Exception):
    """A change that isn't allowed; the message says why."""


def available(company):
    return erp.includes(company.erp_plan, erp.PRODUCTION_COSTING)


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _decimal(value, what, allow_blank=True):
    if value in (None, '') and allow_blank:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        raise CostingError(f"Enter {what} as a number.")
    if not number.is_finite() or number < 0:
        raise CostingError(f"{what[0].upper()}{what[1:]} can't be negative.")
    return number


# --- Rates ------------------------------------------------------------------------------------

def settings_for(company):
    return CostSettings.objects.filter(company=company).first() or CostSettings(company=company)


def save_settings(company, data):
    s = settings_for(company)
    for field, what in (('labour_hour_rate', 'the labour hour rate'), ('overhead_percent', 'the overhead percentage')):
        if field in data and data[field] is not None:
            setattr(s, field, _decimal(data[field], what, allow_blank=False))
    if s.overhead_percent > 500:
        raise CostingError("Overheads above 500% of labour and machine cost look like a mistake.")
    s.save()
    return s


def save_machine(company, machine, data):
    code = (data.get('code') or '').strip().upper()[:20]
    name = (data.get('name') or '').strip()[:80]
    if not code or not name:
        raise CostingError("Enter the machine's code and name.")
    machine.company, machine.code, machine.name = company, code, name
    machine.hour_rate = _decimal(data.get('hour_rate') or 0, 'the hour rate', allow_blank=False)
    if 'is_active' in data and data['is_active'] is not None:
        machine.is_active = bool(data['is_active'])
    try:
        with transaction.atomic():
            machine.save()
    except IntegrityError:
        raise CostingError(f"Another machine already has the code {code}.")
    return machine


def delete_machine(machine):
    if machine.entries.exists():
        raise CostingError("Production has been recorded on this machine. Mark it inactive instead.")
    machine.delete()


# --- The routing on a BOM ----------------------------------------------------------------------

def apply_routing(company, bom, data):
    """Sets a BOM's standard routing and scrap from `data` (before it's saved)."""
    keys = ('machine_id', 'machine_hours', 'labour_hours', 'scrap_item_id', 'scrap_quantity')
    if not any(k in data for k in keys):
        return
    machine_id = data.get('machine_id')
    bom.machine = Machine.objects.filter(company=company, pk=machine_id).first() if machine_id else None
    if machine_id and bom.machine is None:
        raise CostingError("Choose one of your machines.")
    bom.machine_hours = _decimal(data.get('machine_hours') or 0, 'the machine hours', allow_blank=False)
    bom.labour_hours = _decimal(data.get('labour_hours') or 0, 'the labour hours', allow_blank=False)
    if bom.machine_hours and bom.machine is None:
        raise CostingError("Say which machine the machine hours are on.")
    scrap_id = data.get('scrap_item_id')
    bom.scrap_item = Item.objects.filter(company=company, pk=scrap_id).first() if scrap_id else None
    if scrap_id and (bom.scrap_item is None or not bom.scrap_item.tracks_stock):
        raise CostingError("Choose the scrap item (one you keep in stock).")
    if bom.scrap_item and bom.scrap_item.pk == bom.item_id:
        raise CostingError("An item can't be its own scrap.")
    bom.scrap_quantity = _decimal(data.get('scrap_quantity') or 0, 'the scrap quantity', allow_blank=False)
    if bom.scrap_quantity and bom.scrap_item is None:
        raise CostingError("Say which item the scrap is.")
    if (bom.machine or bom.labour_hours or bom.scrap_item) and not available(company):
        raise CostingError("Routing and scrap costing are on the Growth and Pro plans.")


def scrap_rate(item):
    """What scrap fetches a unit: its sale price, else its purchase price."""
    return item.sale_price if item.sale_price is not None else (item.purchase_price or ZERO)


# --- Standard cost ---------------------------------------------------------------------------

def standard_cost(bom, depth=0):
    """A unit's standard cost: {'material', 'labour', 'machine', 'overhead', 'scrap', 'total'}.
    Made components count at their own standard total; scrap is a credit."""
    from .services import default_bom

    rates = settings_for(bom.company)
    material = ZERO
    for c in bom.components.select_related('item'):
        sub = default_bom(c.item) if depth < MAX_DEPTH else None
        rate = standard_cost(sub, depth + 1)['total'] if sub else (c.item.purchase_price or ZERO)
        material += c.quantity * rate
    costed = available(bom.company)
    labour = bom.labour_hours * rates.labour_hour_rate if costed else ZERO
    machine = bom.machine_hours * bom.machine.hour_rate if costed and bom.machine_id else ZERO
    overhead = (labour + machine) * rates.overhead_percent / 100
    scrap = bom.scrap_quantity * scrap_rate(bom.scrap_item) if costed and bom.scrap_item_id else ZERO
    per = bom.output_quantity
    parts = {'material': material / per, 'labour': labour / per, 'machine': machine / per,
             'overhead': overhead / per, 'scrap': scrap / per}
    parts = {k: money(v) for k, v in parts.items()}
    parts['total'] = parts['material'] + parts['labour'] + parts['machine'] + parts['overhead'] - parts['scrap']
    return parts


# --- Costing a production entry ----------------------------------------------------------------

def conversion(company, bom, made, data):
    """Actual hours, the conversion cost and scrap for a production entry.
    Returns {'machine', 'machine_hours', 'labour_hours', 'labour_cost',
    'machine_cost', 'overhead_cost', 'scrap': [(item, quantity, rate)]}.
    `made` is good + rejected; blank hours and scrap come from the BOM."""
    costed = available(company)
    given = [k for k in ('labour_hours', 'machine_hours', 'machine_id') if data.get(k) not in (None, '')]
    if not costed:
        if given or data.get('scrap'):
            raise CostingError("Recording hours and scrap is on the Growth and Pro plans.")
        return {'machine': None, 'machine_hours': ZERO, 'labour_hours': ZERO, 'labour_cost': ZERO,
                'machine_cost': ZERO, 'overhead_cost': ZERO, 'scrap': []}
    scale = made / bom.output_quantity
    machine = bom.machine
    if data.get('machine_id'):
        machine = Machine.objects.filter(company=company, pk=data['machine_id'], is_active=True).first()
        if machine is None:
            raise CostingError("Choose one of your active machines.")
    labour_hours = _decimal(data.get('labour_hours'), 'the labour hours')
    machine_hours = _decimal(data.get('machine_hours'), 'the machine hours')
    labour_hours = (bom.labour_hours * scale).quantize(CENT) if labour_hours is None else labour_hours
    machine_hours = (bom.machine_hours * scale).quantize(CENT) if machine_hours is None else machine_hours
    if machine_hours and machine is None:
        raise CostingError("Say which machine it ran on.")
    rates = settings_for(company)
    labour_cost = money(labour_hours * rates.labour_hour_rate)
    machine_cost = money(machine_hours * machine.hour_rate) if machine else ZERO
    overhead_cost = money((labour_cost + machine_cost) * rates.overhead_percent / 100)

    scrap = []
    rows = data.get('scrap')
    if rows is None:
        if bom.scrap_item_id and bom.scrap_quantity and made:
            scrap.append((bom.scrap_item, (bom.scrap_quantity * scale).quantize(Decimal('0.001')), scrap_rate(bom.scrap_item)))
    else:
        items = {i.pk: i for i in Item.objects.filter(company=company, pk__in=[r.get('item_id') for r in rows])}
        seen = set()
        for row in rows:
            item = items.get(row.get('item_id'))
            if item is None or not item.tracks_stock or not item.is_active:
                raise CostingError("Scrap must be one of your active stock items.")
            if item.pk == bom.item_id:
                raise CostingError(f"{item.name} is what's being made; record rejects as rejected instead.")
            if item.pk in seen:
                raise CostingError(f"{item.name} is listed twice as scrap.")
            seen.add(item.pk)
            quantity = _decimal(row.get('quantity') or 0, f"the scrap quantity of {item.name}", allow_blank=False)
            rate = _decimal(row.get('rate'), f"the scrap rate of {item.name}")
            if quantity:
                scrap.append((item, quantity, scrap_rate(item) if rate is None else rate))
    return {'machine': machine, 'machine_hours': machine_hours, 'labour_hours': labour_hours, 'labour_cost': labour_cost,
            'machine_cost': machine_cost, 'overhead_cost': overhead_cost, 'scrap': scrap}


def total_cost(entry):
    return (entry.material_cost + entry.labour_cost + entry.machine_cost + entry.overhead_cost - entry.scrap_value)


# --- Cost sheets ------------------------------------------------------------------------------

PARTS = ('material', 'labour', 'machine', 'overhead', 'scrap')


def actuals(entries):
    """Totals over posted entries: the cost parts, good and rejected units."""
    out = {k: ZERO for k in PARTS}
    out.update(good=ZERO, rejected=ZERO, labour_hours=ZERO, machine_hours=ZERO, entries=0)
    for e in entries:
        out['material'] += e.material_cost
        out['labour'] += e.labour_cost
        out['machine'] += e.machine_cost
        out['overhead'] += e.overhead_cost
        out['scrap'] += e.scrap_value
        out['good'] += e.quantity_produced
        out['rejected'] += e.quantity_rejected
        out['labour_hours'] += e.labour_hours
        out['machine_hours'] += e.machine_hours
        out['entries'] += 1
    out['total'] = out['material'] + out['labour'] + out['machine'] + out['overhead'] - out['scrap']
    return out


def _sheet(bom, entries):
    actual = actuals(entries)
    standard = standard_cost(bom)
    good = actual['good']
    rows = []
    for part in PARTS + ('total',):
        at_standard = money(standard[part] * good)
        rows.append({'part': part, 'standard': at_standard, 'actual': actual[part], 'variance': actual[part] - at_standard})
    made = actual['good'] + actual['rejected']
    return {
        'good': good, 'rejected': actual['rejected'], 'entries': actual['entries'],
        'labour_hours': actual['labour_hours'], 'machine_hours': actual['machine_hours'],
        'yield_percent': money(actual['good'] * 100 / made) if made else None,
        'standard_unit_cost': standard['total'],
        'actual_unit_cost': money(actual['total'] / good) if good else None,
        'rows': rows,
    }


def work_order_sheet(wo):
    """Standard vs actual for what a work order has made so far."""
    return _sheet(wo.bom, wo.entries.filter(status=ProductionEntry.Status.POSTED))


def report(company, start, end):
    """Per item made between start and end: actual against standard."""
    entries = (ProductionEntry.objects.filter(company=company, status=ProductionEntry.Status.POSTED,
                                              entry_date__gte=start, entry_date__lte=end)
               .select_related('item', 'bom'))
    by_item = defaultdict(list)
    for e in entries:
        by_item[e.item_id].append(e)
    rows = []
    for item_id, group in by_item.items():
        item = group[0].item
        bom = BillOfMaterials.objects.filter(item=item, is_default=True).first() or group[0].bom
        sheet = _sheet(bom, group)
        scrap_quantity = sum((s.quantity for e in group for s in e.scrap.all()), ZERO)
        rows.append({'item_id': item.pk, 'item_code': item.code, 'item_name': item.name, 'unit': item.unit,
                     'scrap_quantity': scrap_quantity, **sheet})
    return sorted(rows, key=lambda r: r['item_code'])
