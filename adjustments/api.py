"""/api/v1 endpoints for credit and debit notes. Notes issued to customers
need invoice.create; supplier notes recorded against bills need
purchase_bill.create. Seeing them needs a sales role (issued) or a
purchase role (received)."""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from inventory.api import ChoiceOut
from organizations import access
from organizations.app_api import membership_for
from purchase.api import VIEW_PERMISSIONS as PURCHASE_ROLES
from purchase.api import PartyOut, _name, _party, _totals, _writes_allowed
from sales.api import VIEW_PERMISSIONS as SALES_ROLES

from . import services
from .models import CreditDebitNote

router = Router(tags=['notes'])
Note, Side, Kind, Status = CreditDebitNote, CreditDebitNote.Side, CreditDebitNote.Kind, CreditDebitNote.Status
WRITE_PERMISSION = {Side.SALES: 'invoice.create', Side.PURCHASE: 'purchase_bill.create'}
VIEW = {Side.SALES: SALES_ROLES, Side.PURCHASE: PURCHASE_ROLES}
LIST_LIMIT = 500


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.NoteError as exc:
        raise HttpError(400, str(exc))


def _side(value):
    if value not in Side.values:
        raise HttpError(400, "side must be 'sales' or 'purchase'.")
    return value


def _can_see(membership, side):
    return any(access.check(membership, code) for code in VIEW[side])


def _viewer(request, side):
    membership = membership_for(request)
    if not _can_see(membership, side):
        raise HttpError(403, "Your role doesn't include these notes.")
    return membership


# --- What a new note can cover -------------------------------------------------------------

class RoomLineOut(Schema):
    line_id: int
    item_code: str
    item_name: str
    hsn_code: str
    quantity: Decimal
    unit: str
    rate: Decimal
    gst_rate: Decimal
    taxable_value: Decimal
    returnable: Decimal
    creditable: Decimal


class NoteRoomOut(Schema):
    document_id: int
    document_number: str
    document_date: date
    party: PartyOut
    is_interstate: bool
    credit_deadline: Optional[date]
    credit_reasons: List[ChoiceOut]
    debit_reasons: List[ChoiceOut]
    lines: List[RoomLineOut]


def _reasons(values):
    return [{'value': v, 'label': Note.Reason(v).label} for v in values]


@router.get('/notes/room', response=NoteRoomOut)
def note_room(request, side: str, document_id: int):
    side = _side(side)
    membership = _viewer(request, side)
    doc = _run(services._document, membership.company, side, document_id)
    out = []
    for r in services.room(side, doc):
        l = r['line']
        out.append({'line_id': l.pk, 'item_code': l.item.code, 'item_name': l.item.name,
                    'hsn_code': getattr(l, 'hsn_code', '') or l.item.hsn_code, 'quantity': l.quantity,
                    'unit': services.original_unit(l), 'rate': l.rate, 'gst_rate': l.gst_rate, 'taxable_value': l.taxable_value,
                    'returnable': r['returnable'], 'creditable': r['creditable']})
    return {
        'document_id': doc.pk, 'document_number': doc.number if side == Side.SALES else doc.supplier_invoice_number,
        'document_date': doc.invoice_date, 'party': _party(doc.customer if side == Side.SALES else doc.supplier),
        'is_interstate': doc.is_interstate,
        'credit_deadline': services.credit_note_deadline(doc.invoice_date) if side == Side.SALES else None,
        'credit_reasons': _reasons(services.CREDIT_REASONS), 'debit_reasons': _reasons(services.DEBIT_REASONS), 'lines': out,
    }


# --- Notes -------------------------------------------------------------------------------------

class NoteLineIn(Schema):
    line_id: int
    quantity: Decimal = Decimal('0')
    taxable_value: Decimal = Decimal('0')


class NoteIn(Schema):
    side: str
    kind: str
    document_id: int
    reason: str
    note_date: Optional[date] = None
    warehouse_id: Optional[int] = None
    party_note_number: str = ''
    remarks: str = ''
    lines: List[NoteLineIn]


class NoteLineOut(Schema):
    item_code: str
    item_name: str
    hsn_code: str
    quantity: Decimal
    unit: str
    rate: Decimal
    gst_rate: Decimal
    taxable_value: Decimal
    tax_amount: Decimal


class NoteActionsOut(Schema):
    edit: bool
    post: bool
    cancel: bool


