"""/api/v1 endpoints for bills of materials and production entries.
BOMs: any member can read them; bom.manage changes them. Production
entries: production.record (the Machine Operator light role has it) to
record and see them; cancelling needs bom.manage too, as a supervisor's
correction."""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from inventory.models import Warehouse
from organizations import access
from inventory import batches as batch_tracking
from inventory.api import BatchIn, BatchPickIn, BatchQtyOut, batch_rows
from organizations.app_api import membership_for
from purchase.api import _name, _writes_allowed

from . import costing, services
from .models import BillOfMaterials, Machine, ProductionEntry, WorkOrder

router = Router(tags=['production'])
LIST_LIMIT = 500


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.ProductionError as exc:
        raise HttpError(400, str(exc))


def _can(membership, code):
    return bool(access.check(membership, code))


# --- BOMs ----------------------------------------------------------------------------------

class ComponentIn(Schema):
    item_id: int
    quantity: Decimal


class BomIn(Schema):
    item_id: int
    name: str = 'Standard'
    output_quantity: Decimal = Decimal('1')
    notes: str = ''
    is_default: bool = False
    is_active: Optional[bool] = None
    components: List[ComponentIn]
    # Standard routing and scrap, for output_quantity (production costing).
    machine_id: Optional[int] = None
    machine_hours: Decimal = Decimal('0')
    labour_hours: Decimal = Decimal('0')
    scrap_item_id: Optional[int] = None
    scrap_quantity: Decimal = Decimal('0')


class ComponentOut(Schema):
    item_id: int
    item_code: str
    item_name: str
    unit: str
    quantity: Decimal
    rate: Decimal  # the item's purchase price, for a cost estimate


class StandardCostOut(Schema):
    # Labour, machine, overhead and the total are null for people without costing.manage:
    # with the hours they'd give away the cost rates.
    material: Decimal
    labour: Optional[Decimal]
    machine: Optional[Decimal]
    overhead: Optional[Decimal]
    scrap: Decimal
    total: Optional[Decimal]


class BomOut(Schema):
    id: int
    item_id: int
    item_code: str
    item_name: str
    unit: str
    name: str
    output_quantity: Decimal
    notes: str
    is_default: bool
    is_active: bool
    components: List[ComponentOut]
    # Materials per unit made: bought parts at purchase price, made ones at their own BOM's
    # rolled-up cost (an estimate; actual cost is set when produced).
    estimated_unit_cost: Decimal
    # Components made in house (they have a BOM of their own).
    made_in_house: List[int]
    entries: int
    editable: bool
    machine_id: Optional[int]
    machine_name: str
    machine_hours: Decimal
    labour_hours: Decimal
    scrap_item_id: Optional[int]
    scrap_item_name: str
    scrap_quantity: Decimal
    # A unit at standard: materials, routing at today's rates, less expected scrap.
    standard_cost: StandardCostOut


def _bom_out(bom, membership):
    components = []
    for c in bom.components.select_related('item'):
        rate = c.item.purchase_price or Decimal('0')
        components.append({'item_id': c.item_id, 'item_code': c.item.code, 'item_name': c.item.name, 'unit': c.item.unit,
                           'quantity': c.quantity, 'rate': rate})
    return {
        'id': bom.pk, 'item_id': bom.item_id, 'item_code': bom.item.code, 'item_name': bom.item.name, 'unit': bom.item.unit,
        'name': bom.name, 'output_quantity': bom.output_quantity, 'notes': bom.notes, 'is_default': bom.is_default,
        'is_active': bom.is_active, 'components': components,
        'estimated_unit_cost': services.rolled_unit_cost(bom),
        'made_in_house': [c.item_id for c in bom.components.all() if services.default_bom(c.item)],
        'entries': bom.entries.count(), 'editable': _writes_allowed(membership) and _can(membership, 'bom.manage'),
        'machine_id': bom.machine_id, 'machine_name': str(bom.machine) if bom.machine_id else '',
        'machine_hours': bom.machine_hours, 'labour_hours': bom.labour_hours, 'scrap_item_id': bom.scrap_item_id,
        'scrap_item_name': bom.scrap_item.name if bom.scrap_item_id else '', 'scrap_quantity': bom.scrap_quantity,
        'standard_cost': _standard(bom, membership),
    }


def _sees_costs(membership):
    return _can(membership, 'costing.manage')


