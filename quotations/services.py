"""Every change to quotations and RFQs goes through here. Callers
(quotations.api) have checked the subscription allows changes; this
checks permissions against the other company and applies the rules."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.models import Company
from inventory import services as inventory
from inventory.models import GST_RATE_VALUES, Item
from organizations import access
from purchase import services as purchase
from purchase.models import PurchaseOrder
from purchase.services import _decimal, apply_totals, is_interstate, next_number, place_of_supply
from sales import services as sales
from sales.models import SalesOrder

from .models import Quotation, QuotationLine, Rfq, RfqLine, RfqQuoteLine, RfqSupplier

ZERO = Decimal('0')
MAX_LINES = 100
MAX_SUPPLIERS = 10
DEFAULT_VALIDITY_DAYS = 30
Q = Quotation


class QuotationError(Exception):
    """A change that isn't allowed; the message says why."""


def _check(membership, code, counterparty=None):
    decision = access.check(membership, code, counterparty)
    if not decision:
        raise QuotationError(decision.reason)


def _wrap(fn, *args, **kwargs):
    """Runs a purchase or sales service, turning its error into ours."""
    try:
        return fn(*args, **kwargs)
    except (purchase.PurchaseError, sales.SalesError) as exc:
        raise QuotationError(str(exc))


def expired(q, today=None):
    return q.status == Q.Status.SENT and q.valid_until < (today or timezone.localdate())


# --- Sales quotations ---------------------------------------------------------------------------

def _locked(q, *statuses):
    q = Q.objects.select_for_update().get(pk=q.pk)
    if statuses and q.status not in statuses:
        raise QuotationError(f"This quotation is {q.get_status_display().lower()}.")
    return q


@transaction.atomic
def save_quotation(membership, q, data):
    """Creates a draft quotation, or updates a draft."""
    company = membership.company
    if q.pk:
        q = _locked(q, Q.Status.DRAFT)
    customer = Company.objects.filter(pk=data.get('customer_id')).first()
    if customer is None or customer.pk == company.pk:
        raise QuotationError("Choose a customer.")
    _check(membership, 'quotation.create', counterparty=customer)
    warehouse = inventory.store(company, data.get('dispatch_from_id'))
    if warehouse is None:
        raise QuotationError("Choose the warehouse you'd supply from.")
    quote_date = data.get('quote_date') or timezone.localdate()
    valid_until = data.get('valid_until') or quote_date + timedelta(days=DEFAULT_VALIDITY_DAYS)
    if valid_until < quote_date:
        raise QuotationError("It can't be valid until before it's dated.")
    rows = data.get('lines') or []
    if len(rows) > MAX_LINES:
        raise QuotationError(f"A quotation can have up to {MAX_LINES} lines.")
    # Same checks as a sales order's lines (rate defaults to the item's sale price).
    order_lines = _wrap(sales._order_lines, company, rows)

    q.company, q.customer, q.dispatch_from = company, customer, warehouse
    q.quote_date, q.valid_until = quote_date, valid_until
    q.enquiry_reference = (data.get('enquiry_reference') or '').strip()[:100]
    relationship = sales._relationship(company, customer)
    terms = data.get('payment_terms_days')
    q.payment_terms_days = terms if terms is not None else (relationship.payment_terms_days if relationship else None)
    q.delivery_days = data.get('delivery_days')
    q.terms = (data.get('terms') or '').strip()
    q.notes = (data.get('notes') or '').strip()
    q.place_of_supply = purchase._state_of(customer)
    q.is_interstate = is_interstate(company, q.place_of_supply)
    if q.pk is None:
        q.number = next_number(company, 'erp_qt', 'QT', quote_date)
        q.created_by = membership.user
    lines = [QuotationLine(position=l.position, item=l.item, description=l.description, quantity=l.quantity, unit=l.unit,
                           rate=l.rate, gst_rate=l.gst_rate) for l in order_lines]
    apply_totals(q, lines)
    q.save()
    q.lines.all().delete()
    for line in lines:
        line.quotation = q
    QuotationLine.objects.bulk_create(lines)
    return q


@transaction.atomic
def send(membership, q):
    q = _locked(q, Q.Status.DRAFT)
    _check(membership, 'quotation.create', counterparty=q.customer)
    if q.valid_until < timezone.localdate():
        raise QuotationError("Its validity has already run out; change the 'valid until' date first.")
    q.status, q.sent_at = Q.Status.SENT, timezone.now()
    q.save(update_fields=['status', 'sent_at', 'updated_at'])
    return q


