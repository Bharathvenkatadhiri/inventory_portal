"""QC. When the plan includes QC and an item needs inspection:

- receiving it (purchase.services.post_receipt) or producing it
  (production.services.record) puts the quantity in the company's
  'Under inspection' location instead of the store, and opens a lot;
- an inspection moves the accepted part to the lot's store and the
  rejected part to 'Rejected goods', at the cost the lot came in at.

Under-inspection and rejected stock can't be picked for dispatch,
production or receipts (inventory.services.store), so it can't be used
by mistake. Rejected goods can go back to the supplier with a purchase
return note from the 'Rejected goods' location.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from inventory import batches
from inventory import services as inventory
from inventory.models import StockEntry, Warehouse
from plans import erp
from purchase.services import _decimal, next_number

from .models import Inspection, InspectionLot

ZERO = Decimal('0')
Lot = InspectionLot


class QualityError(Exception):
    """A change that isn't allowed; the message says why."""


def needs_inspection(company, item):
    return bool(item.inspection_required and item.tracks_stock and erp.includes(company.erp_plan, erp.QC))


def hold(company):
    return inventory.system_location(company, Warehouse.Kind.QC_HOLD)


def open_lots(company, *, item, rows, rate, destination, source, reference, received_on,
              receipt_line=None, production_entry=None, supplier=None):
    """Called inside the receipt's or production's transaction, after its
    stock entries (`rows`: [(batch, quantity)], from inventory.batches)
    have gone into the 'Under inspection' location. A batch-tracked item
    gets a lot per batch, as each batch or heat is inspected on its own;
    serial numbers share one lot."""
    groups = [[row] for row in rows] if item.by_batch else [rows]
    lots = []
    for group in groups:
        lot = Lot.objects.create(
            company=company, number=next_number(company, 'erp_qc', 'QC', received_on), source=source, reference=reference,
            receipt_line=receipt_line, production_entry=production_entry, supplier=supplier, item=item,
            quantity=sum((q for _b, q in group), ZERO), rate=rate, destination=destination, received_on=received_on,
        )
        lot.batches.set([b for b, _q in group if b])
        lots.append(lot)
    return lots


def _move(company, lot, rows, to, kind, day, user, note):
    """Out of 'Under inspection', into `to`, at the lot's cost."""
    source = hold(company)
    for warehouse, sign in ((source, -1), (to, 1)):
        batches.write(company, lot.item, warehouse, kind, rows, sign=sign, rate=lot.rate, entry_date=day,
                      reference=lot.number, note=note, user=user)


@transaction.atomic
def inspect(membership, lot, data):
    """Records QC's verdict on some or all of what's waiting in a lot."""
    company = membership.company
    lot = Lot.objects.select_for_update().select_related('item', 'destination').get(pk=lot.pk)
    if lot.status in (Lot.Status.DONE, Lot.Status.CANCELLED):
        raise QualityError(f"This lot is {lot.get_status_display().lower()}.")
    accepted = _decimal(data.get('accepted') or 0, 'the accepted quantity')
    rejected = _decimal(data.get('rejected') or 0, 'the rejected quantity')
    if accepted < 0 or rejected < 0:
        raise QualityError("Quantities can't be negative.")
    if accepted + rejected <= 0:
        raise QualityError("Enter how many passed or failed.")
    pending = lot.pending_quantity
    if accepted + rejected > pending:
        raise QualityError(f"Only {pending.normalize():f} {lot.item.unit} is waiting for inspection in this lot.")
    day = data.get('inspected_on') or timezone.localdate()
    if day > timezone.localdate():
        raise QualityError("The inspection date can't be in the future.")
    if day < lot.received_on:
        raise QualityError("It can't be inspected before it arrived.")
    remarks = (data.get('remarks') or '').strip()[:300]
    if rejected and not remarks:
        raise QualityError("Say what was wrong with the rejected ones.")

    if not lot.destination.is_active:
        raise QualityError(f"{lot.destination.name} is inactive; make it active to pass goods into it.")
    Inspection.objects.create(lot=lot, inspected_on=day, accepted=accepted, rejected=rejected, remarks=remarks, inspector=membership.user)
    # Serial-numbered goods: QC says which units failed; the rest pass.
    within = list(lot.batches.values_list('pk', flat=True)) if lot.item.tracked else None
    failed = [{'number': n} for n in data.get('rejected_serials') or [] if str(n).strip()]
    if lot.item.by_serial and rejected and len(failed) != rejected:
        raise QualityError(f"Enter the serial numbers of the {rejected.normalize():f} that failed.")
    if failed and not lot.item.by_serial:
        raise QualityError(f"{lot.item.name} doesn't have serial numbers.")
    if rejected:
        rows = batches.outgoing(company, lot.item, hold(company), rejected, failed, within=within, allow_expired=True)
        _move(company, lot, rows, inventory.system_location(company, Warehouse.Kind.REJECTED), StockEntry.Kind.QC_REJECTED,
              day, membership.user, f"Rejected ({lot.reference}): {remarks}")
    if accepted:
        rows = batches.outgoing(company, lot.item, hold(company), accepted, within=within, allow_expired=True)
        _move(company, lot, rows, lot.destination, StockEntry.Kind.QC_ACCEPTED, day, membership.user,
              f"Passed inspection ({lot.reference})")
    lot.accepted_quantity += accepted
    lot.rejected_quantity += rejected
    lot.status = Lot.Status.DONE if lot.pending_quantity == 0 else Lot.Status.PARTLY
    lot.save(update_fields=['accepted_quantity', 'rejected_quantity', 'status'])
    return lot


def cancel_for_production(entry):
    """A production entry being cancelled: its lots go too, unless QC has
    already inspected them. Returns nothing; raises QualityError."""
    lots = Lot.objects.select_for_update().filter(production_entry=entry)
    if any(l.status != Lot.Status.PENDING for l in lots):
        raise QualityError("QC has already inspected this production; it can't be cancelled.")
    lots.update(status=Lot.Status.CANCELLED)