def _standard(bom, membership):
    cost = costing.standard_cost(bom)
    if not _sees_costs(membership) and (cost['labour'] or cost['machine'] or cost['overhead']):
        cost.update(labour=None, machine=None, overhead=None, total=None)
    return cost


def _bom(membership, bom_id):
    return get_object_or_404(BillOfMaterials.objects.select_related('item'), pk=bom_id, company=membership.company)


@router.get('/boms', response=List[BomOut])
def list_boms(request, item_id: Optional[int] = None, active: Optional[bool] = None):
    membership = membership_for(request)
    boms = BillOfMaterials.objects.filter(company=membership.company).select_related('item')
    if item_id:
        boms = boms.filter(item_id=item_id)
    if active is not None:
        boms = boms.filter(is_active=active)
    return [_bom_out(b, membership) for b in boms[:LIST_LIMIT]]


@router.get('/boms/{bom_id}', response=BomOut)
def get_bom(request, bom_id: int):
    membership = membership_for(request)
    return _bom_out(_bom(membership, bom_id), membership)


@router.post('/boms', response=BomOut)
def create_bom(request, payload: BomIn):
    membership = membership_for(request, 'bom.manage', write=True)
    bom = _run(services.save_bom, membership, BillOfMaterials(), payload.dict())
    return _bom_out(_bom(membership, bom.pk), membership)


@router.put('/boms/{bom_id}', response=BomOut)
def update_bom(request, bom_id: int, payload: BomIn):
    membership = membership_for(request, 'bom.manage', write=True)
    bom = _run(services.save_bom, membership, _bom(membership, bom_id), payload.dict())
    return _bom_out(_bom(membership, bom.pk), membership)


@router.delete('/boms/{bom_id}', response={204: None})
def delete_bom(request, bom_id: int):
    membership = membership_for(request, 'bom.manage', write=True)
    _run(services.delete_bom, _bom(membership, bom_id))
    return 204, None


# --- Planning ----------------------------------------------------------------------------------

class PlanLineOut(Schema):
    item_id: int
    item_code: str
    item_name: str
    unit: str
    planned: Decimal
    on_hand: Decimal
    short: Decimal


@router.get('/production/plan', response=List[PlanLineOut])
def production_plan(request, bom_id: int, quantity: Decimal, warehouse_id: int):
    """What making `quantity` units (good + rejected) needs from a warehouse."""
    membership = membership_for(request, 'production.record')
    bom = _bom(membership, bom_id)
    warehouse = get_object_or_404(Warehouse, pk=warehouse_id, company=membership.company)
    if quantity <= 0:
        raise HttpError(400, "Enter how many will be made.")
    return [{'item_id': r['item'].pk, 'item_code': r['item'].code, 'item_name': r['item'].name, 'unit': r['item'].unit,
             'planned': r['planned'], 'on_hand': r['on_hand'], 'short': r['short']} for r in services.plan(bom, quantity, warehouse)]


# --- Production entries ------------------------------------------------------------------------

class ExplodeRowOut(Schema):
    level: int
    item_id: int
    item_code: str
    item_name: str
    unit: str
    quantity: Decimal
    made: bool
    on_hand: Optional[Decimal]
    short: Optional[Decimal]


@router.get('/boms/{bom_id}/explode', response=List[ExplodeRowOut])
def explode_bom(request, bom_id: int, quantity: Decimal = Decimal('1'), warehouse_id: Optional[int] = None):
    """The whole tree under a BOM for `quantity` made, sub-assemblies expanded."""
    membership = membership_for(request)
    bom = _bom(membership, bom_id)
    warehouse = get_object_or_404(Warehouse, pk=warehouse_id, company=membership.company) if warehouse_id else None
    if quantity <= 0:
        raise HttpError(400, "Enter a quantity.")
    return [{'level': r['level'], 'item_id': r['item'].pk, 'item_code': r['item'].code, 'item_name': r['item'].name,
             'unit': r['item'].unit, 'quantity': r['quantity'], 'made': r['made'], 'on_hand': r['on_hand'], 'short': r['short']}
            for r in services.explode(bom, quantity, warehouse)]


class ConsumptionIn(Schema):
    item_id: int
    # Left out, the BOM's quantity for what was made.
    quantity: Optional[Decimal] = None
    # Batch- or serial-tracked components: which were used. Left out, the first to expire.
    batches: List[BatchPickIn] = []