@transaction.atomic
def revise(membership, q):
    """A new draft revision of a sent or declined quotation; the old one is kept as revised."""
    q = _locked(q, Q.Status.SENT, Q.Status.DECLINED)
    _check(membership, 'quotation.create', counterparty=q.customer)
    old_pk = q.pk
    lines = list(QuotationLine.objects.filter(quotation_id=old_pk))
    q.superseded_status, q.status = q.status, Q.Status.SUPERSEDED
    q.save(update_fields=['status', 'superseded_status', 'updated_at'])
    new = Q.objects.get(pk=old_pk)
    new.pk = new.id = None
    new.revision = Q.objects.filter(company=q.company, number=q.number).order_by('-revision').first().revision + 1
    new.previous_id = old_pk
    new.status, new.sent_at, new.responded_at, new.responded_by, new.response_note = Q.Status.DRAFT, None, None, None, ''
    new.quote_date = timezone.localdate()
    new.valid_until = max(new.valid_until, new.quote_date + timedelta(days=DEFAULT_VALIDITY_DAYS))
    new.created_by = membership.user
    new.save()
    for line in lines:
        line.pk = line.id = None
        line.quotation = new
    QuotationLine.objects.bulk_create(lines)
    return new


def _respond(q, accepted, membership, note):
    if accepted and expired(q):
        raise QuotationError(f"This quotation expired on {q.valid_until:%d %b %Y}. Ask for a revised one.")
    q.status = Q.Status.ACCEPTED if accepted else Q.Status.DECLINED
    q.responded_at, q.responded_by, q.response_note = timezone.now(), membership.user, (note or '').strip()[:300]
    q.save(update_fields=['status', 'responded_at', 'responded_by', 'response_note', 'updated_at'])
    return q


@transaction.atomic
def record_response(membership, q, accepted, note=''):
    """We note the customer's answer (they told us by phone or email)."""
    q = _locked(q, Q.Status.SENT)
    _check(membership, 'quotation.create', counterparty=q.customer)
    if not accepted and not (note or '').strip():
        raise QuotationError("Say why they declined, e.g. price or delivery.")
    return _respond(q, accepted, membership, note)


@transaction.atomic
def customer_response(membership, q, accepted, note=''):
    """The customer answers on MakeSetu."""
    q = _locked(q, Q.Status.SENT)
    if q.customer_id != membership.company_id:
        raise QuotationError("This quotation isn't addressed to you.")
    _check(membership, 'po.create', counterparty=q.company)
    if not accepted and not (note or '').strip():
        raise QuotationError("Tell the supplier why, e.g. price or delivery.")
    return _respond(q, accepted, membership, note)


@transaction.atomic
def convert(membership, q):
    """A sales order (draft) from an accepted quotation, at its rates."""
    q = _locked(q, Q.Status.ACCEPTED, Q.Status.SENT)
    if q.status == Q.Status.SENT:
        if expired(q):
            raise QuotationError(f"This quotation expired on {q.valid_until:%d %b %Y}. Revise it first.")
        _respond(q, True, membership, 'Accepted (recorded with the sales order)')
    order = _wrap(sales.save_order, membership, SalesOrder(), {
        'customer_id': q.customer_id, 'dispatch_from_id': q.dispatch_from_id, 'order_date': timezone.localdate(),
        'delivery_date': timezone.localdate() + timedelta(days=q.delivery_days) if q.delivery_days else None,
        'customer_po_number': q.enquiry_reference[:40], 'payment_terms_days': q.payment_terms_days,
        'notes': '\n'.join(p for p in (f"Against quotation {q.display_number}.", q.terms) if p),
        'lines': [{'item_id': l.item_id, 'quantity': l.quantity, 'rate': l.rate, 'gst_rate': l.gst_rate, 'description': l.description}
                  for l in q.lines.all()],
    })
    q.status, q.sales_order = Q.Status.CONVERTED, order
    q.save(update_fields=['status', 'sales_order', 'updated_at'])
    return order


@transaction.atomic
def cancel_quotation(membership, q, reason):
    q = _locked(q, Q.Status.SENT, Q.Status.ACCEPTED)
    _check(membership, 'quotation.create', counterparty=q.customer)
    if not (reason or '').strip():
        raise QuotationError("Say why it's being withdrawn; the customer sees this.")
    q.status, q.response_note = Q.Status.CANCELLED, reason.strip()[:300]
    q.save(update_fields=['status', 'response_note', 'updated_at'])
    return q


