"""/api/v1 endpoints for e-invoices (IRN) and e-way bills, on the
organization the request is for. Registering and cancelling an IRN needs
invoice.create; e-way bills need dispatch.post (delivery challans) or
job_work.manage (job work challans). The register needs a sales or
accounts role to see."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from adjustments.models import CreditDebitNote
from jobwork.models import JobWorkChallan
from organizations import access
from organizations.app_api import membership_for
from purchase.api import _name, _writes_allowed
from sales.api import VIEW_PERMISSIONS as SALES_ROLES
from sales.models import Dispatch, SalesInvoice

from . import services
from .models import EInvoice, EwayBill
from .providers import CANCEL_WINDOW

router = Router(tags=['einvoice'])


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.EInvoiceError as exc:
        raise HttpError(400, str(exc))


def _can(membership, code):
    return bool(access.check(membership, code))


def _writer(request):
    membership = membership_for(request)
    if not _writes_allowed(membership):
        raise HttpError(402, "Your subscription has lapsed, so changes are off. Renew it under Settings, Billing.")
    return membership


# --- E-invoices -------------------------------------------------------------------------------------

class EInvoiceOut(Schema):
    id: int
    document_type: str
    document_number: str
    irn: str
    ack_number: str
    ack_date: datetime
    signed_qr: str
    status: str
    status_label: str
    cancel_reason_label: str
    cancel_remark: str
    cancelled_at: Optional[datetime]
    can_cancel: bool
    provider: str
    registered_by: str


class EInvoiceStateOut(Schema):
    # Whether e-invoicing applies to this company (plan + turned on in settings).
    applicable: bool
    can_register: bool
    current: Optional[EInvoiceOut]
    history: List[EInvoiceOut]


def _einvoice_out(e, membership):
    return {
        'id': e.pk, 'document_type': e.document_type, 'document_number': e.document_number, 'irn': e.irn, 'ack_number': e.ack_number,
        'ack_date': e.ack_date, 'signed_qr': e.signed_qr, 'status': e.status, 'status_label': e.get_status_display(),
        'cancel_reason_label': e.get_cancel_reason_display() if e.cancel_reason else '', 'cancel_remark': e.cancel_remark,
        'cancelled_at': e.cancelled_at, 'provider': e.provider, 'registered_by': _name(e.created_by),
        'can_cancel': (e.status == EInvoice.Status.ACTIVE and timezone.now() - e.ack_date <= CANCEL_WINDOW
                       and _writes_allowed(membership) and _can(membership, 'invoice.create')),
    }


def _state(document, membership):
    field = 'note' if isinstance(document, CreditDebitNote) else 'sales_invoice'
    history = list(EInvoice.objects.filter(**{field: document}).select_related('created_by'))
    current = next((e for e in history if e.status == EInvoice.Status.ACTIVE), None)
    applicable = services.einvoicing_on(membership.company)
    party = document.party if isinstance(document, CreditDebitNote) else document.customer
    return {
        'applicable': applicable,
        'can_register': (applicable and current is None and document.status == 'posted' and bool(party.gstin)
                         and _writes_allowed(membership) and _can(membership, 'invoice.create')),
        'current': _einvoice_out(current, membership) if current else None,
        'history': [_einvoice_out(e, membership) for e in history if e is not current],
    }


def _invoice(membership, invoice_id):
    return get_object_or_404(SalesInvoice.objects.select_related('company', 'customer'), pk=invoice_id, company=membership.company)


def _note(membership, note_id):
    return get_object_or_404(CreditDebitNote.objects.select_related('company', 'party'), pk=note_id, company=membership.company,
                             side=CreditDebitNote.Side.SALES)


def _viewer(request):
    membership = membership_for(request)
    if not any(_can(membership, c) for c in SALES_ROLES):
        raise HttpError(403, "Your role doesn't include this.")
    return membership


class IrnCancelIn(Schema):
    # 1 duplicate, 2 data entry mistake, 3 order cancelled, 4 other.
    reason: str
    remark: str = ''


@router.get('/sales-invoices/{invoice_id}/einvoice', response=EInvoiceStateOut)
def invoice_einvoice(request, invoice_id: int):
    membership = _viewer(request)
    return _state(_invoice(membership, invoice_id), membership)


@router.post('/sales-invoices/{invoice_id}/einvoice', response=EInvoiceStateOut)
def register_invoice(request, invoice_id: int):
    """Registers the invoice with the IRP: its IRN and signed QR code."""
    membership = _writer(request)
    invoice = _invoice(membership, invoice_id)
    _run(services.register, membership, invoice)
    return _state(invoice, membership)


@router.post('/sales-invoices/{invoice_id}/einvoice/cancel', response=EInvoiceStateOut)
def cancel_invoice_irn(request, invoice_id: int, payload: IrnCancelIn):
    """Cancels the IRN (within 24 hours). The invoice itself can then be cancelled."""
    membership = _writer(request)
    invoice = _invoice(membership, invoice_id)
    current = services.active_irn(invoice)
    if current is None:
        raise HttpError(400, "This invoice has no IRN.")
    _run(services.cancel_irn, membership, current, payload.reason, payload.remark)
    return _state(invoice, membership)


@router.get('/notes/{note_id}/einvoice', response=EInvoiceStateOut)
def note_einvoice(request, note_id: int):
    membership = _viewer(request)
    return _state(_note(membership, note_id), membership)


@router.post('/notes/{note_id}/einvoice', response=EInvoiceStateOut)
def register_note(request, note_id: int):
    membership = _writer(request)
    note = _note(membership, note_id)
    _run(services.register, membership, note)
    return _state(note, membership)


@router.post('/notes/{note_id}/einvoice/cancel', response=EInvoiceStateOut)
def cancel_note_irn(request, note_id: int, payload: IrnCancelIn):
    membership = _writer(request)
    note = _note(membership, note_id)
    current = services.active_irn(note)
    if current is None:
        raise HttpError(400, "This note has no IRN.")
    _run(services.cancel_irn, membership, current, payload.reason, payload.remark)
    return _state(note, membership)


# --- E-way bills -------------------------------------------------------------------------------------

class EwbVehicleOut(Schema):
    vehicle_number: str
    from_place: str
    reason_label: str
    updated_at: datetime


class EwayBillOut(Schema):
    id: int
    number: str
    status: str
    status_label: str
    generated_at: datetime
    valid_until: Optional[datetime]
    expired: bool
    document_number: str
    value: Decimal
    distance_km: int
    transport_mode: str
    transport_mode_label: str
    vehicle_type: str
    vehicle_number: str
    transporter_id: str
    transporter_name: str
    transport_document: str
    from_pincode: str
    to_pincode: str
    cancel_reason_label: str
    cancel_remark: str
    vehicles: List[EwbVehicleOut]
    can_update_vehicle: bool
    can_cancel: bool
    provider: str
    generated_by: str


class EwbStateOut(Schema):
    needed: bool
    why: str
    value: Decimal
    can_generate: bool
    current: Optional[EwayBillOut]
    history: List[EwayBillOut]


def _ewb_out(e, membership, code):
    now = timezone.now()
    live = e.status == EwayBill.Status.ACTIVE
    allowed = _writes_allowed(membership) and _can(membership, code)
    return {
        'id': e.pk, 'number': e.number, 'status': e.status, 'status_label': e.get_status_display(), 'generated_at': e.generated_at,
        'valid_until': e.valid_until, 'expired': bool(live and e.valid_until and e.valid_until < now),
        'document_number': e.document_number, 'value': e.value, 'distance_km': e.distance_km, 'transport_mode': e.transport_mode,
        'transport_mode_label': e.get_transport_mode_display(), 'vehicle_type': e.vehicle_type, 'vehicle_number': e.vehicle_number,
        'transporter_id': e.transporter_id, 'transporter_name': e.transporter_name, 'transport_document': e.transport_document,
        'from_pincode': e.from_pincode, 'to_pincode': e.to_pincode,
        'cancel_reason_label': e.get_cancel_reason_display() if e.cancel_reason else '', 'cancel_remark': e.cancel_remark,
        'vehicles': [{'vehicle_number': v.vehicle_number, 'from_place': v.from_place, 'reason_label': v.get_reason_display(),
                      'updated_at': v.updated_at} for v in e.vehicles.all()],
        'can_update_vehicle': live and allowed and not (e.valid_until and e.valid_until < now),
        'can_cancel': live and allowed and now - e.generated_at <= CANCEL_WINDOW,
        'provider': e.provider, 'generated_by': _name(e.created_by),
    }


def _ewb_state(document, membership, code):
    field = 'job_work_challan' if isinstance(document, JobWorkChallan) else 'dispatch'
    history = list(EwayBill.objects.filter(**{field: document}).select_related('created_by').prefetch_related('vehicles'))
    current = next((e for e in history if e.status == EwayBill.Status.ACTIVE), None)
    needed, value, why = services.needed(document)
    return {
        'needed': needed, 'why': why, 'value': value,
        'can_generate': current is None and _writes_allowed(membership) and _can(membership, code)
                        and getattr(document, 'status', 'open') != 'cancelled',
        'current': _ewb_out(current, membership, code) if current else None,
        'history': [_ewb_out(e, membership, code) for e in history if e is not current],
    }


class EwbIn(Schema):
    distance_km: int
    transport_mode: str = '1'      # 1 road, 2 rail, 3 air, 4 ship
    vehicle_type: str = 'R'        # R regular, O over-dimensional cargo
    vehicle_number: str = ''       # Part B; can be added later
    transporter_id: str = ''
    transporter_name: str = ''
    transport_document: str = ''   # for rail, air and ship
    transport_document_date: Optional[date] = None


class EwbVehicleIn(Schema):
    vehicle_number: str
    from_place: str = ''
    reason: str = ''               # 1 breakdown, 2 transshipment, 3 other (not needed for the first)
    remark: str = ''


class EwbCancelIn(Schema):
    # 1 duplicate, 2 order cancelled, 3 data entry mistake, 4 other.
    reason: str
    remark: str = ''


def _dispatch(membership, dispatch_id):
    return get_object_or_404(Dispatch.objects.select_related('company', 'order__customer', 'warehouse__address'),
                             pk=dispatch_id, company=membership.company)


def _challan(membership, challan_id):
    return get_object_or_404(JobWorkChallan.objects.select_related('company', 'job_worker', 'from_warehouse__address'),
                             pk=challan_id, company=membership.company)


def _ewb_endpoints(prefix, loader, code):
    """GET/POST {prefix}/{id}/eway-bill, .../vehicle and .../cancel for a kind of document."""

    def state(request, document_id: int):
        membership = membership_for(request)
        if not (_can(membership, code) or any(_can(membership, c) for c in SALES_ROLES)):
            raise HttpError(403, "Your role doesn't include this.")
        return _ewb_state(loader(membership, document_id), membership, code)

    def generate(request, document_id: int, payload: EwbIn):
        membership = _writer(request)
        document = loader(membership, document_id)
        _run(services.generate_ewb, membership, document, payload.dict())
        return _ewb_state(loader(membership, document_id), membership, code)

    def vehicle(request, document_id: int, payload: EwbVehicleIn):
        membership = _writer(request)
        document = loader(membership, document_id)
        current = services.active_ewb(document)
        if current is None:
            raise HttpError(400, "There's no e-way bill to update.")
        _run(services.update_vehicle, membership, current, payload.dict())
        return _ewb_state(document, membership, code)

    def cancel(request, document_id: int, payload: EwbCancelIn):
        membership = _writer(request)
        document = loader(membership, document_id)
        current = services.active_ewb(document)
        if current is None:
            raise HttpError(400, "There's no e-way bill to cancel.")
        _run(services.cancel_ewb, membership, current, payload.reason, payload.remark)
        return _ewb_state(document, membership, code)

    name = prefix.strip('/').replace('-', '_')
    for fn, verb in ((state, 'get'), (generate, 'post')):
        fn.__name__ = f"{name}_eway_bill_{verb}"
        getattr(router, verb)(f"{prefix}/{{document_id}}/eway-bill", response=EwbStateOut, operation_id=fn.__name__)(fn)
    vehicle.__name__, cancel.__name__ = f"{name}_eway_bill_vehicle", f"{name}_eway_bill_cancel"
    router.post(f"{prefix}/{{document_id}}/eway-bill/vehicle", response=EwbStateOut, operation_id=vehicle.__name__)(vehicle)
    router.post(f"{prefix}/{{document_id}}/eway-bill/cancel", response=EwbStateOut, operation_id=cancel.__name__)(cancel)


_ewb_endpoints('/dispatches', _dispatch, 'dispatch.post')
_ewb_endpoints('/job-work', _challan, 'job_work.manage')


# --- The register ------------------------------------------------------------------------------------

class GstPortalRowOut(Schema):
    kind: str            # einvoice or eway_bill
    id: int
    document_kind: str   # invoice, note, dispatch or job_work
    document_id: int
    document_number: str
    party: str
    number: str          # IRN or e-way bill number
    date: datetime
    valid_until: Optional[datetime]
    status: str
    status_label: str
    value: Decimal


class GstPortalPendingOut(Schema):
    document_kind: str
    document_id: int
    document_number: str
    party: str
    date: date
    value: Decimal
    why: str


class GstPortalRegisterOut(Schema):
    einvoicing_on: bool
    provider: str
    rows: List[GstPortalRowOut]
    pending: List[GstPortalPendingOut]


@router.get('/gst-portal/register', response=GstPortalRegisterOut)
def register_list(request):
    """Everything registered with the GST portals, newest first, and what still needs it."""
    from .providers import get_provider

    membership = _viewer(request)
    company = membership.company
    rows = []
    for e in EInvoice.objects.filter(company=company).select_related('sales_invoice__customer', 'note__party')[:300]:
        doc = e.sales_invoice or e.note
        party = doc.customer if e.sales_invoice_id else doc.party
        rows.append({'kind': 'einvoice', 'id': e.pk, 'document_kind': 'invoice' if e.sales_invoice_id else 'note',
                     'document_id': doc.pk, 'document_number': e.document_number, 'party': party.name or party.legal_name,
                     'number': e.irn, 'date': e.ack_date, 'valid_until': None, 'status': e.status, 'status_label': e.get_status_display(),
                     'value': doc.total})
    for w in EwayBill.objects.filter(company=company).select_related('dispatch__order__customer', 'job_work_challan__job_worker')[:300]:
        doc = w.dispatch or w.job_work_challan
        party = w.dispatch.order.customer if w.dispatch_id else w.job_work_challan.job_worker
        rows.append({'kind': 'eway_bill', 'id': w.pk, 'document_kind': 'dispatch' if w.dispatch_id else 'job_work',
                     'document_id': doc.pk, 'document_number': w.document_number, 'party': party.name or party.legal_name,
                     'number': w.number, 'date': w.generated_at, 'valid_until': w.valid_until, 'status': w.status,
                     'status_label': w.get_status_display(), 'value': w.value})
    rows.sort(key=lambda r: r['date'], reverse=True)

    pending = []
    recent = timezone.localdate() - timedelta(days=30)  # what's still worth chasing
    if services.einvoicing_on(company):
        registered = set(EInvoice.objects.filter(company=company, status=EInvoice.Status.ACTIVE, sales_invoice__isnull=False)
                         .values_list('sales_invoice_id', flat=True))
        for inv in SalesInvoice.objects.filter(company=company, status='posted', invoice_date__gte=recent).select_related('customer'):
            if inv.pk not in registered and inv.customer.gstin:
                pending.append({'document_kind': 'invoice', 'document_id': inv.pk, 'document_number': inv.number,
                                'party': inv.customer.name or inv.customer.legal_name, 'date': inv.invoice_date, 'value': inv.total,
                                'why': "B2B invoice without an IRN"})
    with_ewb = set(EwayBill.objects.filter(company=company, status=EwayBill.Status.ACTIVE, dispatch__isnull=False)
                   .values_list('dispatch_id', flat=True))
    for d in Dispatch.objects.filter(company=company, dispatch_date__gte=recent).select_related('order__customer'):
        if d.pk in with_ewb:
            continue
        needed, value, why = services.needed(d)
        if needed:
            pending.append({'document_kind': 'dispatch', 'document_id': d.pk, 'document_number': d.number,
                            'party': d.order.customer.name or d.order.customer.legal_name, 'date': d.dispatch_date, 'value': value,
                            'why': why})
    with_jw = set(EwayBill.objects.filter(company=company, status=EwayBill.Status.ACTIVE, job_work_challan__isnull=False)
                  .values_list('job_work_challan_id', flat=True))
    for c in JobWorkChallan.objects.filter(company=company, challan_date__gte=recent).exclude(status='cancelled').select_related('job_worker'):
        if c.pk in with_jw:
            continue
        needed, value, why = services.needed(c)
        if needed:
            pending.append({'document_kind': 'job_work', 'document_id': c.pk, 'document_number': c.number,
                            'party': c.job_worker.name or c.job_worker.legal_name, 'date': c.challan_date, 'value': value, 'why': why})
    return {'einvoicing_on': services.einvoicing_on(company), 'provider': get_provider().name, 'rows': rows, 'pending': pending}
