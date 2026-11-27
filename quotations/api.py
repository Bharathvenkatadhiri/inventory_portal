"""/api/v1 endpoints for quotations (we quote a customer) and RFQs (we ask
suppliers to quote), on the organization the request is for.

- Sales quotations need a sales role to see (sales.api.VIEW_PERMISSIONS)
  and quotation.create to change; the customer sees sent ones under
  /purchase/quotations-received and can accept or decline (po.create).
- RFQs need a purchase role to see and po.create to change; suppliers
  asked see them under /sales/rfqs-received and quote (quotation.create).
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.db.models import Q as Where
from django.http import Http404
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from organizations import access
from organizations.app_api import membership_for
from purchase.api import VIEW_PERMISSIONS as PURCHASE_ROLES
from purchase.api import PartyOut, TotalsOut, _name, _party, _writes_allowed
from sales.api import VIEW_PERMISSIONS as SALES_ROLES

from . import services
from .models import Quotation, Rfq, RfqSupplier

router = Router(tags=['quotations'])
LIST_LIMIT = 500
Q = Quotation


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.QuotationError as exc:
        raise HttpError(400, str(exc))


def _can(membership, code, counterparty=None):
    return bool(access.check(membership, code, counterparty))


def _viewer(request, roles):
    membership = membership_for(request)
    if not any(_can(membership, code) for code in roles):
        raise HttpError(403, "Your role doesn't include this.")
    return membership


def _writer(request):
    membership = membership_for(request)
    if not _writes_allowed(membership):
        raise HttpError(402, "Your subscription has lapsed, so changes are off. Renew it under Settings, Billing.")
    return membership


# --- Sales quotations ------------------------------------------------------------------------

class QuoteLineIn(Schema):
    item_id: int
    quantity: Decimal
    rate: Optional[Decimal] = None       # left out: the item's sale price
    gst_rate: Optional[Decimal] = None   # left out: the item's GST rate
    description: str = ''


class QuotationIn(Schema):
    customer_id: int
    dispatch_from_id: int
    quote_date: Optional[date] = None
    valid_until: Optional[date] = None   # left out: 30 days
    enquiry_reference: str = ''
    delivery_days: Optional[int] = None
    payment_terms_days: Optional[int] = None
    terms: str = ''
    notes: str = ''
    lines: List[QuoteLineIn]


class QuoteLineOut(Schema):
    id: int
    item_id: int
    item_code: str
    item_name: str
    hsn_code: str
    description: str
    quantity: Decimal
    unit: str
    rate: Decimal
    gst_rate: Decimal
    taxable_value: Decimal
    tax_amount: Decimal


class QuoteActionsOut(Schema):
    edit: bool
    send: bool
    revise: bool
    record_response: bool
    convert: bool
    cancel: bool
    respond: bool  # the customer can accept or decline


class QuoteRevisionOut(Schema):
    id: int
    number: str
    status: str
    status_label: str


class QuotationOut(Schema):
    id: int
    number: str
    revision: int
    status: str
    status_label: str
    expired: bool
    seller: PartyOut
    customer: PartyOut
    quote_date: date
    valid_until: date
    enquiry_reference: str
    dispatch_from_id: int
    dispatch_from: str
    delivery_days: Optional[int]
    payment_terms_days: Optional[int]
    terms: str
    notes: str
    response_note: str
    responded_by: str
    sales_order_id: Optional[int]
    sales_order_number: str
    revisions: List[QuoteRevisionOut]
    totals: TotalsOut
    lines: List[QuoteLineOut]
    prepared_by: str
    actions: QuoteActionsOut


def _totals(doc):
    return {'place_of_supply': doc.place_of_supply, 'is_interstate': doc.is_interstate, 'taxable_value': doc.taxable_value,
            'cgst': doc.cgst, 'sgst': doc.sgst, 'igst': doc.igst, 'round_off': doc.round_off, 'total': doc.total}


def _status(q):
    if services.expired(q):
        return 'expired', 'Expired'
    return q.status, q.get_status_display()


def _quote_out(q, membership):
    mine = q.company_id == membership.company_id
    writable = _writes_allowed(membership)
    sell = mine and writable and _can(membership, 'quotation.create', q.customer)
    S = Q.Status
    status, label = _status(q)
    return {
        'id': q.pk, 'number': q.display_number, 'revision': q.revision, 'status': status, 'status_label': label,
        'expired': services.expired(q), 'seller': _party(q.company), 'customer': _party(q.customer),
        'quote_date': q.quote_date, 'valid_until': q.valid_until, 'enquiry_reference': q.enquiry_reference,
        'dispatch_from_id': q.dispatch_from_id, 'dispatch_from': q.dispatch_from.name if mine else '',
        'delivery_days': q.delivery_days, 'payment_terms_days': q.payment_terms_days, 'terms': q.terms,
        'notes': q.notes if mine else '', 'response_note': q.response_note, 'responded_by': _name(q.responded_by),
        'sales_order_id': q.sales_order_id if mine else None,
        'sales_order_number': q.sales_order.number if mine and q.sales_order_id else '',
        'revisions': [{'id': r.pk, 'number': r.display_number, 'status': r.status, 'status_label': r.get_status_display()}
                      for r in Q.objects.filter(company=q.company, number=q.number).exclude(pk=q.pk).order_by('revision')
                      if mine or r.status != S.DRAFT],
        'totals': _totals(q),
        'lines': [{'id': l.pk, 'item_id': l.item_id, 'item_code': l.item.code, 'item_name': l.item.name, 'hsn_code': l.item.hsn_code,
                   'description': l.description, 'quantity': l.quantity, 'unit': l.unit, 'rate': l.rate, 'gst_rate': l.gst_rate,
                   'taxable_value': l.taxable_value, 'tax_amount': l.tax_amount} for l in q.lines.select_related('item')],
        'prepared_by': _name(q.created_by) if mine else '',
        'actions': {
            'edit': sell and q.status == S.DRAFT, 'send': sell and q.status == S.DRAFT,
            'revise': sell and q.status in (S.SENT, S.DECLINED),
            'record_response': sell and q.status == S.SENT,
            'convert': sell and q.status in (S.SENT, S.ACCEPTED) and not services.expired(q) and _can(membership, 'so.create', q.customer),
            'cancel': sell and q.status in (S.SENT, S.ACCEPTED),
            'respond': (not mine and writable and q.status == S.SENT and not services.expired(q)
                        and _can(membership, 'po.create', q.company)),
        },
    }


QUOTE_RELATED = ('company', 'customer', 'dispatch_from', 'sales_order', 'responded_by', 'created_by')


def _quotation(membership, quotation_id):
    return get_object_or_404(Q.objects.select_related(*QUOTE_RELATED), pk=quotation_id, company=membership.company)


class QuoteListOut(Schema):
    id: int
    number: str
    status: str
    status_label: str
    party: PartyOut
    quote_date: date
    valid_until: date
    total: Decimal
    sales_order_number: str


def _quote_row(q, party):
    status, label = _status(q)
    return {'id': q.pk, 'number': q.display_number, 'status': status, 'status_label': label, 'party': _party(party),
            'quote_date': q.quote_date, 'valid_until': q.valid_until, 'total': q.total,
            'sales_order_number': q.sales_order.number if q.sales_order_id else ''}


@router.get('/quotations', response=List[QuoteListOut])
def list_quotations(request, status: str = ''):
    """Our quotations, newest first; revised ones are left out (the latest revision shows)."""
    membership = _viewer(request, SALES_ROLES)
    found = Q.objects.filter(company=membership.company).exclude(status=Q.Status.SUPERSEDED).select_related('customer', 'sales_order')
    if status:
        found = found.filter(status=status)
    return [_quote_row(q, q.customer) for q in found[:LIST_LIMIT]]


@router.get('/quotations/{quotation_id}', response=QuotationOut)
def get_quotation(request, quotation_id: int):
    membership = _viewer(request, SALES_ROLES)
    return _quote_out(_quotation(membership, quotation_id), membership)


@router.post('/quotations', response=QuotationOut)
def create_quotation(request, payload: QuotationIn):
    membership = _writer(request)
    q = _run(services.save_quotation, membership, Q(), payload.dict())
    return _quote_out(_quotation(membership, q.pk), membership)


@router.put('/quotations/{quotation_id}', response=QuotationOut)
def update_quotation(request, quotation_id: int, payload: QuotationIn):
    membership = _writer(request)
    q = _run(services.save_quotation, membership, _quotation(membership, quotation_id), payload.dict())
    return _quote_out(_quotation(membership, q.pk), membership)


@router.delete('/quotations/{quotation_id}', response={204: None})
def delete_quotation(request, quotation_id: int):
    membership = _writer(request)
    if not _can(membership, 'quotation.create'):
        raise HttpError(403, "Your role doesn't include this.")
    _run(services.delete_quotation, _quotation(membership, quotation_id))
    return 204, None


@router.post('/quotations/{quotation_id}/send', response=QuotationOut)
def send_quotation(request, quotation_id: int):
    membership = _writer(request)
    q = _run(services.send, membership, _quotation(membership, quotation_id))
    return _quote_out(_quotation(membership, q.pk), membership)


@router.post('/quotations/{quotation_id}/revise', response=QuotationOut)
def revise_quotation(request, quotation_id: int):
    """A new draft revision; the one sent is kept, marked revised."""
    membership = _writer(request)
    q = _run(services.revise, membership, _quotation(membership, quotation_id))
    return _quote_out(_quotation(membership, q.pk), membership)


class QuoteResponseIn(Schema):
    accepted: bool
    note: str = ''


@router.post('/quotations/{quotation_id}/response', response=QuotationOut)
def record_quotation_response(request, quotation_id: int, payload: QuoteResponseIn):
    """The customer's answer, when they gave it by phone or email."""
    membership = _writer(request)
    q = _run(services.record_response, membership, _quotation(membership, quotation_id), payload.accepted, payload.note)
    return _quote_out(_quotation(membership, q.pk), membership)


