"""Relevant RFQs received per month, for supplier plans.

Each month a supplier company receives up to its plan's
rfqs_received_per_month open RFQs, best capability match first. Receiving
one records an RFQReceipt; the inbox shows received RFQs, new-RFQ alerts
only go out for RFQs being received, and opening an RFQ's link receives it
if there's quota left. Past the quota, further RFQs wait (shown as a count
with an upgrade prompt) until next month or a bigger plan.
"""
from . import access, catalog
from .models import RFQReceipt


def _candidates(supplier):
    """Open RFQs the supplier hasn't received, quoted on or declined."""
    from marketplace import services
    return (
        services.open_requirements_for(supplier)
        .exclude(quote__supplier=supplier).exclude(declines__supplier=supplier)
        .exclude(receipts__supplier=supplier)
    )


def received(queryset, supplier):
    """`queryset` (of Requirements) narrowed to the ones `supplier` received."""
    return queryset.filter(receipts__supplier=supplier).distinct()


def is_received(supplier, requirement):
    return RFQReceipt.objects.filter(supplier=supplier, requirement=requirement).exists()


def deliver(supplier):
    """Receives as many waiting RFQs as this month's quota allows, best
    match first. Returns how many were received."""
    from marketplace import services
    if supplier is None:
        return 0
    room = access.remaining(supplier, catalog.RFQS_RECEIVED_PER_MONTH)
    if room == 0:
        return 0
    waiting = list(_candidates(supplier).order_by('-created_at')[:500])
    if not waiting:
        return 0
    matches = services.bulk_match_percent(waiting, supplier)
    waiting.sort(key=lambda r: (matches.get(r.pk) or -1, r.created_at), reverse=True)
    if room is not None:
        waiting = waiting[:room]
    RFQReceipt.objects.bulk_create([RFQReceipt(supplier=supplier, requirement=r) for r in waiting], ignore_conflicts=True)
    return len(waiting)


def receive(supplier, requirement):
    """Makes sure `supplier` has received `requirement`, using one of this
    month's RFQs if it hasn't yet. False when the quota is used up."""
    if is_received(supplier, requirement):
        return True
    if not access.has_room(supplier, catalog.RFQS_RECEIVED_PER_MONTH):
        return False
    RFQReceipt.objects.get_or_create(supplier=supplier, requirement=requirement)
    return True


def waiting_count(supplier):
    """Open RFQs the supplier would see on a bigger quota."""
    return _candidates(supplier).count() if supplier is not None else 0
