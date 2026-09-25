"""Shared business logic for the buyer and manufacturer dashboards.

Kept separate from models.py because match-percent computation needs data
from both `accounts` (ManufacturerProfile capabilities/materials) and
`marketplace` (RequirementPart) — putting it as a model method on either
side would need that side to import the other, risking circularity.
"""
import hashlib
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Exists, Max, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import ManufacturerProfile, ConsumerProfile
from .models import (
    Requirement, Quote, Order, OrderEvent,
    ProductionUpdate, MessageThread, Message, NotificationRead,
    RequirementAmendment, AmendmentResponse,
)

logger = logging.getLogger(__name__)

# RequirementPart.TECHNOLOGY_TYPES (broad RFQ process categories, e.g.
# "Milling") and ManufacturingTech.TECH_CHOICES (granular machine
# capabilities, e.g. "5_axis_milling") are different vocabularies — this
# maps an RFQ's process to the set of capability keys that satisfy it.
PROCESS_CAPABILITY_MAP = {
    'Milling': {'milling', '5_axis_milling', 'form_milling', 'full_range_milling', 'hsc_milling', 'engraver_milling'},
    'Turning': {'turning', 'full_range_turning'},
    'Full-range turning': {'full_range_turning'},
    'Anodizing': {'anodizing', 'anodizing_partner'},
}

# RequirementPart.MATERIAL_TYPES labels -> MaterialCapability.material_type keys.
MATERIAL_KEY_MAP = {
    'Structural steel': 'structural_steel',
    'Stainless steel': 'stainless_steel',
    'Aluminium': 'aluminium',
    'Case hardening': 'case_hardening',
}

PROCESS_WEIGHT = 60
MATERIAL_WEIGHT = 40


def _part_score(part, capability_keys, material_keys):
    score = 0
    if PROCESS_CAPABILITY_MAP.get(part.technology, set()) & capability_keys:
        score += PROCESS_WEIGHT
    if MATERIAL_KEY_MAP.get(part.Material) in material_keys:
        score += MATERIAL_WEIGHT
    return score


def compute_match_percent(requirement, manufacturer):
    """Real match score (0-100) for one requirement against one
    manufacturer's tagged capabilities/materials — not a placeholder.
    Returns None (not 0) when the manufacturer hasn't configured either
    yet, so an incomplete profile doesn't look like a bad match — it looks
    like "finish your profile"."""
    capability_keys = set(manufacturer.capabilities.values_list('technology_type', flat=True))
    material_keys = set(manufacturer.materials.values_list('material_type', flat=True))
    if not capability_keys and not material_keys:
        return None

    parts = list(requirement.requirement_parts.all())
    if not parts:
        return None

    total = sum(_part_score(part, capability_keys, material_keys) for part in parts)
    return round(total / len(parts))


def bulk_match_percent(requirements, manufacturer):
    """Same computation, batched: fetches the manufacturer's
    capability/material key sets once, then loops in Python over an
    already-small (paginated) list of requirements — avoids N+1 queries in
    list templates."""
    capability_keys = set(manufacturer.capabilities.values_list('technology_type', flat=True))
    material_keys = set(manufacturer.materials.values_list('material_type', flat=True))
    results = {}
    for requirement in requirements:
        if not capability_keys and not material_keys:
            results[requirement.pk] = None
            continue
        parts = list(requirement.requirement_parts.all())
        if not parts:
            results[requirement.pk] = None
            continue
        total = sum(_part_score(part, capability_keys, material_keys) for part in parts)
        results[requirement.pk] = round(total / len(parts))
    return results


def open_requirements_for(manufacturer):
    """The set of RFQs open for a manufacturer to quote on: not expired,
    not deleted, and not already awarded to someone else. Extracted from
    RequirementListView.get_queryset() so the dashboard stats and the RFQ
    inbox can't drift apart the way HomeView's old count did."""
    return Requirement.objects.filter(
        end_date__gte=timezone.now(), is_deleted=False
    ).exclude(quote__is_selected=True).distinct()


