"""Job work (CGST Act s.143, rules 45 and 55). Callers (jobwork.api)
check the permission; this checks the job worker, quantities and stock,
and raises JobWorkError with a message to show.

Stock:
- sending: out of the store, into the system 'At job workers' location,
  at moving average cost (the goods are still the company's);
- receiving back: the input accounted for (processed + waste) leaves 'At
  job workers'; what came back enters the chosen store at the input's cost
  plus the job work charge, spread over the output (so waste is absorbed).

Inputs must come back within a year of the challan (s.143(3)), or they're
treated as supplied on the day they went out and GST is due.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from inventory import services as inventory
from inventory.models import Item, StockEntry, Warehouse
from organizations import access
from purchase.services import _decimal, next_number

from .models import JobWorkChallan, JobWorkChallanLine, JobWorkReceipt, JobWorkReceiptLine

Challan = JobWorkChallan
ZERO, CENT = Decimal('0'), Decimal('0.01')
RETURN_WITHIN_DAYS = 365      # inputs; capital goods have 3 years
WARN_AFTER_DAYS = 330
MAX_LINES = 100


class JobWorkError(Exception):
    """A change that isn't allowed; the message says why."""


def at_job_workers(company):
    return inventory.system_location(company, Warehouse.Kind.JOB_WORK)


def due_back(challan):
    return challan.challan_date + timedelta(days=RETURN_WITHIN_DAYS)


def age_flag(challan, today=None):
    """'' | 'due_soon' | 'overdue' while goods are still with the job worker."""
    if challan.status not in (Challan.Status.OPEN, Challan.Status.PARTLY):
        return ''
    days = ((today or timezone.localdate()) - challan.challan_date).days
    return 'overdue' if days > RETURN_WITHIN_DAYS else 'due_soon' if days > WARN_AFTER_DAYS else ''


@transaction.atomic
def send(membership, data):
    """A job work challan: goods out of a store to the job worker."""
    from accounts.models import Company

    company = membership.company
    worker = Company.objects.filter(pk=data.get('job_worker_id')).first()
    if worker is None or worker.pk == company.pk:
        raise JobWorkError("Choose the job worker.")
    decision = access.check(membership, 'job_work.manage', counterparty=worker)
    if not decision:
        raise JobWorkError(decision.reason.replace('supplier', 'supplier (job worker)'))
    process = (data.get('process') or '').strip()
    if not process:
        raise JobWorkError("Say what the job worker will do, e.g. zinc plating or heat treatment.")
    source = inventory.store(company, data.get('from_warehouse_id'), lock=True)
    if source is None:
        raise JobWorkError("Choose the store the goods go from.")
    day = data.get('challan_date') or timezone.localdate()
    if day > timezone.localdate():
        raise JobWorkError("The challan date can't be in the future.")
    rows = data.get('lines') or []
    if not rows:
        raise JobWorkError("Add at least one item.")
    if len(rows) > MAX_LINES:
        raise JobWorkError(f"A challan can have up to {MAX_LINES} lines.")
    items = {i.pk: i for i in Item.objects.filter(company=company, pk__in=[r.get('item_id') for r in rows])}
    wanted, seen = [], set()
    for row in rows:
        item = items.get(row.get('item_id'))
        if item is None or not item.tracks_stock:
            raise JobWorkError("Choose items you keep in stock.")
        if item.pk in seen:
            raise JobWorkError(f"{item.name} is listed twice; put the total on one line.")
        seen.add(item.pk)
        quantity = _decimal(row.get('quantity'), 'a quantity')
        if quantity <= 0:
            raise JobWorkError(f"{item.name}: the quantity must be more than 0.")
        on_hand = inventory.on_hand(company, item, source)[0]
        if quantity > on_hand:
            raise JobWorkError(f"{item.name}: only {on_hand.normalize():f} {item.unit} in {source.name}.")
        wanted.append((item, quantity, inventory.average_cost(company, item, source)))

    challan = Challan.objects.create(
        company=company, number=next_number(company, 'erp_jw', 'JW', day), job_worker=worker, challan_date=day,
        process=process[:120], expected_return=data.get('expected_return'), from_warehouse=source,
        vehicle_number=(data.get('vehicle_number') or '').strip().upper()[:20], notes=(data.get('notes') or '').strip()[:300],
        created_by=membership.user,
    )
    away = at_job_workers(company)
    for item, quantity, rate in wanted:
        JobWorkChallanLine.objects.create(challan=challan, item=item, quantity=quantity, rate=rate)
        for warehouse, sign in ((source, -1), (away, 1)):
            StockEntry.objects.create(company=company, item=item, warehouse=warehouse, kind=StockEntry.Kind.JOB_WORK_OUT,
                                      quantity=sign * quantity, rate=rate, entry_date=day, reference=challan.number,
                                      note=f"{process} at {worker.name or worker.legal_name}"[:200], created_by=membership.user)
    return challan


