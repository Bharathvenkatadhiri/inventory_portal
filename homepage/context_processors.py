from accounts.models import ManufacturerProfile
from marketplace import services
from marketplace.models import Order


def dashboard_sidebar_counts(request):
    """Real (never fabricated) badge counts for the manufacturer dashboard
    sidebar, available on every page that extends dashboard_base.html —
    not just the ones that happen to compute them for their own stats."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or getattr(user, 'role', None) != 'manufacturer':
        return {}

    supplier = ManufacturerProfile.objects.filter(user=user).first()
    if supplier is None:
        return {}

    new_rfq_count = services.open_requirements_for(supplier).exclude(
        quote__supplier=supplier
    ).exclude(declines__supplier=supplier).count()
    active_orders_count = Order.objects.filter(supplier=supplier).exclude(
        status__in=['completed', 'cancelled']
    ).count()

    return {
        'sidebar_new_rfq_count': new_rfq_count,
        'sidebar_active_orders_count': active_orders_count,
    }