class ConvertedOut(Schema):
    sales_order_id: int
    sales_order_number: str


@router.post('/quotations/{quotation_id}/convert', response=ConvertedOut)
def convert_quotation(request, quotation_id: int):
    """A draft sales order at the quoted rates."""
    membership = _writer(request)
    order = _run(services.convert, membership, _quotation(membership, quotation_id))
    return {'sales_order_id': order.pk, 'sales_order_number': order.number}


class QuoteReasonIn(Schema):
    reason: str = ''


@router.post('/quotations/{quotation_id}/cancel', response=QuotationOut)
def cancel_quotation(request, quotation_id: int, payload: QuoteReasonIn):
    membership = _writer(request)
    q = _run(services.cancel_quotation, membership, _quotation(membership, quotation_id), payload.reason)
    return _quote_out(_quotation(membership, q.pk), membership)


def _pdf_response(data, name):
    from printouts.api import _pdf, _safe
    return _pdf(data, f"{_safe(name)}.pdf")


@router.get('/quotations/{quotation_id}/pdf')
def quotation_pdf(request, quotation_id: int):
    """The seller, and the customer once it's been sent."""
    from printouts import render

    membership = membership_for(request)
    q = Q.objects.select_related(*QUOTE_RELATED).filter(pk=quotation_id).first()
    if q is None:
        raise Http404
    seller = q.company_id == membership.company_id and any(_can(membership, c) for c in SALES_ROLES)
    customer = (q.customer_id == membership.company_id and q.status != Q.Status.DRAFT
                and any(_can(membership, c) for c in PURCHASE_ROLES))
    if not (seller or customer):
        raise Http404
    return _pdf_response(render.quotation(q), q.display_number)


