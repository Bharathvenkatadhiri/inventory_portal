"""Batch and serial tracking (plans with batch/serial tracking).

An item can be tracked by batch (every quantity carries a batch or heat
number) or by serial number (every unit has its own). Each stock ledger
entry of such an item names its Batch, so stock is known per batch, and
following the entries that share a document number traces a batch
forward (what it was made into, who it was sent to) and back (what it
was made from, which supplier it came from).

Stock coming in: incoming() turns what the person entered into batches,
making new ones as needed (numbered from the document when left blank).
Stock going out: outgoing() takes the batches picked, or else the ones
that expire first (then the oldest), skipping expired ones. write()
records the ledger entries for either.

Costing doesn't change: stock is still valued at the moving average cost
of the item in the warehouse.
"""
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Sum
from django.utils import timezone

from plans import erp

from .models import Batch, StockEntry

ZERO = Decimal('0')
ONE = Decimal('1')
MAX_SERIALS = 1000
TRACKING = ('none', 'batch', 'serial')


class BatchError(Exception):
    """Batches entered or picked that don't add up; the message says why."""


def available(company):
    """Whether the company's plan includes batch/serial tracking."""
    return erp.includes(company.erp_plan, erp.BATCH_SERIAL)


def _number(value):
    return ' '.join(str(value or '').split())[:60]


def _quantity(value, item):
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BatchError(f"{item.name}: enter each batch's quantity as a number.")
    if not quantity.is_finite() or quantity <= 0:
        raise BatchError(f"{item.name}: each batch's quantity must be more than 0.")
    return quantity


def _fmt(quantity):
    return f"{quantity.normalize():f}"


def find(item, number):
    return Batch.objects.filter(item=item, number__iexact=_number(number)).first()


def batch_stock(batch, warehouse=None):
    entries = batch.entries.all()
    if warehouse is not None:
        entries = entries.filter(warehouse=warehouse)
    return entries.aggregate(q=Sum('quantity'))['q'] or ZERO


# --- Coming in -----------------------------------------------------------------------------

def _expiry(item, given, day):
    if given.get('expires_on'):
        return given['expires_on']
    if item.shelf_life_days:
        return (given.get('manufactured_on') or day) + timedelta(days=item.shelf_life_days)
    return None


def _make(company, item, number, given, *, origin, reference, day, supplier):
    if given.get('expires_on') and given.get('manufactured_on') and given['expires_on'] < given['manufactured_on']:
        raise BatchError(f"{item.name}, batch {number}: it can't expire before it was made.")
    return Batch.objects.create(
        company=company, item=item, number=number, supplier_batch=_number(given.get('supplier_batch')),
        manufactured_on=given.get('manufactured_on'), expires_on=_expiry(item, given, day), origin=origin,
        reference=reference[:40], supplier=supplier,
    )


def next_serials(item, count):
    """`count` unused serial numbers for an item: CODE-00001, CODE-00002..."""
    prefix = f"{item.code}-"
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$", re.IGNORECASE)
    used = [int(m.group(1)) for n in item.batches.filter(number__istartswith=prefix).values_list('number', flat=True)
            if (m := pattern.match(n))]
    start = max(used, default=0) + 1
    return [f"{prefix}{n:05d}" for n in range(start, start + count)]


def incoming(company, item, quantity, given=None, *, origin, reference, day, supplier=None, default_number=''):
    """[(batch or None, quantity)] for `quantity` of `item` coming in.

    `given` is what was entered: [{number, quantity, supplier_batch,
    manufactured_on, expires_on}]. A batch number already used for the
    item adds to that batch. Left empty, a batch-tracked item gets one
    batch numbered `default_number` (or the document's number) and a
    serial-tracked one gets the next serial numbers."""
    given = [g for g in (given or []) if _number(g.get('number')) or g.get('quantity') not in (None, '')]
    if not item.tracked:
        if given:
            raise BatchError(f"{item.name} isn't tracked by batch or serial number.")
        return [(None, quantity)]
    if quantity <= 0:
        return []
    kwargs = dict(origin=origin, reference=reference, day=day, supplier=supplier)

    if item.by_serial:
        if quantity != quantity.to_integral_value():
            raise BatchError(f"{item.name} has a serial number on each unit, so the quantity must be a whole number.")
        count = int(quantity)
        if count > MAX_SERIALS:
            raise BatchError(f"{item.name}: up to {MAX_SERIALS} serial numbers at a time.")
        numbers = [_number(g.get('number')) for g in given]
        if any(not n for n in numbers):
            raise BatchError(f"{item.name}: enter each serial number.")
        if len({n.lower() for n in numbers}) != len(numbers):
            raise BatchError(f"{item.name}: a serial number is entered twice.")
        if numbers and len(numbers) != count:
            raise BatchError(f"{item.name}: {count} came in, so enter {count} serial numbers (there are {len(numbers)}).")
        pairs = list(zip(numbers, given)) if numbers else [(n, {}) for n in next_serials(item, count)]
        rows = []
        for number, g in pairs:
            if find(item, number):
                raise BatchError(f"{item.name}: serial number {number} is already used.")
            rows.append((_make(company, item, number, g, **kwargs), ONE))
        return rows

    if not given:
        given = [{'number': default_number or reference, 'quantity': quantity}]
    elif len(given) == 1 and given[0].get('quantity') in (None, ''):
        given = [{**given[0], 'quantity': quantity}]
    merged, details = defaultdict(lambda: ZERO), {}
    for g in given:
        number = _number(g.get('number'))
        if not number:
            raise BatchError(f"{item.name}: enter each batch number.")
        merged[number.lower()] += _quantity(g.get('quantity'), item)
        details.setdefault(number.lower(), (number, g))
    total = sum(merged.values(), ZERO)
    if total != quantity:
        raise BatchError(f"{item.name}: the batches add up to {_fmt(total)} but {_fmt(quantity)} {item.unit} came in.")
    rows = []
    for key, batch_quantity in merged.items():
        number, g = details[key]
        batch = find(item, number)
        if batch is None:
            batch = _make(company, item, number, g, **kwargs)
        elif not batch.expires_on and _expiry(item, g, day):
            batch.expires_on = _expiry(item, g, day)
            batch.save(update_fields=['expires_on'])
        rows.append((batch, batch_quantity))
    return rows


