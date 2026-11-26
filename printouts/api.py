"""/api/v1 PDF downloads. A document can be printed by the organization
that issued it, and by the other organization in the deal:

- tax invoice and delivery challan: the seller, and the buyer when it came
  from the buyer's purchase order on MakeSetu (posted invoices only);
- purchase order: the buyer, and the supplier once it's issued.

Each side needs a role that does that kind of work (purchase or sales)."""
from django.http import Http404, HttpResponse
from ninja import Router
from ninja.errors import HttpError

from adjustments.models import CreditDebitNote
from organizations import access
from organizations.app_api import membership_for
from purchase.api import VIEW_PERMISSIONS as PURCHASE_ROLES
from purchase.models import PurchaseOrder
from sales.api import VIEW_PERMISSIONS as SALES_ROLES
from sales.models import Dispatch, SalesInvoice

from . import render

router = Router(tags=['printouts'])


def _has_any(membership, codes):
    return any(access.check(membership, code) for code in codes)


def _pdf(data, filename):
    response = HttpResponse(data, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    return response


def _safe(name):
    return ''.join(c if c.isalnum() or c in '-_' else '-' for c in name)


@router.get('/sales-invoices/{invoice_id}/pdf')
def invoice_pdf(request, invoice_id: int, copies: int = 1):
    """`copies=3` adds the duplicate (transporter) and triplicate (supplier) copies."""
    membership = membership_for(request)
    company = membership.company
    invoice = (SalesInvoice.objects.select_related('company', 'customer', 'order').filter(pk=invoice_id).first())
    if invoice is None:
        raise Http404
    seller_side = invoice.company_id == company.pk and _has_any(membership, SALES_ROLES)
    buyer_side = (invoice.customer_id == company.pk and invoice.status != SalesInvoice.Status.DRAFT
                  and invoice.order.purchase_order_id and _has_any(membership, PURCHASE_ROLES))
    if not (seller_side or buyer_side):
        raise Http404
    if copies not in (1, 3):
        raise HttpError(400, "copies must be 1 or 3.")
    return _pdf(render.tax_invoice(invoice, copies=copies), f"{_safe(invoice.number or f'draft-{invoice.pk}')}.pdf")


@router.get('/dispatches/{dispatch_id}/pdf')
def challan_pdf(request, dispatch_id: int):
    membership = membership_for(request)
    company = membership.company
    dispatch = Dispatch.objects.select_related('company', 'order__customer', 'warehouse__address').filter(pk=dispatch_id).first()
    if dispatch is None:
        raise Http404
    seller_side = dispatch.company_id == company.pk and _has_any(membership, SALES_ROLES)
    buyer_side = (dispatch.order.customer_id == company.pk and dispatch.order.purchase_order_id
                  and _has_any(membership, PURCHASE_ROLES))
    if not (seller_side or buyer_side):
        raise Http404
    return _pdf(render.delivery_challan(dispatch), f"{_safe(dispatch.number)}.pdf")


@router.get('/purchase-orders/{order_id}/pdf')
def purchase_order_pdf(request, order_id: int):
    membership = membership_for(request)
    company = membership.company
    order = PurchaseOrder.objects.select_related('company', 'supplier', 'ship_to__address', 'approved_by').filter(pk=order_id).first()
    if order is None:
        raise Http404
    buyer_side = order.company_id == company.pk and _has_any(membership, PURCHASE_ROLES)
    supplier_side = (order.supplier_id == company.pk
                     and order.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.PENDING_APPROVAL)
                     and _has_any(membership, SALES_ROLES))
    if not (buyer_side or supplier_side):
        raise Http404
    return _pdf(render.purchase_order(order), f"{_safe(order.number)}.pdf")


@router.get('/notes/{note_id}/pdf')
def note_pdf(request, note_id: int):
    """Credit and debit notes issued to customers: the issuer, and the
    customer once posted when the deal came from their PO on MakeSetu.
    (Notes received from suppliers are the supplier's documents to print.)"""
    membership = membership_for(request)
    company = membership.company
    note = (CreditDebitNote.objects.select_related('company', 'party', 'sales_invoice__order', 'warehouse')
            .filter(pk=note_id, side=CreditDebitNote.Side.SALES).first())
    if note is None:
        raise Http404
    seller_side = note.company_id == company.pk and _has_any(membership, SALES_ROLES)
    buyer_side = (note.party_id == company.pk and note.status != CreditDebitNote.Status.DRAFT
                  and note.sales_invoice.order.purchase_order_id and _has_any(membership, PURCHASE_ROLES))
    if not (seller_side or buyer_side):
        raise Http404
    return _pdf(render.credit_debit_note(note), f"{_safe(note.number or f'draft-note-{note.pk}')}.pdf")