# The customer's side.

def _received(membership, quotation_id):
    q = Q.objects.select_related(*QUOTE_RELATED).filter(pk=quotation_id, customer=membership.company).exclude(status=Q.Status.DRAFT).first()
    if q is None:
        raise Http404
    return q


@router.get('/purchase/quotations-received', response=List[QuoteListOut])
def quotations_received(request):
    """Quotations suppliers have sent us on MakeSetu."""
    membership = _viewer(request, PURCHASE_ROLES)
    found = (Q.objects.filter(customer=membership.company).exclude(status__in=[Q.Status.DRAFT, Q.Status.SUPERSEDED])
             .select_related('company', 'sales_order'))
    return [{**_quote_row(q, q.company), 'sales_order_number': ''} for q in found[:LIST_LIMIT]]


@router.get('/purchase/quotations-received/{quotation_id}', response=QuotationOut)
def quotation_received(request, quotation_id: int):
    membership = _viewer(request, PURCHASE_ROLES)
    return _quote_out(_received(membership, quotation_id), membership)


@router.post('/purchase/quotations-received/{quotation_id}/response', response=QuotationOut)
def respond_to_quotation(request, quotation_id: int, payload: QuoteResponseIn):
    membership = _writer(request)
    q = _run(services.customer_response, membership, _received(membership, quotation_id), payload.accepted, payload.note)
    return _quote_out(_received(membership, q.pk), membership)