# --- Going out ------------------------------------------------------------------------------

def _first_out(row):
    """Sort key: batches that expire first, then the oldest; stock with no batch last."""
    batch = row[0]
    if batch is None:
        return (True,)
    return (False, batch.expires_on is None, batch.expires_on or date.max, batch.created_at, batch.pk)


def stock_by_batch(company, item, warehouse, within=None):
    """[(batch or None, quantity on hand)] of an item in a warehouse, the
    first to expire first. `within` limits it to some batch ids."""
    entries = StockEntry.objects.filter(company=company, item=item, warehouse=warehouse)
    if within is not None:
        entries = entries.filter(batch_id__in=within)
    totals = {r['batch_id']: r['q'] for r in entries.values('batch_id').annotate(q=Sum('quantity')) if r['q'] > 0}
    batches = {b.pk: b for b in Batch.objects.filter(pk__in=[k for k in totals if k]).select_related('item')}
    return sorted(((batches.get(k), q) for k, q in totals.items()), key=_first_out)


def _expired(batch, day):
    return batch is not None and batch.expires_on is not None and batch.expires_on < day


def outgoing(company, item, warehouse, quantity, picks=None, *, day=None, allow_expired=False, within=None):
    """[(batch or None, quantity)] for `quantity` of `item` going out of
    `warehouse`: the batches picked ([{batch_id or number, quantity}]),
    or else the first to expire first. Expired batches are only used when
    `allow_expired` (returns, QC, goods coming back from a job worker)."""
    if not item.tracked:
        if picks:
            raise BatchError(f"{item.name} isn't tracked by batch or serial number.")
        return [(None, quantity)]
    if quantity <= 0:
        return []
    day = day or timezone.localdate()
    stock = stock_by_batch(company, item, warehouse, within)
    picks = [p for p in (picks or []) if p.get('batch_id') or _number(p.get('number'))]
    if picks:
        on_hand = {b.pk if b else None: (b, q) for b, q in stock}
        by_number = {b.number.lower(): b.pk for b, _q in stock if b}
        taken, rows = defaultdict(lambda: ZERO), []
        for p in picks:
            key = p.get('batch_id') or by_number.get(_number(p.get('number')).lower())
            if key not in on_hand:
                what = 'Serial number' if item.by_serial else 'Batch'
                raise BatchError(f"{item.name}: {what.lower()} {_number(p.get('number')) or p.get('batch_id')} isn't in stock in {warehouse.name}.")
            batch, have = on_hand[key]
            if _expired(batch, day) and not allow_expired:
                raise BatchError(f"{item.name}: batch {batch.number} expired on {batch.expires_on:%d %b %Y}.")
            want = ONE if item.by_serial else _quantity(p.get('quantity'), item)
            taken[key] += want
            if taken[key] > have:
                raise BatchError(f"{item.name}: batch {batch.number} has {_fmt(have)} {item.unit} in {warehouse.name}.")
        total = sum(taken.values(), ZERO)
        if total != quantity:
            raise BatchError(f"{item.name}: the batches picked add up to {_fmt(total)} but {_fmt(quantity)} {item.unit} is going out.")
        return [(on_hand[k][0], q) for k, q in taken.items()]

    rows, left = [], quantity
    for batch, have in stock:
        if left == 0:
            break
        if _expired(batch, day) and not allow_expired:
            continue
        take = min(have, left)
        rows.append((batch, take))
        left -= take
    if left > 0:
        usable = quantity - left
        expired = sum((q for b, q in stock if _expired(b, day)), ZERO) if not allow_expired else ZERO
        note = f" ({_fmt(expired)} more is in expired batches)" if expired else ''
        raise BatchError(f"{item.name}: only {_fmt(usable)} {item.unit} in {warehouse.name} can be used{note}.")
    return rows