class ScrapIn(Schema):
    item_id: int
    quantity: Decimal
    # What it fetches a unit; left out, the scrap item's sale price.
    rate: Optional[Decimal] = None


class ScrapOut(Schema):
    item_id: int
    item_code: str
    item_name: str
    unit: str
    quantity: Decimal
    rate: Decimal
    value: Decimal


class EntryIn(Schema):
    bom_id: Optional[int] = None
    work_order_id: Optional[int] = None
    quantity_produced: Decimal
    quantity_rejected: Decimal = Decimal('0')
    entry_date: Optional[date] = None
    source_warehouse_id: Optional[int] = None
    target_warehouse_id: Optional[int] = None
    shift: str = ''
    notes: str = ''
    consumption: List[ConsumptionIn] = []
    # Production costing: actual hours and scrap. Left out, the BOM's standard, scaled.
    labour_hours: Optional[Decimal] = None
    machine_id: Optional[int] = None
    machine_hours: Optional[Decimal] = None
    scrap: Optional[List[ScrapIn]] = None
    # Batch- or serial-tracked output: its batch number or the units' serial numbers.
    # Left out, a batch numbered from the entry, or the next serial numbers.
    output_batches: List[BatchIn] = []


class ConsumptionOut(Schema):
    item_code: str
    item_name: str
    unit: str
    planned_quantity: Decimal
    quantity: Decimal
    rate: Decimal
    batches: List[BatchQtyOut]


class EntryOut(Schema):
    id: int
    number: str
    status: str
    status_label: str
    entry_date: date
    item_code: str
    item_name: str
    unit: str
    bom_name: str
    quantity_produced: Decimal
    quantity_rejected: Decimal
    source_warehouse: str
    target_warehouse: str
    material_cost: Decimal
    unit_cost: Decimal
    shift: str
    notes: str
    cancel_reason: str
    output_batches: List[BatchQtyOut]
    recorded_by: str
    work_order_id: Optional[int]
    work_order_number: str
    consumption: List[ConsumptionOut]
    can_cancel: bool
    machine_name: str
    machine_hours: Decimal
    labour_hours: Decimal
    labour_cost: Optional[Decimal]  # null without costing.manage
    machine_cost: Optional[Decimal]  # null without costing.manage
    overhead_cost: Optional[Decimal]  # null without costing.manage
    scrap_value: Decimal
    total_cost: Optional[Decimal]  # null without costing.manage
    scrap: List[ScrapOut]


def _entry_out(e, membership):
    used = batch_tracking.by_document(e.company, [e.number], -1)
    made = batch_tracking.by_document(e.company, [e.number], 1)
    return {
        'id': e.pk, 'number': e.number, 'status': e.status, 'status_label': e.get_status_display(), 'entry_date': e.entry_date,
        'item_code': e.item.code, 'item_name': e.item.name, 'unit': e.item.unit, 'bom_name': e.bom.name,
        'quantity_produced': e.quantity_produced, 'quantity_rejected': e.quantity_rejected,
        'source_warehouse': e.source_warehouse.name, 'target_warehouse': e.target_warehouse.name,
        'material_cost': e.material_cost, 'unit_cost': e.unit_cost, 'shift': e.shift, 'notes': e.notes,
        'cancel_reason': e.cancel_reason, 'recorded_by': _name(e.created_by),
        'work_order_id': e.work_order_id, 'work_order_number': e.work_order.number if e.work_order_id else '',
        'consumption': [{'item_code': c.item.code, 'item_name': c.item.name, 'unit': c.item.unit,
                         'planned_quantity': c.planned_quantity, 'quantity': c.quantity, 'rate': c.rate,
                         'batches': batch_rows(used.get((e.number, c.item_id)))}
                        for c in e.consumption.select_related('item')],
        'output_batches': batch_rows(made.get((e.number, e.item_id))),
        'machine_name': str(e.machine) if e.machine_id else '', 'machine_hours': e.machine_hours, 'labour_hours': e.labour_hours,
        **({'labour_cost': e.labour_cost, 'machine_cost': e.machine_cost, 'overhead_cost': e.overhead_cost,
            'total_cost': costing.total_cost(e)}
           if _sees_costs(membership) or not (e.labour_cost or e.machine_cost or e.overhead_cost) else
           {'labour_cost': None, 'machine_cost': None, 'overhead_cost': None, 'total_cost': None}),
        'scrap_value': e.scrap_value,
        'scrap': [{'item_id': s.item_id, 'item_code': s.item.code, 'item_name': s.item.name, 'unit': s.item.unit,
                   'quantity': s.quantity, 'rate': s.rate, 'value': costing.money(s.value)} for s in e.scrap.select_related('item')],
        'can_cancel': e.status == ProductionEntry.Status.POSTED and _writes_allowed(membership) and _can(membership, 'bom.manage'),
    }


