from accounts import team
from marketplace import services
from marketplace.models import Order


LIVE_KEYS = (
    'topbar_unread_notifications', 'sidebar_unread_message_threads', 'sidebar_new_rfq_count',
    'sidebar_active_orders_count', 'sidebar_open_rfq_count', 'sidebar_quotes_to_review_count',
    'sidebar_pending_approvals_count',
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

    profile = team.company(user)
    # Buyer-side viewers are read-only: dashboard_base.html hides every
    # write control. Supplier-side viewers may update production, dispatch,
    # quality documents and invoices, so only marked controls are hidden.
    counts['is_viewer'] = team.is_viewer(user)
    counts['read_only'] = counts['is_viewer'] and team.company_kind(user) == team.BUYER
    if profile is not None:
        counts['sidebar_team_role'] = team.role_label(user)
        if team.can_manage_company(user):
            from marketplace import approvals
            counts['sidebar_pending_approvals_count'] = approvals.pending_for(profile).count()

    if user.role == 'manufacturer':
        supplier = team.supplier_profile(user)
        if supplier is None:
            return counts
        counts['sidebar_company_name'] = supplier.companyname
        from plans import rfq_inbox
        new_rfq_count = rfq_inbox.received(services.open_requirements_for(supplier), supplier).exclude(
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
        customer = team.buyer_profile(user)
        if customer:
            counts['sidebar_company_name'] = customer.Name
        counts.update({
            'sidebar_open_rfq_count': services.buyer_open_requirements(user).count(),
            'sidebar_quotes_to_review_count': services.buyer_pending_quotes(user).count(),
        })

    return counts


def portal_feedback_prompt(request):
    """`needs_portal_feedback`: a signed-in buyer or manufacturer who hasn't
    rated MakeSetu yet, so logging out asks them first. Lazy — only
    queried on pages that render the logout button."""
    user = getattr(request, 'user', None)

    def needs():
        from homepage.models import PortalFeedback
        return (
            bool(user and user.is_authenticated) and not user.is_staff
            and user.role in ('consumer', 'manufacturer')
            and not PortalFeedback.objects.filter(user=user).exists()
        )
    return {'needs_portal_feedback': needs}
