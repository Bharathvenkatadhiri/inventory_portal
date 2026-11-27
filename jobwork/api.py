"""/api/v1 endpoints for job work: challans, receipts back, the challan
PDF, and ITC-04 rows as CSV. All need job_work.manage (Store, Production
Planner, Org Admin), on plans with job work."""
import csv
import io
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from inventory import batches as batch_tracking
from inventory.api import BatchIn, BatchPickIn, BatchQtyOut, batch_rows
from organizations.app_api import membership_for
from purchase.api import PartyOut, _name, _party, _writes_allowed

from . import services
from .models import JobWorkChallan

router = Router(tags=['job-work'])
Challan = JobWorkChallan
LIST_LIMIT = 500


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.JobWorkError as exc:
        raise HttpError(400, str(exc))


class JobWorkLineIn(Schema):
    item_id: int
    quantity: Decimal
    # Batch- or serial-tracked items: which to send. Left out, the first to expire.
    batches: List[BatchPickIn] = []


class JobWorkChallanIn(Schema):
    job_worker_id: int
    process: str
    from_warehouse_id: int
    challan_date: Optional[date] = None
    expected_return: Optional[date] = None
    vehicle_number: str = ''
    notes: str = ''
    lines: List[JobWorkLineIn]


class JobWorkChallanLineOut(Schema):
    id: int
    item_id: int
    item_code: str
    item_name: str
    unit: str
    quantity: Decimal
    rate: Decimal
    returned_quantity: Decimal
    waste_quantity: Decimal
    pending_quantity: Decimal
    # The batches sent.
    batches: List[BatchQtyOut]


class JobWorkReceiptLineOut(Schema):
    item_name: str
    input_quantity: Decimal
    waste_quantity: Decimal
    output_item_name: str
    output_quantity: Decimal
    unit: str
    rate: Decimal


class JobWorkReceiptOut(Schema):
    id: int
    number: str
    received_date: date
    job_worker_challan: str
    into_warehouse: str
    recorded_by: str
    lines: List[JobWorkReceiptLineOut]


class JobWorkChallanOut(Schema):
    id: int
    number: str
    job_worker: PartyOut
    challan_date: date
    process: str
    expected_return: Optional[date]
    due_back: date
    age_flag: str
    from_warehouse: str
    vehicle_number: str
    notes: str
    status: str
    status_label: str
    cancel_reason: str
    value: Decimal
    lines: List[JobWorkChallanLineOut]
    receipts: List[JobWorkReceiptOut]
    can_change: bool


def _challan_out(c, membership):
    lines = list(c.lines.select_related('item'))
    sent = batch_tracking.by_document(c.company, [c.number], -1)
    return {
        'id': c.pk, 'number': c.number, 'job_worker': _party(c.job_worker), 'challan_date': c.challan_date, 'process': c.process,
        'expected_return': c.expected_return, 'due_back': services.due_back(c), 'age_flag': services.age_flag(c),
        'from_warehouse': c.from_warehouse.name, 'vehicle_number': c.vehicle_number, 'notes': c.notes, 'status': c.status,
        'status_label': c.get_status_display(), 'cancel_reason': c.cancel_reason,
        'value': sum((l.quantity * l.rate for l in lines), Decimal('0')).quantize(Decimal('0.01')),
        'lines': [{'id': l.pk, 'item_id': l.item_id, 'item_code': l.item.code, 'item_name': l.item.name, 'unit': l.item.unit,
                   'quantity': l.quantity, 'rate': l.rate, 'returned_quantity': l.returned_quantity,
                   'waste_quantity': l.waste_quantity, 'pending_quantity': l.pending_quantity,
                   'batches': batch_rows(sent.get((c.number, l.item_id)))} for l in lines],
        'receipts': [{
            'id': r.pk, 'number': r.number, 'received_date': r.received_date, 'job_worker_challan': r.job_worker_challan,
            'into_warehouse': r.into_warehouse.name, 'recorded_by': _name(r.created_by),
            'lines': [{'item_name': rl.challan_line.item.name, 'input_quantity': rl.input_quantity, 'waste_quantity': rl.waste_quantity,
                       'output_item_name': rl.output_item.name, 'output_quantity': rl.output_quantity, 'unit': rl.output_item.unit,
                       'rate': rl.rate} for rl in r.lines.select_related('challan_line__item', 'output_item')],
        } for r in c.receipts.select_related('into_warehouse', 'created_by')],
        'can_change': c.status in (Challan.Status.OPEN, Challan.Status.PARTLY) and _writes_allowed(membership),
    }


def _member(request, write=False):
    return membership_for(request, 'job_work.manage', write=write)


def _challan(membership, challan_id):
    return get_object_or_404(Challan.objects.select_related('job_worker', 'from_warehouse'), pk=challan_id, company=membership.company)