def _entry(membership, entry_id):
    return get_object_or_404(ProductionEntry.objects.select_related('item', 'bom', 'source_warehouse', 'target_warehouse', 'created_by', 'work_order'),
                             pk=entry_id, company=membership.company)


@router.get('/production/entries', response=List[EntryOut])
def list_entries(request, item_id: Optional[int] = None):
    membership = membership_for(request, 'production.record')
    entries = ProductionEntry.objects.filter(company=membership.company).select_related(
        'item', 'bom', 'source_warehouse', 'target_warehouse', 'created_by', 'work_order')
    if item_id:
        entries = entries.filter(item_id=item_id)
    return [_entry_out(e, membership) for e in entries[:LIST_LIMIT]]


@router.post('/production/entries', response=EntryOut)
def record_entry(request, payload: EntryIn):
    membership = membership_for(request, 'production.record', write=True)
    entry = _run(services.record, membership, payload.dict())
    return _entry_out(_entry(membership, entry.pk), membership)


class ReasonIn(Schema):
    reason: str = ''


@router.post('/production/entries/{entry_id}/cancel', response=EntryOut)
def cancel_entry(request, entry_id: int, payload: ReasonIn):
    membership = membership_for(request, 'bom.manage', write=True)
    entry = _run(services.cancel, membership, _entry(membership, entry_id), payload.reason)
    return _entry_out(_entry(membership, entry.pk), membership)



# --- Work orders --------------------------------------------------------------------------------

class WorkOrderIn(Schema):
    item_id: int
    bom_id: Optional[int] = None
    quantity: Decimal
    due_date: Optional[date] = None
    source_warehouse_id: int
    target_warehouse_id: Optional[int] = None
    notes: str = ''


class WorkOrderRefOut(Schema):
    id: int
    number: str
    item_name: str
    quantity: Decimal
    status: str
    status_label: str


class MaterialOut(Schema):
    item_code: str
    item_name: str
    unit: str
    planned: Decimal
    used: Decimal
    on_hand: Decimal
    short: Decimal


class WorkOrderActionsOut(Schema):
    edit: bool
    release: bool
    record: bool
    close: bool
    cancel: bool


class WorkOrderOut(Schema):
    id: int
    number: str
    item_id: int
    item_code: str
    item_name: str
    unit: str
    bom_id: int
    bom_name: str
    quantity: Decimal
    due_date: Optional[date]
    overdue: bool
    source_warehouse_id: int
    source_warehouse: str
    target_warehouse: str
    status: str
    status_label: str
    notes: str
    close_reason: str
    parent: Optional[WorkOrderRefOut]
    children: List[WorkOrderRefOut]
    made: Decimal
    rejected: Decimal
    remaining: Decimal
    planned_unit_cost: Decimal
    actual_unit_cost: Decimal
    materials: List[MaterialOut]
    actions: WorkOrderActionsOut


def _ref(wo):
    return {'id': wo.pk, 'number': wo.number, 'item_name': wo.item.name, 'quantity': wo.quantity, 'status': wo.status,
            'status_label': wo.get_status_display()}