def write(company, item, warehouse, kind, rows, *, sign, rate, entry_date, reference, note='', user=None):
    """Ledger entries for each (batch, quantity) row: in when sign is 1, out when -1."""
    return [StockEntry.objects.create(company=company, item=item, warehouse=warehouse, kind=kind, quantity=sign * quantity,
                                      rate=rate, entry_date=entry_date, reference=reference, note=note[:200], batch=batch,
                                      created_by=user)
            for batch, quantity in rows if quantity]


def reverse(entries, *, entry_date, note, user=None):
    """The opposite of each entry (same item, warehouse, batch and rate), for cancelling a document."""
    return [StockEntry.objects.create(company=e.company, item=e.item, warehouse=e.warehouse, kind=e.kind, quantity=-e.quantity,
                                      rate=e.rate, entry_date=entry_date, reference=e.reference, note=note[:200],
                                      batch=e.batch, created_by=user)
            for e in entries]


def still_on_hand(entries):
    """Raises BatchError when stock some entries brought in has since gone
    out of their batch, so they can't be reversed."""
    for e in entries:
        if e.quantity > 0 and e.batch_id and batch_stock(e.batch, e.warehouse) < e.quantity:
            what = 'serial number' if e.item.by_serial else 'batch'
            raise BatchError(f"{e.item.name}: {what} {e.batch.number} has already been used or sent from {e.warehouse.name}.")


def numbers(rows):
    """'B1 (40), B2 (10)' for showing which batches a line used."""
    return ', '.join(b.number if b.item.by_serial else f"{b.number} ({_fmt(q)})" for b, q in rows if b)


def by_document(company, references, sign):
    """{(document number, item id): [(batch, quantity)]}: the batches the
    documents moved in (sign 1) or out (-1), net of any reversal."""
    net = defaultdict(lambda: ZERO)
    entries = StockEntry.objects.filter(company=company, reference__in=list(references), batch__isnull=False)
    for e in entries.values('reference', 'item_id', 'batch_id', 'warehouse_id').annotate(q=Sum('quantity')):
        if e['q'] * sign > 0:
            net[(e['reference'], e['item_id'], e['batch_id'])] += abs(e['q'])
    found = {b.pk: b for b in Batch.objects.filter(pk__in={k[2] for k in net}).select_related('item')}
    result = defaultdict(list)
    for (reference, item_id, batch_id), quantity in sorted(net.items(), key=lambda kv: found[kv[0][2]].number):
        result[(reference, item_id)].append((found[batch_id], quantity))
    return result


# --- Tracing --------------------------------------------------------------------------------

MADE_OUT = (StockEntry.Kind.PRODUCTION_ISSUE, StockEntry.Kind.JOB_WORK_IN)
MADE_IN = (StockEntry.Kind.PRODUCTION_RECEIPT, StockEntry.Kind.JOB_WORK_IN)


def _net_references(entries, sign):
    """Document numbers where this batch moved in (sign 1) or out (-1) and wasn't reversed."""
    net = defaultdict(lambda: ZERO)
    for e in entries:
        net[e.reference] += e.quantity
    return {ref for ref, q in net.items() if ref and q * sign > 0}


def trace(batch):
    """What a batch was made from and made into: {'made_from': [Batch],
    'made_into': [Batch]}, following production entries and job work."""
    entries = list(batch.entries.all())
    used_in = _net_references([e for e in entries if e.kind in MADE_OUT and e.quantity < 0 or
                               e.kind == StockEntry.Kind.PRODUCTION_ISSUE], -1)
    came_from = _net_references([e for e in entries if e.kind in MADE_IN and e.quantity > 0 or
                                 e.kind == StockEntry.Kind.PRODUCTION_RECEIPT], 1)

    def batches(references, kinds, sign):
        rows = StockEntry.objects.filter(company=batch.company, reference__in=references, kind__in=kinds,
                                         batch__isnull=False).exclude(batch=batch)
        net = defaultdict(lambda: ZERO)
        for e in rows:
            net[e.batch_id] += e.quantity
        ids = [k for k, q in net.items() if q * sign > 0]
        return list(Batch.objects.filter(pk__in=ids).select_related('item').order_by('item__code', 'number'))

    return {
        'made_into': batches(used_in, MADE_IN, 1) if used_in else [],
        'made_from': batches(came_from, MADE_OUT, -1) if came_from else [],
    }