@transaction.atomic
def delete_quotation(q):
    q = _locked(q, Q.Status.DRAFT)
    if q.previous_id:
        previous = Q.objects.select_for_update().get(pk=q.previous_id)
        previous.status, previous.superseded_status = previous.superseded_status or Q.Status.SENT, ''
        previous.save(update_fields=['status', 'superseded_status', 'updated_at'])
    q.delete()


# --- Requests for quotation -----------------------------------------------------------------------

def _locked_rfq(rfq, *statuses):
    rfq = Rfq.objects.select_for_update().get(pk=rfq.pk)
    if statuses and rfq.status not in statuses:
        raise QuotationError(f"This RFQ is {rfq.get_status_display().lower()}.")
    return rfq


@transaction.atomic
def save_rfq(membership, rfq, data):
    """Creates a draft RFQ, or updates a draft: what's needed and who to ask."""
    company = membership.company
    _check(membership, 'po.create')
    if rfq.pk:
        rfq = _locked_rfq(rfq, Rfq.Status.DRAFT)
    title = (data.get('title') or '').strip()[:120]
    if not title:
        raise QuotationError("Give the RFQ a title, e.g. 'EN8 bar for March'.")
    warehouse = inventory.store(company, data.get('ship_to_id'))
    if warehouse is None:
        raise QuotationError("Choose the warehouse the goods would go to.")
    rfq_date = data.get('rfq_date') or timezone.localdate()
    for field, what in (('respond_by', 'reply'), ('required_by', 'goods')):
        if data.get(field) and data[field] < rfq_date:
            raise QuotationError(f"The date for the {what} can't be before the RFQ date.")

    rows = data.get('lines') or []
    if not rows:
        raise QuotationError("Add at least one item.")
    if len(rows) > MAX_LINES:
        raise QuotationError(f"An RFQ can have up to {MAX_LINES} lines.")
    items = {i.pk: i for i in Item.objects.filter(company=company, pk__in=[r.get('item_id') for r in rows])}
    lines = []
    for position, row in enumerate(rows, start=1):
        item = items.get(row.get('item_id'))
        if item is None or not item.is_active:
            raise QuotationError(f"Line {position}: choose one of your active items.")
        quantity = _decimal(row.get('quantity'), 'the quantity')
        if quantity <= 0:
            raise QuotationError(f"Line {position}: the quantity must be more than 0.")
        lines.append(RfqLine(position=position, item=item, description=(row.get('description') or '').strip()[:200],
                             quantity=quantity, unit=item.unit))

    supplier_ids = list(dict.fromkeys(data.get('supplier_ids') or []))
    if len(supplier_ids) > MAX_SUPPLIERS:
        raise QuotationError(f"Ask up to {MAX_SUPPLIERS} suppliers.")
    suppliers = {c.pk: c for c in Company.objects.filter(pk__in=supplier_ids)}
    for supplier_id in supplier_ids:
        supplier = suppliers.get(supplier_id)
        if supplier is None or supplier.pk == company.pk:
            raise QuotationError("Choose your suppliers.")
        _check(membership, 'po.create', counterparty=supplier)

    rfq.company, rfq.title, rfq.ship_to, rfq.rfq_date = company, title, warehouse, rfq_date
    rfq.respond_by, rfq.required_by = data.get('respond_by'), data.get('required_by')
    rfq.notes = (data.get('notes') or '').strip()
    if rfq.pk is None:
        rfq.number = next_number(company, 'erp_rfq', 'RFQ', rfq_date)
        rfq.created_by = membership.user
    rfq.save()
    rfq.lines.all().delete()
    for line in lines:
        line.rfq = rfq
    RfqLine.objects.bulk_create(lines)
    rfq.suppliers.exclude(supplier_id__in=supplier_ids).delete()
    have = set(rfq.suppliers.values_list('supplier_id', flat=True))
    RfqSupplier.objects.bulk_create([RfqSupplier(rfq=rfq, supplier=suppliers[s]) for s in supplier_ids if s not in have])
    return rfq


@transaction.atomic
def send_rfq(membership, rfq):
    rfq = _locked_rfq(rfq, Rfq.Status.DRAFT)
    _check(membership, 'po.create')
    if not rfq.suppliers.exists():
        raise QuotationError("Choose at least one supplier to ask.")
    rfq.status, rfq.sent_at = Rfq.Status.SENT, timezone.now()
    rfq.save(update_fields=['status', 'sent_at'])
    return rfq