def totals_by_currency(pairs):
    """Sums (currency, amount) pairs per currency, largest first. Each RFQ
    has its own quote currency, so amounts must never be added across
    currencies into a single figure."""
    totals = {}
    for currency, amount in pairs:
        key = currency or 'INR'
        totals[key] = totals.get(key, 0) + amount
    return [{'currency': c, 'amount': a} for c, a in sorted(totals.items(), key=lambda item: item[1], reverse=True)]


def award_quote(requirement, quote):
    """Awards `quote`: it's selected, the RFQ is marked Approved, every other
    undecided quote is rejected (their suppliers get "not selected"), the
    order is created, and any open change request is closed. Suppliers who
    never quoted see the RFQ leave their inbox (awarded_elsewhere_for).
    Call inside a transaction. Returns (order, number of quotes rejected)."""
    now = timezone.now()
    quote.status = 'Approved'
    quote.is_selected = True
    quote.decided_at = now
    quote.save()
    requirement.status = 'Approved'
    requirement.save()
    rejected = Quote.objects.filter(
        requirement=requirement, status__isnull=True,
    ).exclude(pk=quote.pk).update(status='Rejected', decided_at=now)
    order = create_award_order(requirement, quote)
    for amendment in requirement.amendments.filter(status=RequirementAmendment.PENDING):
        amendment.close(RequirementAmendment.CLOSED)
    AmendmentResponse.objects.filter(
        amendment__requirement=requirement,
        status__in=[AmendmentResponse.PENDING, AmendmentResponse.ACCEPTED],
    ).update(status=AmendmentResponse.CLOSED)
    return order, rejected


def create_award_order(requirement, quote):
    """Creates the order the moment the buyer selects a quote, so it can be
    tracked (updates, QC, shipment, payment) from award onwards. It used to
    be created only when the supplier marked the RFQ Completed."""
    customer = ConsumerProfile.objects.filter(user=requirement.user).first()
    if customer is None:
        logger.warning("Requirement #%s awarded but its buyer has no ConsumerProfile — no order created", requirement.pk)
        return None
    order = Order.objects.create(requirement=requirement, quote=quote, supplier=quote.supplier, customer=customer)
    order.mark_quoted()
    order.select_quote(note=f"Quote #{quote.pk} awarded by the buyer")
    order.save()
    return order


def sync_after_order_transition(order):
    """Keeps the RFQ's status and its message threads in step with the
    order, so the two state machines can't contradict each other."""
    requirement = order.requirement
    if order.status == 'in_production' and requirement.status == 'Approved':
        requirement.status = 'Production'
        requirement.save(update_fields=['status', 'updated_at'])
    elif order.status == 'completed' and requirement.status in ('Approved', 'Production'):
        requirement.status = 'Completed'
        requirement.save(update_fields=['status', 'updated_at'])
    if order.status in ('completed', 'cancelled'):
        closed = requirement.message_threads.filter(closed_at__isnull=True).update(closed_at=timezone.now())
        if closed:
            logger.info("Closed %d message thread(s) after order #%s was %s", closed, order.billno, order.status)


def signature(parts):
    """Short, stable hash of whatever a live-updating section shows. Pollers
    send it back so the server can answer "nothing changed" with a 204."""
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


def thread_signature(thread):
    stats = thread.messages.aggregate(total=Count('pk'), last=Max('pk'), deleted=Count('pk', filter=Q(is_deleted=True)))
    return signature([stats['total'], stats['last'], stats['deleted'], thread.is_closed])


def quotes_signature(requirement):
    stats = Quote.objects.filter(requirement=requirement, is_deleted=False).aggregate(
        total=Count('pk'), changed=Max('updated_at'),
        rejected=Count('pk', filter=Q(status='Rejected')), selected=Max('pk', filter=Q(is_selected=True)),
    )
    return signature([stats['total'], stats['changed'], stats['rejected'], stats['selected'], requirement.status])