# --- Requests for quotation -----------------------------------------------------------------------

class RfqLineIn(Schema):
    item_id: int
    quantity: Decimal
    description: str = ''


class RfqIn(Schema):
    title: str
    ship_to_id: int
    rfq_date: Optional[date] = None
    respond_by: Optional[date] = None
    required_by: Optional[date] = None
    notes: str = ''
    lines: List[RfqLineIn]
    supplier_ids: List[int] = []


class RfqLineOut(Schema):
    id: int
    item_id: int
    item_code: str
    item_name: str
    description: str
    quantity: Decimal
    unit: str
    awarded_to: Optional[int]  # the invite it was awarded to


class RfqOfferOut(Schema):
    invite_id: int
    rate: Decimal
    gst_rate: Decimal
    value: Decimal
    lowest: bool


class RfqCompareLineOut(Schema):
    line: RfqLineOut
    offers: List[RfqOfferOut]


class RfqInviteOut(Schema):
    id: int
    supplier: PartyOut
    status: str
    status_label: str
    their_reference: str
    valid_until: Optional[date]
    delivery_days: Optional[int]
    payment_terms_days: Optional[int]
    remarks: str
    entered_by_supplier: bool
    lines_quoted: int
    complete: bool
    taxable_value: Decimal
    total: Decimal
    purchase_order_id: Optional[int]
    purchase_order_number: str


class RfqActionsOut(Schema):
    edit: bool
    send: bool
    enter_quotes: bool
    award: bool
    cancel: bool


class RfqOut(Schema):
    id: int
    number: str
    title: str
    status: str
    status_label: str
    rfq_date: date
    respond_by: Optional[date]
    required_by: Optional[date]
    ship_to_id: int
    ship_to: str
    notes: str
    cancel_reason: str
    lines: List[RfqCompareLineOut]
    suppliers: List[RfqInviteOut]
    created_by: str
    actions: RfqActionsOut


def _rfq_line(l):
    return {'id': l.pk, 'item_id': l.item_id, 'item_code': l.item.code, 'item_name': l.item.name, 'description': l.description,
            'quantity': l.quantity, 'unit': l.unit, 'awarded_to': l.awarded_to_id}