class NoteOut(Schema):
    id: int
    side: str
    kind: str
    kind_label: str
    number: str
    party_note_number: str
    status: str
    status_label: str
    reason: str
    reason_label: str
    note_date: date
    party: PartyOut
    document_id: int
    document_number: str
    document_date: date
    warehouse: str
    remarks: str
    cancel_reason: str
    place_of_supply: str
    is_interstate: bool
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal
    total: Decimal
    created_by: str
    posted_by: str
    lines: List[NoteLineOut]
    actions: NoteActionsOut


def _note_out(n, membership):
    doc = n.document
    can = _writes_allowed(membership) and bool(access.check(membership, WRITE_PERMISSION[n.side]))
    return {
        **_totals(n), 'id': n.pk, 'side': n.side, 'kind': n.kind, 'kind_label': n.get_kind_display(), 'number': n.number,
        'party_note_number': n.party_note_number, 'status': n.status, 'status_label': n.get_status_display(),
        'reason': n.reason, 'reason_label': n.get_reason_display(), 'note_date': n.note_date, 'party': _party(n.party),
        'document_id': doc.pk, 'document_number': doc.number if n.side == Side.SALES else doc.supplier_invoice_number,
        'document_date': doc.invoice_date, 'warehouse': n.warehouse.name if n.warehouse else '', 'remarks': n.remarks,
        'cancel_reason': n.cancel_reason, 'created_by': _name(n.created_by), 'posted_by': _name(n.posted_by),
        'lines': [{'item_code': l.item.code, 'item_name': l.item.name, 'hsn_code': l.hsn_code, 'quantity': l.quantity,
                   'unit': l.unit, 'rate': l.rate, 'gst_rate': l.gst_rate, 'taxable_value': l.taxable_value,
                   'tax_amount': l.tax_amount} for l in n.lines.select_related('item')],
        'actions': {'edit': n.status == Status.DRAFT and can, 'post': n.status == Status.DRAFT and can,
                    'cancel': n.status == Status.POSTED and can},
    }


def _note(membership, note_id):
    note = get_object_or_404(Note.objects.select_related('party', 'warehouse', 'sales_invoice', 'purchase_bill', 'created_by', 'posted_by'),
                             pk=note_id, company=membership.company)
    if not _can_see(membership, note.side):
        raise HttpError(403, "Your role doesn't include these notes.")
    return note


@router.get('/notes', response=List[NoteOut])
def list_notes(request, side: str, status: str = '', document_id: Optional[int] = None):
    side = _side(side)
    membership = _viewer(request, side)
    notes = Note.objects.filter(company=membership.company, side=side).select_related(
        'party', 'warehouse', 'sales_invoice', 'purchase_bill', 'created_by', 'posted_by')
    if status:
        notes = notes.filter(status=status)
    if document_id:
        notes = notes.filter(**{('sales_invoice_id' if side == Side.SALES else 'purchase_bill_id'): document_id})
    return [_note_out(n, membership) for n in notes[:LIST_LIMIT]]


@router.get('/notes/{note_id}', response=NoteOut)
def get_note(request, note_id: int):
    membership = membership_for(request)
    return _note_out(_note(membership, note_id), membership)


@router.post('/notes', response=NoteOut)
def create_note(request, payload: NoteIn):
    membership = membership_for(request, WRITE_PERMISSION[_side(payload.side)], write=True)
    note = _run(services.save_note, membership, Note(), payload.dict())
    return _note_out(_note(membership, note.pk), membership)


def _writer(request, note_id):
    membership = membership_for(request, write=True)
    note = _note(membership, note_id)
    if not access.check(membership, WRITE_PERMISSION[note.side]):
        raise HttpError(403, "Your role can't change these notes.")
    return membership, note


@router.put('/notes/{note_id}', response=NoteOut)
def update_note(request, note_id: int, payload: NoteIn):
    membership, note = _writer(request, note_id)
    note = _run(services.save_note, membership, note, payload.dict())
    return _note_out(_note(membership, note.pk), membership)


@router.delete('/notes/{note_id}', response={204: None})
def delete_note(request, note_id: int):
    membership, note = _writer(request, note_id)
    _run(services.delete_note, note)
    return 204, None


@router.post('/notes/{note_id}/post', response=NoteOut)
def post_note(request, note_id: int):
    membership, note = _writer(request, note_id)
    note = _run(services.post_note, membership, note)
    return _note_out(_note(membership, note.pk), membership)


class ReasonIn(Schema):
    reason: str = ''


@router.post('/notes/{note_id}/cancel', response=NoteOut)
def cancel_note(request, note_id: int, payload: ReasonIn):
    membership, note = _writer(request, note_id)
    note = _run(services.cancel_note, membership, note, payload.reason)
    return _note_out(_note(membership, note.pk), membership)