@transaction.atomic
def receive(membership, challan, data):
    """Goods back from the job worker against a challan."""
    company = membership.company
    challan = Challan.objects.select_for_update().select_related('job_worker').get(pk=challan.pk)
    if challan.status not in (Challan.Status.OPEN, Challan.Status.PARTLY):
        raise JobWorkError(f"This challan is {challan.get_status_display().lower()}.")
    into = inventory.store(company, data.get('into_warehouse_id') or challan.from_warehouse_id, lock=True)
    if into is None:
        raise JobWorkError("Choose the store the goods come back into.")
    day = data.get('received_date') or timezone.localdate()
    if day > timezone.localdate():
        raise JobWorkError("The date can't be in the future.")
    if day < challan.challan_date:
        raise JobWorkError("Goods can't come back before they were sent.")

    lines = {l.pk: l for l in challan.lines.select_for_update().select_related('item')}
    rows = []
    for row in data.get('lines') or []:
        line = lines.get(row.get('challan_line_id'))
        if line is None:
            raise JobWorkError("A line isn't on this challan.")
        processed = _decimal(row.get('input_quantity') or 0, 'the quantity back')
        waste = _decimal(row.get('waste_quantity') or 0, 'the waste')
        if processed < 0 or waste < 0:
            raise JobWorkError(f"{line.item.name}: quantities can't be negative.")
        if processed + waste == 0:
            continue
        if processed + waste > line.pending_quantity:
            raise JobWorkError(f"{line.item.name}: only {line.pending_quantity.normalize():f} {line.item.unit} is still with the job worker.")
        output_item = line.item
        if row.get('output_item_id') and row['output_item_id'] != line.item_id:
            output_item = Item.objects.filter(company=company, pk=row['output_item_id']).first()
            if output_item is None or not output_item.tracks_stock or not output_item.is_active:
                raise JobWorkError("What came back must be one of your active stock items.")
        output = _decimal(row.get('output_quantity') if row.get('output_quantity') not in (None, '') else processed, 'the quantity received')
        if output < 0 or (processed and output == 0):
            raise JobWorkError(f"{line.item.name}: enter how much came back.")
        charge = _decimal(row.get('charge_per_unit') or 0, 'the charge')
        if charge < 0:
            raise JobWorkError("The job work charge can't be negative.")
        rows.append((line, processed, waste, output_item, output, charge))
    if not rows:
        raise JobWorkError("Enter what came back for at least one line.")

    receipt = JobWorkReceipt.objects.create(
        company=company, challan=challan, number=next_number(company, 'erp_jwr', 'JWR', day), received_date=day,
        job_worker_challan=(data.get('job_worker_challan') or '').strip()[:40], into_warehouse=into,
        notes=(data.get('notes') or '').strip()[:300], created_by=membership.user,
    )
    away = at_job_workers(company)
    from quality import services as quality
    for line, processed, waste, output_item, output, charge in rows:
        input_value = (processed + waste) * line.rate
        rate = ((input_value + charge * output) / output).quantize(CENT, rounding=ROUND_HALF_UP) if output else ZERO
        JobWorkReceiptLine.objects.create(receipt=receipt, challan_line=line, input_quantity=processed, waste_quantity=waste,
                                          output_item=output_item, output_quantity=output, charge_per_unit=charge, rate=rate)
        StockEntry.objects.create(company=company, item=line.item, warehouse=away, kind=StockEntry.Kind.JOB_WORK_IN,
                                  quantity=-(processed + waste), rate=line.rate, entry_date=day, reference=receipt.number,
                                  note=f"Back from {challan.process}" + (f", {waste.normalize():f} lost" if waste else ''),
                                  created_by=membership.user)
        if output:
            inspect = quality.needs_inspection(company, output_item)
            StockEntry.objects.create(company=company, item=output_item, warehouse=quality.hold(company) if inspect else into,
                                      kind=StockEntry.Kind.JOB_WORK_IN, quantity=output, rate=rate, entry_date=day,
                                      reference=receipt.number, note=f"{challan.process} ({challan.number})"[:200],
                                      created_by=membership.user)
            if inspect:
                quality.open_lot(company, item=output_item, quantity=output, rate=rate, destination=into, source='receipt',
                                 reference=receipt.number, received_on=day, supplier=challan.job_worker)
        line.returned_quantity += processed
        line.waste_quantity += waste
        line.save(update_fields=['returned_quantity', 'waste_quantity'])
    challan.status = Challan.Status.CLOSED if all(l.pending_quantity == 0 for l in lines.values()) else Challan.Status.PARTLY
    challan.save(update_fields=['status'])
    return receipt