def _rfq_out(rfq, membership):
    compare = services.comparison(rfq)
    manage = _writes_allowed(membership) and _can(membership, 'po.create')
    S = Rfq.Status
    return {
        'id': rfq.pk, 'number': rfq.number, 'title': rfq.title, 'status': rfq.status, 'status_label': rfq.get_status_display(),
        'rfq_date': rfq.rfq_date, 'respond_by': rfq.respond_by, 'required_by': rfq.required_by, 'ship_to_id': rfq.ship_to_id,
        'ship_to': rfq.ship_to.name, 'notes': rfq.notes, 'cancel_reason': rfq.cancel_reason,
        'lines': [{'line': _rfq_line(r['line']),
                   'offers': [{'invite_id': o['invite'].pk, 'rate': o['rate'], 'gst_rate': o['gst_rate'], 'value': o['value'],
                               'lowest': o['lowest']} for o in r['offers']]} for r in compare['lines']],
        'suppliers': [{
            'id': t['invite'].pk, 'supplier': _party(t['invite'].supplier), 'status': t['invite'].status,
            'status_label': t['invite'].get_status_display(), 'their_reference': t['invite'].their_reference,
            'valid_until': t['invite'].valid_until, 'delivery_days': t['invite'].delivery_days,
            'payment_terms_days': t['invite'].payment_terms_days, 'remarks': t['invite'].remarks,
            'entered_by_supplier': t['invite'].entered_by_supplier, 'lines_quoted': t['lines_quoted'], 'complete': t['complete'],
            'taxable_value': t['taxable_value'], 'total': t['total'], 'purchase_order_id': t['invite'].purchase_order_id,
            'purchase_order_number': t['invite'].purchase_order.number if t['invite'].purchase_order_id else '',
        } for t in compare['suppliers']],
        'created_by': _name(rfq.created_by),
        'actions': {'edit': manage and rfq.status == S.DRAFT, 'send': manage and rfq.status == S.DRAFT,
                    'enter_quotes': manage and rfq.status == S.SENT,
                    'award': manage and rfq.status == S.SENT and any(t['lines_quoted'] for t in compare['suppliers']),
                    'cancel': manage and rfq.status in (S.DRAFT, S.SENT)},
    }


def _rfq(membership, rfq_id):
    return get_object_or_404(Rfq.objects.select_related('ship_to', 'created_by'), pk=rfq_id, company=membership.company)


class RfqListOut(Schema):
    id: int
    number: str
    title: str
    status: str
    status_label: str
    rfq_date: date
    respond_by: Optional[date]
    asked: int
    quoted: int


@router.get('/rfqs', response=List[RfqListOut])
def list_rfqs(request):
    membership = _viewer(request, PURCHASE_ROLES)
    rows = []
    for rfq in Rfq.objects.filter(company=membership.company).prefetch_related('suppliers')[:LIST_LIMIT]:
        invites = list(rfq.suppliers.all())
        rows.append({'id': rfq.pk, 'number': rfq.number, 'title': rfq.title, 'status': rfq.status,
                     'status_label': rfq.get_status_display(), 'rfq_date': rfq.rfq_date, 'respond_by': rfq.respond_by,
                     'asked': len(invites), 'quoted': sum(1 for i in invites if i.quoted_at and i.status != RfqSupplier.Status.DECLINED)})
    return rows


@router.get('/rfqs/{rfq_id}', response=RfqOut)
def get_rfq(request, rfq_id: int):
    """The RFQ with every supplier's quote side by side."""
    membership = _viewer(request, PURCHASE_ROLES)
    return _rfq_out(_rfq(membership, rfq_id), membership)


@router.post('/rfqs', response=RfqOut)
def create_rfq(request, payload: RfqIn):
    membership = _writer(request)
    rfq = _run(services.save_rfq, membership, Rfq(), payload.dict())
    return _rfq_out(_rfq(membership, rfq.pk), membership)


@router.put('/rfqs/{rfq_id}', response=RfqOut)
def update_rfq(request, rfq_id: int, payload: RfqIn):
    membership = _writer(request)
    rfq = _run(services.save_rfq, membership, _rfq(membership, rfq_id), payload.dict())
    return _rfq_out(_rfq(membership, rfq.pk), membership)


@router.post('/rfqs/{rfq_id}/send', response=RfqOut)
def send_rfq(request, rfq_id: int):
    membership = _writer(request)
    rfq = _run(services.send_rfq, membership, _rfq(membership, rfq_id))
    return _rfq_out(_rfq(membership, rfq.pk), membership)


class RfqAddSupplierIn(Schema):
    supplier_id: int


@router.post('/rfqs/{rfq_id}/suppliers', response=RfqOut)
def add_rfq_supplier(request, rfq_id: int, payload: RfqAddSupplierIn):
    membership = _writer(request)
    _run(services.add_supplier, membership, _rfq(membership, rfq_id), payload.supplier_id)
    return _rfq_out(_rfq(membership, rfq_id), membership)


