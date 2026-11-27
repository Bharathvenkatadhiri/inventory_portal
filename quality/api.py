"""/api/v1 endpoints for QC: lots waiting for inspection, and recording
an inspection. Seeing lots needs inspection.record or stock.view;
recording needs inspection.record (the QC and QC Inspector roles)."""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from organizations import access
from inventory import batches as batch_tracking
from inventory.api import BatchQtyOut, batch_rows
from organizations.app_api import membership_for
from purchase.api import _name, _writes_allowed

from . import services
from .models import InspectionLot

router = Router(tags=['qc'])
Lot = InspectionLot
LIST_LIMIT = 500


def _viewer(request):
    membership = membership_for(request)
    if not (access.check(membership, 'inspection.record') or access.check(membership, 'stock.view')):
        raise HttpError(403, "Your role doesn't include QC.")
    return membership


class InspectionOut(Schema):
    inspected_on: date
    accepted: Decimal
    rejected: Decimal
    remarks: str
    inspector: str


class LotOut(Schema):
    id: int
    number: str
    source: str
    source_label: str
    reference: str
    supplier: str
    item_id: int
    item_code: str
    item_name: str
    unit: str
    quantity: Decimal
    accepted_quantity: Decimal
    rejected_quantity: Decimal
    pending_quantity: Decimal
    # 'none', 'batch' or 'serial'; batches: what's still waiting, per batch or serial number.
    tracking: str
    batches: List[BatchQtyOut]
    destination: str
    status: str
    status_label: str
    received_on: date
    inspections: List[InspectionOut]
    can_inspect: bool


def _lot_out(lot, membership):
    waiting = []
    if lot.item.tracked and lot.pending_quantity:
        waiting = batch_tracking.stock_by_batch(lot.company, lot.item, services.hold(lot.company),
                                               within=list(lot.batches.values_list('pk', flat=True)))
    return {
        'id': lot.pk, 'number': lot.number, 'source': lot.source, 'source_label': lot.get_source_display(),
        'reference': lot.reference, 'supplier': (lot.supplier.name or lot.supplier.legal_name) if lot.supplier else '',
        'item_id': lot.item_id, 'item_code': lot.item.code, 'item_name': lot.item.name, 'unit': lot.item.unit,
        'quantity': lot.quantity, 'accepted_quantity': lot.accepted_quantity, 'rejected_quantity': lot.rejected_quantity,
        'pending_quantity': lot.pending_quantity, 'destination': lot.destination.name, 'status': lot.status,
        'tracking': lot.item.tracking, 'batches': batch_rows(waiting),
        'status_label': lot.get_status_display(), 'received_on': lot.received_on,
        'inspections': [{'inspected_on': i.inspected_on, 'accepted': i.accepted, 'rejected': i.rejected, 'remarks': i.remarks,
                         'inspector': _name(i.inspector)} for i in lot.inspections.select_related('inspector')],
        'can_inspect': lot.status in (Lot.Status.PENDING, Lot.Status.PARTLY) and _writes_allowed(membership)
                       and bool(access.check(membership, 'inspection.record')),
    }


@router.get('/qc/lots', response=List[LotOut])
def list_lots(request, status: str = 'open', item_id: Optional[int] = None):
    """status: 'open' (waiting or partly inspected), 'done', or 'all'."""
    membership = _viewer(request)
    lots = Lot.objects.filter(company=membership.company).select_related('item', 'destination', 'supplier')
    if status == 'open':
        lots = lots.filter(status__in=[Lot.Status.PENDING, Lot.Status.PARTLY])
    elif status != 'all':
        lots = lots.filter(status=status)
    if item_id:
        lots = lots.filter(item_id=item_id)
    return [_lot_out(lot, membership) for lot in lots[:LIST_LIMIT]]


class InspectIn(Schema):
    accepted: Decimal = Decimal('0')
    rejected: Decimal = Decimal('0')
    inspected_on: Optional[date] = None
    remarks: str = ''
    # Serial-numbered items: the units that failed (the rest of those inspected pass).
    rejected_serials: List[str] = []


@router.post('/qc/lots/{lot_id}/inspections', response=LotOut)
def inspect(request, lot_id: int, payload: InspectIn):
    membership = membership_for(request, 'inspection.record', write=True)
    lot = get_object_or_404(Lot, pk=lot_id, company=membership.company)
    try:
        lot = services.inspect(membership, lot, payload.dict())
    except services.QualityError as exc:
        raise HttpError(400, str(exc))
    return _lot_out(Lot.objects.select_related('item', 'destination', 'supplier').get(pk=lot.pk), membership)