def _wo_out(wo, membership):
    from django.utils import timezone
    p = services.progress(wo)
    S = WorkOrder.Status
    writable = _writes_allowed(membership)
    manage = writable and _can(membership, 'work_order.manage')
    return {
        'id': wo.pk, 'number': wo.number, 'item_id': wo.item_id, 'item_code': wo.item.code, 'item_name': wo.item.name,
        'unit': wo.item.unit, 'bom_id': wo.bom_id, 'bom_name': wo.bom.name, 'quantity': wo.quantity, 'due_date': wo.due_date,
        'overdue': bool(wo.due_date and wo.due_date < timezone.localdate() and wo.status in (S.RELEASED, S.IN_PROGRESS)),
        'source_warehouse_id': wo.source_warehouse_id, 'source_warehouse': wo.source_warehouse.name,
        'target_warehouse': wo.target_warehouse.name, 'status': wo.status, 'status_label': wo.get_status_display(),
        'notes': wo.notes, 'close_reason': wo.close_reason, 'parent': _ref(wo.parent) if wo.parent_id else None,
        'children': [_ref(c) for c in wo.children.select_related('item')],
        'made': p['made'], 'rejected': p['rejected'], 'remaining': p['remaining'],
        'planned_unit_cost': p['planned_unit_cost'], 'actual_unit_cost': p['actual_unit_cost'],
        'materials': [{'item_code': m['item'].code, 'item_name': m['item'].name, 'unit': m['item'].unit, 'planned': m['planned'],
                       'used': m['used'], 'on_hand': m['on_hand'], 'short': m['short']} for m in p['materials']],
        'actions': {
            'edit': wo.status == S.DRAFT and manage, 'release': wo.status == S.DRAFT and manage,
            'record': wo.status in (S.RELEASED, S.IN_PROGRESS) and writable and _can(membership, 'production.record'),
            'close': wo.status == S.IN_PROGRESS and manage, 'cancel': wo.status in (S.DRAFT, S.RELEASED) and manage,
        },
    }


def _wo(membership, wo_id):
    return get_object_or_404(WorkOrder.objects.select_related('item', 'bom', 'source_warehouse', 'target_warehouse', 'parent__item'),
                             pk=wo_id, company=membership.company)


def _wo_viewer(request):
    membership = membership_for(request)
    if not (_can(membership, 'work_order.manage') or _can(membership, 'production.record')):
        raise HttpError(403, "Your role doesn't include work orders.")
    return membership


@router.get('/work-orders', response=List[WorkOrderOut])
def list_work_orders(request, status: str = 'open'):
    """status: 'open' (draft, released or in progress), a status, or 'all'."""
    membership = _wo_viewer(request)
    orders = WorkOrder.objects.filter(company=membership.company).select_related(
        'item', 'bom', 'source_warehouse', 'target_warehouse', 'parent__item')
    if status == 'open':
        orders = orders.filter(status__in=[WorkOrder.Status.DRAFT, WorkOrder.Status.RELEASED, WorkOrder.Status.IN_PROGRESS])
    elif status != 'all':
        orders = orders.filter(status=status)
    return [_wo_out(wo, membership) for wo in orders[:LIST_LIMIT]]


@router.get('/work-orders/{wo_id}', response=WorkOrderOut)
def get_work_order(request, wo_id: int):
    membership = _wo_viewer(request)
    return _wo_out(_wo(membership, wo_id), membership)


@router.post('/work-orders', response=WorkOrderOut)
def create_work_order(request, payload: WorkOrderIn):
    membership = membership_for(request, 'work_order.manage', write=True)
    wo = _run(services.save_work_order, membership, WorkOrder(), payload.dict())
    return _wo_out(_wo(membership, wo.pk), membership)


@router.put('/work-orders/{wo_id}', response=WorkOrderOut)
def update_work_order(request, wo_id: int, payload: WorkOrderIn):
    membership = membership_for(request, 'work_order.manage', write=True)
    wo = _run(services.save_work_order, membership, _wo(membership, wo_id), payload.dict())
    return _wo_out(_wo(membership, wo.pk), membership)


class ReleaseIn(Schema):
    make_subassemblies: bool = False


@router.post('/work-orders/{wo_id}/release', response=WorkOrderOut)
def release_work_order(request, wo_id: int, payload: ReleaseIn):
    membership = membership_for(request, 'work_order.manage', write=True)
    wo = _run(services.release, membership, _wo(membership, wo_id), payload.make_subassemblies)
    return _wo_out(_wo(membership, wo.pk), membership)


@router.post('/work-orders/{wo_id}/close', response=WorkOrderOut)
def close_work_order(request, wo_id: int, payload: ReasonIn):
    membership = membership_for(request, 'work_order.manage', write=True)
    wo = _run(services.close, membership, _wo(membership, wo_id), payload.reason)
    return _wo_out(_wo(membership, wo.pk), membership)