class RfqQuoteLineIn(Schema):
    rfq_line_id: int
    rate: Optional[Decimal] = None   # left out: not quoted
    gst_rate: Optional[Decimal] = None


class RfqQuoteIn(Schema):
    their_reference: str = ''
    valid_until: Optional[date] = None
    delivery_days: Optional[int] = None
    payment_terms_days: Optional[int] = None
    remarks: str = ''
    lines: List[RfqQuoteLineIn]


def _invite(rfq, invite_id):
    return get_object_or_404(RfqSupplier, pk=invite_id, rfq=rfq)


@router.post('/rfqs/{rfq_id}/suppliers/{invite_id}/quote', response=RfqOut)
def enter_quote(request, rfq_id: int, invite_id: int, payload: RfqQuoteIn):
    """A supplier's quote, entered for them (from their email or letter)."""
    membership = _writer(request)
    rfq = _rfq(membership, rfq_id)
    _run(services.save_quote, membership, _invite(rfq, invite_id), payload.dict())
    return _rfq_out(_rfq(membership, rfq_id), membership)


@router.post('/rfqs/{rfq_id}/suppliers/{invite_id}/decline', response=RfqOut)
def mark_declined(request, rfq_id: int, invite_id: int, payload: QuoteReasonIn):
    """The supplier said they won't quote."""
    membership = _writer(request)
    rfq = _rfq(membership, rfq_id)
    _run(services.decline_rfq, membership, _invite(rfq, invite_id), payload.reason)
    return _rfq_out(_rfq(membership, rfq_id), membership)


class RfqAwardLineIn(Schema):
    rfq_line_id: int
    invite_id: int


class RfqAwardIn(Schema):
    # Either every line a supplier quoted for, or line by line.
    invite_id: Optional[int] = None
    awards: List[RfqAwardLineIn] = []


class RfqAwardedOut(Schema):
    rfq: RfqOut
    purchase_order_ids: List[int]


@router.post('/rfqs/{rfq_id}/award', response=RfqAwardedOut)
def award_rfq(request, rfq_id: int, payload: RfqAwardIn):
    """Awards the RFQ and raises a draft purchase order for each supplier chosen."""
    membership = _writer(request)
    orders = _run(services.award, membership, _rfq(membership, rfq_id), payload.dict())
    return {'rfq': _rfq_out(_rfq(membership, rfq_id), membership), 'purchase_order_ids': [o.pk for o in orders]}


@router.post('/rfqs/{rfq_id}/cancel', response=RfqOut)
def cancel_rfq(request, rfq_id: int, payload: QuoteReasonIn):
    membership = _writer(request)
    _run(services.cancel_rfq, membership, _rfq(membership, rfq_id), payload.reason)
    return _rfq_out(_rfq(membership, rfq_id), membership)


@router.delete('/rfqs/{rfq_id}', response={204: None})
def delete_rfq(request, rfq_id: int):
    membership = _writer(request)
    rfq = _rfq(membership, rfq_id)
    if rfq.status != Rfq.Status.DRAFT or not _can(membership, 'po.create'):
        raise HttpError(400, "Only a draft RFQ can be deleted; cancel it instead.")
    rfq.delete()
    return 204, None


@router.get('/rfqs/{rfq_id}/pdf')
def rfq_pdf(request, rfq_id: int):
    """The RFQ to send to a supplier who isn't on MakeSetu."""
    from printouts import render

    membership = _viewer(request, PURCHASE_ROLES)
    rfq = _rfq(membership, rfq_id)
    return _pdf_response(render.request_for_quotation(rfq), rfq.number)


# The supplier's side.

class RfqReceivedLineOut(Schema):
    rfq_line_id: int
    item_code: str
    item_name: str
    hsn_code: str
    description: str
    quantity: Decimal
    unit: str
    rate: Optional[Decimal]
    gst_rate: Optional[Decimal]
    awarded_to_you: Optional[bool]