def _same(old, new):
    return (old in (None, '') and new in (None, '')) or old == new


def diff_rfq_edit(requirement, form, formset):
    """Compares a validated RFQ edit with what's stored, without saving.

    Returns (changes, new_end_date, unsupported):
    - changes: the RequirementAmendment.changes dict ({} if nothing that
      affects pricing changed)
    - new_end_date: the new due date if it changed (applied straight away;
      it doesn't affect pricing), else None
    - unsupported: labels of edits a change request can't carry (adding or
      removing parts, replacing files)
    """
    original = Requirement.objects.get(pk=requirement.pk)
    changes, unsupported = {}, []

    requirement_diff = {}
    for field in RequirementAmendment.REQUIREMENT_FIELDS:
        old, new = getattr(original, field), form.cleaned_data.get(field)
        if not _same(old, new):
            requirement_diff[field] = {'old': old, 'new': new}
    if requirement_diff:
        changes['requirement'] = requirement_diff
    if 'file' in form.changed_data:
        unsupported.append('the master file')

    originals = {part.pk: part for part in original.requirement_parts.all()}
    part_diffs = {}
    for part_form in formset.forms:
        if not part_form.has_changed():
            continue
        existing = originals.get(part_form.instance.pk) if part_form.instance.pk else None
        if part_form.cleaned_data.get('DELETE'):
            unsupported.append('removing parts')
            continue
        if existing is None:
            unsupported.append('adding parts')
            continue
        if 'file' in part_form.changed_data:
            unsupported.append('part drawings')
        diff = {}
        for field in RequirementAmendment.PART_FIELDS:
            old, new = getattr(existing, field), part_form.cleaned_data.get(field)
            if not _same(old, new):
                diff[field] = {'old': old, 'new': new}
        if diff:
            part_diffs[str(existing.pk)] = diff
    if part_diffs:
        changes['parts'] = part_diffs

    new_end = form.cleaned_data.get('end_date')
    new_end_date = new_end if not _same(original.end_date, new_end) else None
    return changes, new_end_date, sorted(set(unsupported))


def rfq_allowance(user):
    """(monthly RFQ limit or None for unlimited, RFQs posted so far this
    month) from the user's plan. Users without a plan row get Basic's
    limit, the same tier registration assigns."""
    from accounts.models import SubscriptionPlan
    from core.settings import subscription_plan_details

    plan = SubscriptionPlan.objects.filter(user_profile=user, is_active=True).first()
    raw_limit = plan.rfq_limit if plan else subscription_plan_details['basic']['rfq_limit']
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = None  # "unlimited"
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Deleted RFQs still count, otherwise delete-and-repost would bypass the limit.
    used = Requirement.objects.filter(user=user, created_at__gte=month_start).count()
    return limit, used


def expired_without_quotes(user):
    """The buyer's unawarded RFQs whose due date passed with no quotes."""
    submitted = Quote.objects.filter(requirement=OuterRef('pk'), is_deleted=False, is_draft=False)
    return (
        Requirement.objects.filter(user=user, is_deleted=False, status__isnull=True, end_date__lt=timezone.now())
        .exclude(Exists(submitted))
        .order_by('-end_date')[:20]
    )


AWARDED_ELSEWHERE_WINDOW = timedelta(days=14)


def awarded_elsewhere_for(supplier):
    """RFQs this supplier could see in their inbox but never quoted on,
    that another manufacturer has since won. They drop out of the inbox at
    award (open_requirements_for excludes awarded RFQs); this is what lets
    the supplier know why. Only recent awards, only RFQs posted while the
    supplier was on the platform, and not ones they'd already declined."""
    return (
        Requirement.objects.filter(is_deleted=False, created_at__gte=supplier.created_at)
        .exclude(quote__supplier=supplier)
        .exclude(declines__supplier=supplier)
        .annotate(awarded_at=Max('quote__decided_at', filter=Q(quote__is_selected=True)))
        .filter(awarded_at__gte=timezone.now() - AWARDED_ELSEWHERE_WINDOW)
        .order_by('-awarded_at')[:20]
    )