@router.post('/work-orders/{wo_id}/cancel', response=WorkOrderOut)
def cancel_work_order(request, wo_id: int, payload: ReasonIn):
    membership = membership_for(request, 'work_order.manage', write=True)
    wo = _run(services.cancel_work_order, membership, _wo(membership, wo_id), payload.reason)
    return _wo_out(_wo(membership, wo.pk), membership)


# --- Production costing ------------------------------------------------------------------------

def _costing(request, write=False):
    """Someone who may set cost rates and see costs, on a plan with production costing."""
    return membership_for(request, 'costing.manage', write=write)


def _cost_run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except costing.CostingError as exc:
        raise HttpError(400, str(exc))


class CostSettingsIn(Schema):
    labour_hour_rate: Decimal
    overhead_percent: Decimal


class CostSettingsOut(Schema):
    labour_hour_rate: Decimal
    overhead_percent: Decimal


@router.get('/production/cost-settings', response=CostSettingsOut)
def get_cost_settings(request):
    s = costing.settings_for(_costing(request).company)
    return {'labour_hour_rate': s.labour_hour_rate, 'overhead_percent': s.overhead_percent}


@router.put('/production/cost-settings', response=CostSettingsOut)
def put_cost_settings(request, payload: CostSettingsIn):
    s = _cost_run(costing.save_settings, _costing(request, write=True).company, payload.dict())
    return {'labour_hour_rate': s.labour_hour_rate, 'overhead_percent': s.overhead_percent}


class MachineIn(Schema):
    code: str
    name: str
    hour_rate: Decimal = Decimal('0')
    is_active: Optional[bool] = None


class MachineOut(Schema):
    id: int
    code: str
    name: str
    # Null for people who can't see costs.
    hour_rate: Optional[Decimal]
    is_active: bool
    used: bool


def _machine_out(m, show_rate):
    return {'id': m.pk, 'code': m.code, 'name': m.name, 'hour_rate': m.hour_rate if show_rate else None,
            'is_active': m.is_active, 'used': m.entries.exists()}


@router.get('/machines', response=List[MachineOut])
def list_machines(request):
    """Machines (work centres), for routing and production entries."""
    membership = membership_for(request)
    show = _can(membership, 'costing.manage')
    return [_machine_out(m, show) for m in Machine.objects.filter(company=membership.company)]


@router.post('/machines', response=MachineOut)
def create_machine(request, payload: MachineIn):
    company = _costing(request, write=True).company
    return _machine_out(_cost_run(costing.save_machine, company, Machine(), payload.dict()), True)


@router.put('/machines/{machine_id}', response=MachineOut)
def update_machine(request, machine_id: int, payload: MachineIn):
    company = _costing(request, write=True).company
    machine = get_object_or_404(Machine, pk=machine_id, company=company)
    return _machine_out(_cost_run(costing.save_machine, company, machine, payload.dict()), True)


@router.delete('/machines/{machine_id}', response={204: None})
def delete_machine(request, machine_id: int):
    company = _costing(request, write=True).company
    _cost_run(costing.delete_machine, get_object_or_404(Machine, pk=machine_id, company=company))
    return 204, None


class CostRowOut(Schema):
    # material, labour, machine, overhead, scrap (a credit) or total.
    part: str
    standard: Decimal
    actual: Decimal
    variance: Decimal


class CostSheetOut(Schema):
    good: Decimal
    rejected: Decimal
    entries: int
    labour_hours: Decimal
    machine_hours: Decimal
    yield_percent: Optional[Decimal]
    standard_unit_cost: Decimal
    actual_unit_cost: Optional[Decimal]
    rows: List[CostRowOut]


@router.get('/work-orders/{wo_id}/costs', response=CostSheetOut)
def work_order_costs(request, wo_id: int):
    """Standard against actual for what the work order has made so far."""
    membership = _costing(request)
    return costing.work_order_sheet(_wo(membership, wo_id))


class CostReportRowOut(CostSheetOut):
    item_id: int
    item_code: str
    item_name: str
    unit: str
    scrap_quantity: Decimal


@router.get('/production/costs', response=List[CostReportRowOut])
def production_costs(request, start: date, end: date):
    """Per item made between start and end: actual cost against standard, yield and scrap."""
    if end < start:
        raise HttpError(400, "The end date is before the start.")
    return costing.report(_costing(request).company, start, end)