class RfqReceivedOut(Schema):
    invite_id: int
    rfq_number: str
    title: str
    buyer: PartyOut
    rfq_date: date
    respond_by: Optional[date]
    required_by: Optional[date]
    deliver_to: str
    notes: str
    rfq_status: str
    status: str
    status_label: str
    their_reference: str
    valid_until: Optional[date]
    delivery_days: Optional[int]
    payment_terms_days: Optional[int]
    remarks: str
    lines: List[RfqReceivedLineOut]
    can_quote: bool




def _received_out(invite, membership):
    rfq = invite.rfq
    quotes = {q.line_id: q for q in invite.quote_lines.all()}
    address = rfq.ship_to.address
    awarded = rfq.status == Rfq.Status.AWARDED
    return {
        'invite_id': invite.pk, 'rfq_number': rfq.number, 'title': rfq.title, 'buyer': _party(rfq.company),
        'rfq_date': rfq.rfq_date, 'respond_by': rfq.respond_by, 'required_by': rfq.required_by,
        'deliver_to': ', '.join(p for p in (address.city, address.state) if p) if address else (rfq.company.city or ''),
        'notes': rfq.notes, 'rfq_status': rfq.status, 'status': invite.status, 'status_label': invite.get_status_display(),
        'their_reference': invite.their_reference, 'valid_until': invite.valid_until, 'delivery_days': invite.delivery_days,
        'payment_terms_days': invite.payment_terms_days, 'remarks': invite.remarks,
        'lines': [{'rfq_line_id': l.pk, 'item_code': l.item.code, 'item_name': l.item.name, 'hsn_code': l.item.hsn_code,
                   'description': l.description, 'quantity': l.quantity, 'unit': l.unit,
                   'rate': quotes[l.pk].rate if l.pk in quotes else None, 'gst_rate': quotes[l.pk].gst_rate if l.pk in quotes else None,
                   'awarded_to_you': (l.awarded_to_id == invite.pk) if awarded else None}
                  for l in rfq.lines.select_related('item')],
        'can_quote': (rfq.status == Rfq.Status.SENT and _writes_allowed(membership)
                      and _can(membership, 'quotation.create', rfq.company)),
    }


def _invite_received(membership, invite_id):
    invite = (RfqSupplier.objects.select_related('rfq__company', 'rfq__ship_to__address')
              .filter(pk=invite_id, supplier=membership.company).exclude(rfq__status=Rfq.Status.DRAFT).first())
    if invite is None:
        raise Http404
    return invite


@router.get('/sales/rfqs-received', response=List[RfqReceivedOut])
def rfqs_received(request):
    """RFQs customers have sent us on MakeSetu."""
    membership = _viewer(request, SALES_ROLES)
    invites = (RfqSupplier.objects.filter(supplier=membership.company).exclude(rfq__status=Rfq.Status.DRAFT)
               .select_related('rfq__company', 'rfq__ship_to__address').order_by('-rfq__rfq_date', '-id'))
    return [_received_out(i, membership) for i in invites[:LIST_LIMIT]]


@router.get('/sales/rfqs-received/{invite_id}', response=RfqReceivedOut)
def rfq_received(request, invite_id: int):
    membership = _viewer(request, SALES_ROLES)
    return _received_out(_invite_received(membership, invite_id), membership)


@router.post('/sales/rfqs-received/{invite_id}/quote', response=RfqReceivedOut)
def quote_on_rfq(request, invite_id: int, payload: RfqQuoteIn):
    """Our quote to the customer, against their RFQ."""
    membership = _writer(request)
    invite = _run(services.save_quote, membership, _invite_received(membership, invite_id), payload.dict(), True)
    return _received_out(_invite_received(membership, invite.pk), membership)


@router.post('/sales/rfqs-received/{invite_id}/decline', response=RfqReceivedOut)
def decline_rfq_received(request, invite_id: int, payload: QuoteReasonIn):
    membership = _writer(request)
    invite = _run(services.decline_rfq, membership, _invite_received(membership, invite_id), payload.reason, True)
    return _received_out(_invite_received(membership, invite.pk), membership)