@transaction.atomic
def add_supplier(membership, rfq, supplier_id):
    """Asks one more supplier after the RFQ has gone out."""
    rfq = _locked_rfq(rfq, Rfq.Status.DRAFT, Rfq.Status.SENT)
    supplier = Company.objects.filter(pk=supplier_id).exclude(pk=rfq.company_id).first()
    if supplier is None:
        raise QuotationError("Choose a supplier.")
    _check(membership, 'po.create', counterparty=supplier)
    if rfq.suppliers.filter(supplier=supplier).exists():
        raise QuotationError(f"{supplier.name or supplier.legal_name} has already been asked.")
    if rfq.suppliers.count() >= MAX_SUPPLIERS:
        raise QuotationError(f"Ask up to {MAX_SUPPLIERS} suppliers.")
    return RfqSupplier.objects.create(rfq=rfq, supplier=supplier)


def _can_quote(membership, invite, by_supplier):
    rfq = invite.rfq
    if by_supplier:
        if invite.supplier_id != membership.company_id:
            raise QuotationError("This RFQ wasn't sent to you.")
        _check(membership, 'quotation.create', counterparty=rfq.company)
    else:
        if rfq.company_id != membership.company_id:
            raise QuotationError("This isn't your RFQ.")
        _check(membership, 'po.create', counterparty=invite.supplier)
    if rfq.status != Rfq.Status.SENT:
        raise QuotationError(f"This RFQ is {rfq.get_status_display().lower()}; quotes can't change now.")


@transaction.atomic
def save_quote(membership, invite, data, by_supplier=False):
    """A supplier's quote against an RFQ: entered by the supplier on
    MakeSetu, or by the buyer from their email. Lines left out aren't quoted."""
    invite = RfqSupplier.objects.select_for_update().select_related('rfq', 'supplier').get(pk=invite.pk)
    _can_quote(membership, invite, by_supplier)
    rfq_lines = {l.pk: l for l in invite.rfq.lines.select_related('item')}
    quotes = []
    for row in data.get('lines') or []:
        line = rfq_lines.get(row.get('rfq_line_id'))
        if line is None:
            raise QuotationError("A line isn't on this RFQ.")
        if row.get('rate') in (None, ''):
            continue
        rate = _decimal(row.get('rate'), 'the rate')
        gst_rate = _decimal(row.get('gst_rate') if row.get('gst_rate') not in (None, '') else line.item.gst_rate, 'the GST rate')
        if rate < 0:
            raise QuotationError(f"{line.item.name}: the rate can't be negative.")
        if gst_rate not in GST_RATE_VALUES:
            raise QuotationError(f"{line.item.name}: that isn't a GST rate.")
        quotes.append(RfqQuoteLine(invite=invite, line=line, rate=rate, gst_rate=gst_rate))
    if not quotes:
        raise QuotationError("Enter a rate for at least one line.")
    valid_until = data.get('valid_until')
    if valid_until and valid_until < timezone.localdate():
        raise QuotationError("The quote's validity has already run out.")
    invite.quote_lines.all().delete()
    RfqQuoteLine.objects.bulk_create(quotes)
    invite.their_reference = (data.get('their_reference') or '').strip()[:60]
    invite.valid_until, invite.delivery_days = valid_until, data.get('delivery_days')
    invite.payment_terms_days = data.get('payment_terms_days')
    invite.remarks = (data.get('remarks') or '').strip()[:300]
    invite.status, invite.quoted_at, invite.entered_by_supplier = RfqSupplier.Status.QUOTED, timezone.now(), by_supplier
    invite.save()
    return invite


@transaction.atomic
def decline_rfq(membership, invite, reason, by_supplier=False):
    invite = RfqSupplier.objects.select_for_update().select_related('rfq', 'supplier').get(pk=invite.pk)
    _can_quote(membership, invite, by_supplier)
    invite.quote_lines.all().delete()
    invite.status, invite.remarks, invite.quoted_at = RfqSupplier.Status.DECLINED, (reason or '').strip()[:300], timezone.now()
    invite.entered_by_supplier = by_supplier
    invite.save()
    return invite


