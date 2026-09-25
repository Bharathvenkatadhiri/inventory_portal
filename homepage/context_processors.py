from accounts.models import ManufacturerProfile, ConsumerProfile
from marketplace import services
from marketplace.models import Order


LIVE_KEYS = (
    'topbar_unread_notifications', 'sidebar_unread_message_threads', 'sidebar_new_rfq_count',
    'sidebar_active_orders_count', 'sidebar_open_rfq_count', 'sidebar_quotes_to_review_count',
)


def dashboard_sidebar_counts(request):
    """Real (never fabricated) badge counts and the topbar's company-name
    label, available on every page that extends dashboard_base.html — not
    just the ones that happen to compute them for their own stats.

    Also returns `live_sig`, a short hash of everything the bell and badges
    show. The page's live-update poller sends it back, and the server only
    returns fresh markup when it has changed."""
    counts = _counts(request)
    if counts:
        counts['live_sig'] = services.signature(
            [counts.get(key) for key in LIVE_KEYS]
            + [(event['key'], event['unread']) for event in counts['topbar_notifications']]
        )
    return counts


def _counts(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}

    feed = services.cached_notification_feed(user)
    counts = {
        'sidebar_unread_message_threads': services.unread_thread_count(user),
        'topbar_unread_notifications': sum(1 for event in feed if event['unread']),
        'topbar_notifications': feed[:6],
    }

    if user.role == 'manufacturer':
        supplier = ManufacturerProfile.objects.filter(user=user).first()
        if supplier is None:
            return counts
        counts['sidebar_company_name'] = supplier.companyname
        new_rfq_count = services.open_requirements_for(supplier).exclude(
            quote__supplier=supplier
        ).exclude(declines__supplier=supplier).count()
        active_orders_count = Order.objects.filter(supplier=supplier).exclude(
            status__in=['completed', 'cancelled']
        ).count()
        counts.update({
            'sidebar_new_rfq_count': new_rfq_count,
            'sidebar_active_orders_count': active_orders_count,
        })
    else:
        customer = ConsumerProfile.objects.filter(user=user).first()
        if customer:
            counts['sidebar_company_name'] = customer.Name
        counts.update({
            'sidebar_open_rfq_count': services.buyer_open_requirements(user).count(),
            'sidebar_quotes_to_review_count': services.buyer_pending_quotes(user).count(),
        })

    return counts