def quarter_start(now):
    quarter = (now.month - 1) // 3
    return now.replace(month=quarter * 3 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def buyer_open_requirements(user):
    """RFQs the buyer posted that haven't reached a final state yet."""
    return Requirement.objects.filter(user=user, is_deleted=False).exclude(status__in=['Completed', 'Rejected'])


def buyer_pending_quotes(user):
    """Quotes sitting on the buyer's desk awaiting a decision, across every
    RFQ they've posted. Quotes the buyer sent back for revision are waiting
    on the supplier, not the buyer, so they're left out until revised."""
    return Quote.objects.filter(
        requirement__user=user, requirement__is_deleted=False, is_deleted=False,
        is_draft=False, is_selected=False, status__isnull=True, revision_requested_at__isnull=True,
    ).select_related('requirement', 'supplier')


def buyer_action_items(user):
    """Real, derived "needs your attention" items for the buyer dashboard —
    never fabricated: an order the FSM has actually put in payment_pending
    (the buyer is the one who has to act next), a production update with a
    photo posted recently, and supplier messages the buyer hasn't read."""
    orders = Order.objects.filter(customer__user=user).exclude(status__in=['completed', 'cancelled'])
    items = []
    for order in orders.filter(status='payment_pending').select_related('requirement', 'supplier'):
        items.append({
            'title': f"Payment due · ORD-{order.billno}",
            'detail': f"{order.supplier.companyname or order.supplier} is waiting on payment for {order.requirement.title} to keep production moving.",
            'url': reverse('order-detail', kwargs={'billno': order.billno}),
            'cta': 'Review order',
        })

    recent_cutoff = timezone.now() - timedelta(days=5)
    updates_with_photo = ProductionUpdate.objects.filter(
        order__in=orders, created_at__gte=recent_cutoff,
    ).exclude(photo='').select_related('order', 'order__requirement', 'order__supplier').order_by('-created_at')
    seen_orders = set()
    for update in updates_with_photo:
        if update.order_id in seen_orders:
            continue
        seen_orders.add(update.order_id)
        supplier = update.order.supplier
        items.append({
            'title': f"{supplier.companyname or supplier} shared photos · ORD-{update.order.billno}",
            'detail': f"{update.order.requirement.title}. {update.body}",
            'url': reverse('order-detail', kwargs={'billno': update.order.billno}),
            'cta': 'Review update',
        })

    for requirement in expired_without_quotes(user)[:3]:
        items.append({
            'title': f"No quotes by the due date \u00b7 RFQ-{requirement.pk}",
            'detail': f"{requirement.title} closed on {requirement.end_date:%d %b} with no quotes. Move the due date to reopen it, or delete it.",
            'url': reverse('requirement', kwargs={'pk': requirement.pk}),
            'cta': 'Extend or delete',
        })
    for response in AmendmentResponse.objects.filter(
        amendment__requirement__user=user, status=AmendmentResponse.ACCEPTED,
    ).select_related('amendment', 'quote__supplier')[:3]:
        items.append({
            'title': f"New pricing to review \u00b7 RFQ-{response.amendment.requirement_id}",
            'detail': f"{response.quote.supplier.companyname or response.quote.supplier} accepted your changes. Accepting their new pricing awards them the RFQ.",
            'url': reverse('requirement', kwargs={'pk': response.amendment.requirement_id}) + '#changes',
            'cta': 'Review new pricing',
        })
    unread_threads = MessageThread.objects.filter(
        requirement__user=user, requirement__is_deleted=False, closed_at__isnull=True,
    ).select_related('requirement', 'supplier')
    for thread in unread_threads:
        if len(items) >= 6:
            break
        if thread.unread_count_for(user):
            latest = thread.messages.exclude(sender=user).filter(is_deleted=False).order_by('-created_at').first()
            items.append({
                'title': f"{thread.supplier.companyname or thread.supplier} sent you a message · RFQ-{thread.requirement_id}",
                'detail': (latest.body or f"Shared {latest.attachment_name}") if latest else '',
                'url': reverse('message-thread', kwargs={'pk': thread.pk}),
                'cta': 'Reply',
            })
    return items


def message_thread_rows(user):
    """Every message thread visible to this user (as buyer or supplier),
    newest activity first — shared by the thread list page and the
    thread-detail page's sidebar so they can't drift apart."""
    if getattr(user, 'role', None) == 'manufacturer':
        supplier = ManufacturerProfile.objects.filter(user=user).first()
        threads = MessageThread.objects.filter(supplier=supplier) if supplier else MessageThread.objects.none()
    else:
        threads = MessageThread.objects.filter(requirement__user=user)
    threads = threads.select_related('requirement', 'supplier').prefetch_related('messages')
    rows = [{'thread': t, 'last_message': t.last_message(), 'unread': t.unread_count_for(user)} for t in threads]
    rows.sort(key=lambda row: row['last_message'].created_at if row['last_message'] else row['thread'].created_at, reverse=True)
    return rows


def unread_thread_count(user):
    """Number of message threads (not messages) with something unread for this user."""
    if getattr(user, 'role', None) == 'manufacturer':
        supplier = ManufacturerProfile.objects.filter(user=user).first()
        if supplier is None:
            return 0
        threads = MessageThread.objects.filter(supplier=supplier)
    else:
        threads = MessageThread.objects.filter(requirement__user=user)
    return sum(1 for thread in threads if thread.unread_count_for(user) > 0)


def notification_feed(user, limit=30):
    """Merged, time-sorted feed of real events for the notifications page and
    the topbar bell. Every entry maps to an actual row (quotes, order
    events, messages) and carries a stable `key`
    that NotificationRead uses to remember what this user has read."""
    role = getattr(user, 'role', None)
    events = []

    if role == 'manufacturer':
        supplier = ManufacturerProfile.objects.filter(user=user).first()
        if supplier is None:
            return []
        orders = Order.objects.filter(supplier=supplier)
        threads = MessageThread.objects.filter(supplier=supplier)
        for requirement in open_requirements_for(supplier).exclude(
            quote__supplier=supplier
        ).exclude(declines__supplier=supplier).order_by('-created_at')[:20]:
            events.append({
                'key': f'rfq:{requirement.pk}', 'kind': 'rfq',
                'title': f"New RFQ matched · RFQ-{requirement.pk}",
                'detail': requirement.title,
                'url': reverse('requirement', kwargs={'pk': requirement.pk}),
                'timestamp': requirement.created_at,
            })
        for requirement in awarded_elsewhere_for(supplier):
            events.append({
                'key': f'rfq-closed:{requirement.pk}', 'kind': 'rfq',
                'title': f"RFQ-{requirement.pk} closed \u00b7 awarded to another manufacturer",
                'detail': f"{requirement.title} is no longer open for quotes and has been removed from your RFQ inbox.",
                'url': reverse('requirement', kwargs={'pk': requirement.pk}),
                'timestamp': requirement.awarded_at,
            })
        for response in AmendmentResponse.objects.filter(quote__supplier=supplier).select_related('amendment').order_by('-created_at')[:20]:
            requirement_id = response.amendment.requirement_id
            url = reverse('requirement', kwargs={'pk': requirement_id}) + '#changes'
            events.append({
                'key': f'amendment:{response.pk}', 'kind': 'rfq',
                'title': f"Buyer changed RFQ-{requirement_id} \u00b7 accept with new pricing or reject",
                'detail': ', '.join(row['label'] for row in response.amendment.change_rows())[:120],
                'url': url, 'timestamp': response.created_at,
            })
            if response.buyer_decided_at:
                accepted = response.status == AmendmentResponse.BUYER_ACCEPTED
                events.append({
                    'key': f'amendment-decision:{response.pk}', 'kind': 'quote',
                    'title': f"{'Buyer accepted your updated pricing and awarded you the RFQ' if accepted else 'Buyer kept the original details'} \u00b7 RFQ-{requirement_id}",
                    'detail': 'The RFQ was awarded to you at your new pricing.' if accepted else 'Your earlier quote still stands.',
                    'url': url, 'timestamp': response.buyer_decided_at,
                })
        my_quotes = Quote.objects.filter(supplier=supplier, is_deleted=False).select_related('requirement')
        for quote in my_quotes.filter(revision_requested_at__isnull=False).order_by('-revision_requested_at')[:20]:
            events.append({
                # The timestamp is part of the key so a second request notifies again.
                'key': f'quote-revision:{quote.pk}:{int(quote.revision_requested_at.timestamp())}', 'kind': 'quote',
                'title': f"Revision requested · RFQ-{quote.requirement_id}",
                'detail': quote.revision_note[:120],
                'url': reverse('edit-quote', kwargs={'pk': quote.pk}),
                'timestamp': quote.revision_requested_at,
            })
        for quote in my_quotes.filter(decided_at__isnull=False).order_by('-decided_at')[:20]:
            won = quote.is_selected
            events.append({
                'key': f'quote-decision:{quote.pk}', 'kind': 'quote',
                'title': f"{'Your quote was awarded' if won else 'Your quote was not selected'} · RFQ-{quote.requirement_id}",
                'detail': quote.requirement.title,
                'url': reverse('requirement', kwargs={'pk': quote.requirement_id}),
                'timestamp': quote.decided_at,
            })
    else:
        orders = Order.objects.filter(customer__user=user)
        threads = MessageThread.objects.filter(requirement__user=user)
        for quote in Quote.objects.filter(requirement__user=user, is_deleted=False, is_draft=False).select_related(
            'requirement', 'supplier'
        ).order_by('-created_at')[:20]:
            lead_time = f"{quote.lead_time_value} {quote.get_lead_time_unit_display()}" if quote.lead_time_value else "—"
            events.append({
                'key': f'quote:{quote.pk}', 'kind': 'quote',
                'title': f"New quote on RFQ-{quote.requirement_id}",
                'detail': f"{quote.supplier.companyname or quote.supplier} quoted {quote.requirement.quote_currency} {quote.quote_price} with a {lead_time} lead time.",
                'url': reverse('requirement', kwargs={'pk': quote.requirement_id}),
                'timestamp': quote.created_at,
            })
        for response in AmendmentResponse.objects.filter(
            amendment__requirement__user=user, responded_at__isnull=False,
        ).select_related('amendment', 'quote__supplier').order_by('-responded_at')[:20]:
            supplier_name = response.quote.supplier.companyname or str(response.quote.supplier)
            rejected = response.status == AmendmentResponse.REJECTED
            events.append({
                'key': f'amendment-response:{response.pk}', 'kind': 'rfq',
                'title': f"{supplier_name} {'rejected your changes' if rejected else 'accepted your changes'} \u00b7 RFQ-{response.amendment.requirement_id}",
                'detail': (response.supplier_note or 'No reason given.') if rejected else f"New unit price {response.amendment.requirement.quote_currency} {response.quote_price} \u2014 accepting it awards them the RFQ.",
                'url': reverse('requirement', kwargs={'pk': response.amendment.requirement_id}) + '#changes',
                'timestamp': response.responded_at,
            })
        for requirement in expired_without_quotes(user):
            events.append({
                'key': f'rfq-expired:{requirement.pk}:{int(requirement.end_date.timestamp())}', 'kind': 'rfq',
                'title': f"No quotes by the due date \u00b7 RFQ-{requirement.pk}",
                'detail': f"{requirement.title}: move the due date to reopen it, or delete it.",
                'url': reverse('requirement', kwargs={'pk': requirement.pk}),
                'timestamp': requirement.end_date,
            })
        for quote in Quote.objects.filter(requirement__user=user, is_deleted=False, revised_at__isnull=False).select_related(
            'requirement', 'supplier'
        ).order_by('-revised_at')[:20]:
            events.append({
                'key': f'quote-revised:{quote.pk}:{int(quote.revised_at.timestamp())}', 'kind': 'quote',
                'title': f"Revised quote on RFQ-{quote.requirement_id}",
                'detail': f"{quote.supplier.companyname or quote.supplier} now quotes {quote.requirement.quote_currency} {quote.quote_price}.",
                'url': reverse('requirement', kwargs={'pk': quote.requirement_id}) + '#quotes-section',
                'timestamp': quote.revised_at,
            })

    for event in OrderEvent.objects.filter(order__in=orders).select_related('order__requirement').order_by('-changed_at')[:40]:
        events.append({
            'key': f'order-event:{event.pk}', 'kind': 'order',
            'title': f"ORD-{event.order_id} moved to {event.to_label()}",
            'detail': event.order.requirement.title,
            'url': reverse('order-detail', kwargs={'billno': event.order_id}),
            'timestamp': event.changed_at,
        })

    for thread in threads.select_related('requirement__user', 'supplier'):
        is_buyer = user == thread.requirement.user
        if is_buyer:
            other_party = thread.supplier.companyname or str(thread.supplier)
        else:
            other_party = thread.requirement.user.get_full_name() or thread.requirement.user.email
        # Opening the thread already counts as reading these messages.
        thread_read_at = thread.buyer_last_read_at if is_buyer else thread.supplier_last_read_at
        for message in thread.messages.exclude(sender=user).filter(is_deleted=False).order_by('-created_at')[:5]:
            events.append({
                'key': f'message:{message.pk}', 'kind': 'message',
                'title': f"New message · {other_party}",
                'detail': message.body[:120] or f"Shared {message.attachment_name}",
                'url': reverse('message-thread', kwargs={'pk': thread.pk}),
                'timestamp': message.created_at,
                'seen': bool(thread_read_at and message.created_at <= thread_read_at),
            })

    events.sort(key=lambda event: event['timestamp'], reverse=True)
    events = events[:limit]

    read_keys = set(NotificationRead.objects.filter(
        user=user, key__in=[event['key'] for event in events],
    ).values_list('key', flat=True))
    for event in events:
        event['unread'] = not (event.pop('seen', False) or event['key'] in read_keys)
    return events


NOTIFICATION_CACHE_SECONDS = 15


def _notification_cache_key(user):
    return f"notification-feed:{user.pk}"


def cached_notification_feed(user):
    """The feed behind the topbar bell, which renders on every page. Cached
    briefly per user so each page view doesn't rerun the ~10 feed queries;
    the user's own read actions clear it, so only other people's new
    activity can lag, by at most NOTIFICATION_CACHE_SECONDS."""
    key = _notification_cache_key(user)
    feed = cache.get(key)
    if feed is None:
        feed = notification_feed(user)
        cache.set(key, feed, NOTIFICATION_CACHE_SECONDS)
    return feed


def invalidate_notification_cache(user):
    cache.delete(_notification_cache_key(user))


def mark_notifications_read(user, keys):
    NotificationRead.objects.bulk_create(
        [NotificationRead(user=user, key=key) for key in keys],
        ignore_conflicts=True,
    )
    invalidate_notification_cache(user)


def annotate_quote_badges(quotes):
    """Mutates quote objects in place with real, derived comparison badges
    (lowest price / shortest lead time among the set shown) — never
    fabricated ratings, since the data model doesn't have any."""
    quotes = list(quotes)
    priced = [q for q in quotes if q.quote_price is not None]
    best_price_id = min(priced, key=lambda q: q.quote_price).pk if len(priced) > 1 else None
    timed = [q for q in quotes if q.lead_time_as_timedelta() is not None]
    fastest_id = min(timed, key=lambda q: q.lead_time_as_timedelta()).pk if len(timed) > 1 else None
    for quote in quotes:
        quote.badge_best_price = quote.pk == best_price_id
        quote.badge_fastest = quote.pk == fastest_id
    return quotes


def message_attachment_documents(user):
    """Files shared in the user's conversations, linked through the
    participant-checked download view rather than their storage URL."""
    if getattr(user, 'role', None) == 'manufacturer':
        supplier = ManufacturerProfile.objects.filter(user=user).first()
        threads = MessageThread.objects.filter(supplier=supplier) if supplier else MessageThread.objects.none()
    else:
        threads = MessageThread.objects.filter(requirement__user=user)
    messages = Message.objects.filter(thread__in=threads, is_deleted=False).exclude(attachment='').select_related('sender', 'thread')
    return [{
        'name': message.attachment_name or 'attachment',
        'url': reverse('message-attachment', kwargs={'pk': message.pk}),
        'type': 'Attachment',
        'linked_label': f'RFQ-{message.thread.requirement_id}',
        'linked_url': reverse('message-thread', kwargs={'pk': message.thread_id}),
        'uploaded_by': message.sender.get_full_name() or message.sender.email,
        'date': message.created_at,
    } for message in messages]


def buyer_documents(user):
    """All real files across a buyer's RFQs, quotes and orders, in one
    list. No separate Document model — the files already live on
    Requirement/RequirementPart/Quote/ProductionUpdate, so this just
    collects them rather than duplicating storage."""
    docs = []
    requirements = list(Requirement.objects.filter(user=user, is_deleted=False).prefetch_related('requirement_parts'))

    for requirement in requirements:
        if requirement.file:
            docs.append({
                'name': requirement.file.name.rsplit('/', 1)[-1], 'url': requirement.file.url, 'type': 'Drawing',
                'linked_label': f'RFQ-{requirement.pk}', 'linked_url': reverse('requirement', kwargs={'pk': requirement.pk}),
                'uploaded_by': requirement.user.get_full_name() or requirement.user.email, 'date': requirement.created_at,
            })
        for part in requirement.requirement_parts.all():
            if part.file:
                docs.append({
                    'name': part.file.name.rsplit('/', 1)[-1], 'url': part.file.url, 'type': 'Drawing',
                    'linked_label': f'RFQ-{requirement.pk}', 'linked_url': reverse('requirement', kwargs={'pk': requirement.pk}),
                    'uploaded_by': requirement.user.get_full_name() or requirement.user.email, 'date': part.created_at,
                })

    quotes = Quote.objects.filter(requirement__user=user, is_deleted=False).select_related('requirement', 'supplier')
    for quote in quotes:
        if quote.quote_file:
            docs.append({
                'name': quote.quote_file.name.rsplit('/', 1)[-1], 'url': quote.quote_file.url, 'type': 'Quote',
                'linked_label': f'RFQ-{quote.requirement_id}', 'linked_url': reverse('requirement', kwargs={'pk': quote.requirement_id}),
                'uploaded_by': quote.supplier.companyname or str(quote.supplier), 'date': quote.created_at,
            })

    orders = Order.objects.filter(customer__user=user).select_related('requirement')
    for order in orders:
        for update in order.updates.select_related('author').all():
            if update.document:
                docs.append({
                    'name': update.document.name.rsplit('/', 1)[-1], 'url': update.document.url, 'type': 'Certificate',
                    'linked_label': f'ORD-{order.billno}', 'linked_url': reverse('order-detail', kwargs={'billno': order.billno}),
                    'uploaded_by': update.author.get_full_name() if update.author else '—', 'date': update.created_at,
                })

    docs.sort(key=lambda doc: doc['date'], reverse=True)
    return docs