def comparison(rfq):
    """The quotes side by side: per line, each supplier's rate and value
    (lowest marked), and per supplier the totals and terms."""
    invites = list(rfq.suppliers.select_related('supplier').prefetch_related('quote_lines'))
    lines = list(rfq.lines.select_related('item', 'awarded_to'))
    quotes = {(q.invite_id, q.line_id): q for i in invites for q in i.quote_lines.all()}
    rows = []
    for line in lines:
        offers = [(i, quotes[(i.pk, line.pk)]) for i in invites if (i.pk, line.pk) in quotes]
        lowest = min((q.rate for _i, q in offers), default=None)
        rows.append({'line': line, 'offers': [{'invite': i, 'rate': q.rate, 'gst_rate': q.gst_rate,
                                               'value': purchase._money(line.quantity * q.rate),
                                               'lowest': q.rate == lowest and len(offers) > 1} for i, q in offers]})
    totals = []
    for i in invites:
        mine = [(line, quotes[(i.pk, line.pk)]) for line in lines if (i.pk, line.pk) in quotes]
        taxable = sum((purchase._money(l.quantity * q.rate) for l, q in mine), ZERO)
        tax = sum((purchase._money(purchase._money(l.quantity * q.rate) * q.gst_rate / 100) for l, q in mine), ZERO)
        totals.append({'invite': i, 'lines_quoted': len(mine), 'complete': len(mine) == len(lines),
                       'taxable_value': taxable, 'total': taxable + tax})
    return {'lines': rows, 'suppliers': totals}


@transaction.atomic
def award(membership, rfq, data):
    """Awards lines to suppliers (`awards`: [{rfq_line_id, invite_id}], or
    `invite_id` for every line it quoted) and raises a draft purchase
    order for each supplier at their quoted rates."""
    rfq = _locked_rfq(rfq, Rfq.Status.SENT)
    _check(membership, 'po.create')
    invites = {i.pk: i for i in rfq.suppliers.select_related('supplier')}
    lines = {l.pk: l for l in rfq.lines.select_related('item')}
    quotes = {(q.invite_id, q.line_id): q for q in RfqQuoteLine.objects.filter(invite__rfq=rfq)}
    awards = data.get('awards') or []
    if data.get('invite_id'):
        awards = [{'rfq_line_id': line_id, 'invite_id': data['invite_id']} for line_id in lines
                  if (data['invite_id'], line_id) in quotes]
    if not awards:
        raise QuotationError("Choose who to award each line to.")
    chosen = {}
    for a in awards:
        line, invite = lines.get(a.get('rfq_line_id')), invites.get(a.get('invite_id'))
        if line is None or invite is None:
            raise QuotationError("A line or supplier isn't on this RFQ.")
        if (invite.pk, line.pk) not in quotes:
            raise QuotationError(f"{invite.supplier.name or invite.supplier.legal_name} didn't quote for {line.item.name}.")
        if line.pk in chosen:
            raise QuotationError(f"{line.item.name} is awarded twice.")
        chosen[line.pk] = invite
    orders = []
    for invite in invites.values():
        mine = [lines[line_id] for line_id, i in chosen.items() if i.pk == invite.pk]
        if not mine:
            if invite.status == RfqSupplier.Status.QUOTED:
                invite.status = RfqSupplier.Status.NOT_AWARDED
                invite.save(update_fields=['status'])
            continue
        order = _wrap(purchase.save_order, membership, PurchaseOrder(), {
            'supplier_id': invite.supplier_id, 'ship_to_id': rfq.ship_to_id, 'order_date': timezone.localdate(),
            'expected_date': rfq.required_by if rfq.required_by and rfq.required_by >= timezone.localdate() else None,
            'payment_terms_days': invite.payment_terms_days,
            'supplier_reference': (invite.their_reference or f"Quote against {rfq.number}")[:100],
            'notes': f"Against {rfq.number} ({rfq.title}).",
            'lines': [{'item_id': l.item_id, 'quantity': l.quantity, 'rate': quotes[(invite.pk, l.pk)].rate,
                       'gst_rate': quotes[(invite.pk, l.pk)].gst_rate, 'description': l.description}
                      for l in sorted(mine, key=lambda l: l.position)],
        })
        for l in mine:
            l.awarded_to = invite
            l.save(update_fields=['awarded_to'])
        invite.status, invite.purchase_order = RfqSupplier.Status.AWARDED, order
        invite.save(update_fields=['status', 'purchase_order'])
        orders.append(order)
    rfq.status, rfq.awarded_at = Rfq.Status.AWARDED, timezone.now()
    rfq.save(update_fields=['status', 'awarded_at'])
    return orders


@transaction.atomic
def cancel_rfq(membership, rfq, reason):
    rfq = _locked_rfq(rfq, Rfq.Status.DRAFT, Rfq.Status.SENT)
    _check(membership, 'po.create')
    if rfq.status == Rfq.Status.SENT and not (reason or '').strip():
        raise QuotationError("Say why; the suppliers asked will see it.")
    rfq.status, rfq.cancel_reason = Rfq.Status.CANCELLED, (reason or '').strip()[:300]
    rfq.save(update_fields=['status', 'cancel_reason'])
    return rfq