@router.get('/job-work', response=List[JobWorkChallanOut])
def list_challans(request, status: str = ''):
    """status: 'open' (with the job worker), or a status, or '' for all."""
    membership = _member(request)
    challans = Challan.objects.filter(company=membership.company).select_related('job_worker', 'from_warehouse')
    if status == 'open':
        challans = challans.filter(status__in=[Challan.Status.OPEN, Challan.Status.PARTLY])
    elif status:
        challans = challans.filter(status=status)
    return [_challan_out(c, membership) for c in challans[:LIST_LIMIT]]


@router.post('/job-work', response=JobWorkChallanOut)
def send(request, payload: JobWorkChallanIn):
    membership = _member(request, write=True)
    challan = _run(services.send, membership, payload.dict())
    return _challan_out(_challan(membership, challan.pk), membership)


class JobWorkReceiveLineIn(Schema):
    challan_line_id: int
    input_quantity: Decimal = Decimal('0')
    waste_quantity: Decimal = Decimal('0')
    output_item_id: Optional[int] = None
    output_quantity: Optional[Decimal] = None
    charge_per_unit: Decimal = Decimal('0')
    # A tracked item processed into another (or a different count): its batch number.
    # Left out, the same batches come back, or the first batch's number is kept.
    output_batches: List[BatchIn] = []


class JobWorkReceiveIn(Schema):
    into_warehouse_id: Optional[int] = None
    received_date: Optional[date] = None
    job_worker_challan: str = ''
    notes: str = ''
    lines: List[JobWorkReceiveLineIn]


@router.post('/job-work/{challan_id}/receipts', response=JobWorkChallanOut)
def receive(request, challan_id: int, payload: JobWorkReceiveIn):
    membership = _member(request, write=True)
    _run(services.receive, membership, _challan(membership, challan_id), payload.dict())
    return _challan_out(_challan(membership, challan_id), membership)


class ReasonIn(Schema):
    reason: str = ''


@router.post('/job-work/{challan_id}/cancel', response=JobWorkChallanOut)
def cancel(request, challan_id: int, payload: ReasonIn):
    membership = _member(request, write=True)
    _run(services.cancel, membership, _challan(membership, challan_id), payload.reason)
    return _challan_out(_challan(membership, challan_id), membership)


@router.get('/job-work/{challan_id}/pdf')
def challan_pdf(request, challan_id: int):
    from printouts.render import job_work_challan
    membership = _member(request)
    challan = _challan(membership, challan_id)
    response = HttpResponse(job_work_challan(challan), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{challan.number}.pdf"'
    response['Cache-Control'] = 'private, no-store'
    return response


@router.get('/job-work/itc04')
def itc04_csv(request, start: date, end: date, table: str = 'sent'):
    """ITC-04 rows for start..end: table 'sent' (table 4) or 'received' (table 5A)."""
    membership = _member(request)
    if end < start:
        raise HttpError(400, "The end date is before the start date.")
    sent, received = services.itc04(membership.company, start, end)
    out = io.StringIO()
    writer = csv.writer(out)
    if table == 'sent':
        writer.writerow(['Job worker GSTIN', 'Job worker state', 'Challan number', 'Challan date', 'Description of goods', 'HSN',
                         'UQC', 'Quantity', 'Taxable value', 'GST rate %', 'Nature of job work'])
        for r in sent:
            writer.writerow([r['job_worker_gstin'], r['job_worker_state'], r['challan_number'], r['challan_date'].strftime('%d/%m/%Y'),
                             r['goods'], r['hsn'], r['uqc'], f"{r['quantity'].normalize():f}", f"{r['taxable_value']:.2f}",
                             f"{r['gst_rate'].normalize():f}", r['process']])
    elif table == 'received':
        writer.writerow(['Job worker GSTIN', 'Job worker state', 'Original challan number', 'Original challan date',
                         "Job worker's challan number", 'Date received', 'Nature of job work', 'Description of goods', 'UQC',
                         'Quantity', 'Losses and wastes'])
        for r in received:
            writer.writerow([r['job_worker_gstin'], r['job_worker_state'], r['original_challan_number'],
                             r['original_challan_date'].strftime('%d/%m/%Y'), r['job_worker_challan'],
                             r['received_date'].strftime('%d/%m/%Y'), r['process'], r['goods'], r['uqc'],
                             f"{r['quantity'].normalize():f}", f"{r['losses_and_wastes'].normalize():f}"])
    else:
        raise HttpError(400, "table must be 'sent' or 'received'.")
    response = HttpResponse(out.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="ITC04_{table}_{start:%Y%m%d}_{end:%Y%m%d}.csv"'
    response['Cache-Control'] = 'private, no-store'
    return response