@transaction.atomic
def cancel(membership, challan, reason):
    """Undo a challan nothing has come back against: the goods return to the store."""
    reason = (reason or '').strip()
    if not reason:
        raise JobWorkError("Say why it's being cancelled.")
    challan = Challan.objects.select_for_update().get(pk=challan.pk)
    if challan.status != Challan.Status.OPEN or challan.receipts.exists():
        raise JobWorkError("Goods have come back against this challan, so it can't be cancelled.")
    for entry in StockEntry.objects.filter(company=challan.company, reference=challan.number, kind=StockEntry.Kind.JOB_WORK_OUT):
        StockEntry.objects.create(company=challan.company, item=entry.item, warehouse=entry.warehouse, kind=entry.kind,
                                  quantity=-entry.quantity, rate=entry.rate, entry_date=timezone.localdate(),
                                  reference=challan.number, note=f"Cancelled: {reason}"[:200], created_by=membership.user)
    challan.status, challan.cancel_reason = Challan.Status.CANCELLED, reason[:300]
    challan.save(update_fields=['status', 'cancel_reason'])
    return challan


# --- ITC-04 -----------------------------------------------------------------------------------

def itc04(company, start, end):
    """(table 4 rows: goods sent; table 5 rows: goods received back), for challans
    and receipts dated between start and end."""
    sent = []
    for challan in (Challan.objects.filter(company=company, challan_date__gte=start, challan_date__lte=end)
                    .exclude(status=Challan.Status.CANCELLED).select_related('job_worker').prefetch_related('lines__item')):
        for line in challan.lines.all():
            sent.append({
                'job_worker_gstin': challan.job_worker.gstin or '', 'job_worker_state': challan.job_worker.state,
                'challan_number': challan.number, 'challan_date': challan.challan_date, 'goods': line.item.name,
                'hsn': line.item.hsn_code, 'uqc': line.item.unit, 'quantity': line.quantity,
                'taxable_value': (line.quantity * line.rate).quantize(CENT, rounding=ROUND_HALF_UP),
                'gst_rate': line.item.gst_rate, 'process': challan.process,
            })
    received = []
    for receipt in (JobWorkReceipt.objects.filter(company=company, received_date__gte=start, received_date__lte=end)
                    .select_related('challan__job_worker').prefetch_related('lines__challan_line__item', 'lines__output_item')):
        challan = receipt.challan
        for line in receipt.lines.all():
            received.append({
                'job_worker_gstin': challan.job_worker.gstin or '', 'job_worker_state': challan.job_worker.state,
                'original_challan_number': challan.number, 'original_challan_date': challan.challan_date,
                'job_worker_challan': receipt.job_worker_challan, 'received_date': receipt.received_date,
                'process': challan.process, 'goods': line.challan_line.item.name, 'uqc': line.challan_line.item.unit,
                'quantity': line.input_quantity, 'losses_and_wastes': line.waste_quantity,
            })
    return sent, received
