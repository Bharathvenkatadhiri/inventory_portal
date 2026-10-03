"""Can this user do this, on this company's plan, right now?

    decision = access.check(user, 'rfq.create')
    if not decision:
        messages.error(request, decision.reason)

check() answers in three steps, in this order:

1. Role — does the user's role allow it? (accounts.team.ROLE_PERMISSIONS)
2. Plan — does the company's plan include the feature? (PLAN_RULES)
3. Limit — is there room left under the plan's limit? (PLAN_RULES)

The subscription and plan belong to the company (held on the owner's
account); roles belong to the people on it. Nothing here decides roles, and
accounts.team decides nothing about plans.
"""
from dataclasses import dataclass

from django.utils import timezone

from accounts import team

from . import catalog
from .catalog import BASIC, BUYER, STANDARD, SUPPLIER

# action -> what the plan must allow, per side: a feature (with the level
# needed) and/or a limit with room for one more. Actions not listed need
# nothing from the plan.
PLAN_RULES = {
    BUYER: {
        'rfq.create': {'limit': catalog.RFQS_PER_MONTH},
        'quote.request_accept': {'feature': ('approval_workflows', STANDARD)},
        'team.invite': {'limit': catalog.USERS},
        'analytics.view': {'feature': ('advanced_reports', STANDARD)},
        'export': {'feature': ('export', STANDARD)},
        'invoice.list': {'feature': ('invoice_management', STANDARD)},
        'orders.filter': {'feature': ('order_management', STANDARD)},
        'directory.filter': {'feature': ('supplier_directory', STANDARD)},
        'suppliers.suggest': {'feature': ('supplier_matching', STANDARD)},
        'quotes.compare': {'feature': ('quote_comparison', catalog.ADVANCED)},
        'audit.filter': {'feature': ('audit_logs', catalog.ADVANCED)},
    },
    SUPPLIER: {
        'quote.submit': {'limit': catalog.QUOTES_PER_MONTH},
        'quote.templates': {'feature': ('quote_templates', STANDARD)},
        'quotes.filter': {'feature': ('quote_management', STANDARD)},
        'quotes.bulk': {'feature': ('quote_management', catalog.ADVANCED)},
        'quotes.history': {'feature': ('quote_management', catalog.ADVANCED)},
        'quotes.compare': {'feature': ('quote_comparison', STANDARD)},
        'team.invite': {'limit': catalog.USERS},
        'analytics.sales': {'feature': ('quote_analytics', STANDARD)},
        'analytics.operations': {'feature': ('analytics', STANDARD)},
        'performance.view': {'feature': ('performance_insights', STANDARD)},
        'rfq.recommendations': {'feature': ('matched_rfq_recommendations', STANDARD)},
        'rfq.match_scores': {'feature': ('supplier_matching', STANDARD)},
        'rfq.alert_preferences': {'feature': ('rfq_alerts', catalog.ADVANCED)},
        'contacts.details': {'feature': ('contacts', STANDARD)},
        'export': {'feature': ('export', STANDARD)},
        'invoice.list': {'feature': ('invoice_management', STANDARD)},
        'orders.filter': {'feature': ('order_management', STANDARD)},
        'audit.filter': {'feature': ('audit_logs', catalog.ADVANCED)},
    },
}

# Plan-only actions: the role check uses this action instead (they're
# about the plan, so any role that can see the thing may use it).
ROLE_FOR = {
    'team.invite': 'team.manage',
    'invoice.list': 'invoice.view',
    'orders.filter': 'order.manage',
    'directory.filter': 'directory.view',
    'suppliers.suggest': 'rfq.create',
    'quotes.compare': ('quotes.manage', 'company.view'),
    'audit.filter': ('audit.view', 'audit.view_own'),
    'quote.submit': 'quotes.manage',
    'quote.templates': 'quotes.manage',
    'quotes.filter': 'rfq.view',
    'quotes.bulk': 'quotes.manage',
    'quotes.history': 'rfq.view',
    'performance.view': 'analytics.operations',
    'rfq.recommendations': 'rfq.view',
    'rfq.match_scores': 'rfq.view',
    'rfq.alert_preferences': 'rfq.alerts',
    'contacts.details': 'buyer_info.view',
}


@dataclass
class Decision:
    allowed: bool
    reason: str = ''
    # Set when an upgrade would allow it: the cheapest plan that would.
    upgrade_to: str = ''

    def __bool__(self):
        return self.allowed

    @property
    def upgrade_label(self):
        return catalog.PLAN_LABELS.get(self.upgrade_to, '')


ALLOWED = Decision(True)


def side_of(profile):
    from accounts.models import ConsumerProfile
    return BUYER if isinstance(profile, ConsumerProfile) else SUPPLIER


def subscription_of(profile):
    from accounts.models import SubscriptionPlan
    return SubscriptionPlan.objects.filter(user_profile=profile.user).first()


def plan_of(profile):
    """The plan the company is entitled to right now (billing.services.
    entitled_plan): no subscription, an inactive one, or a paid one past its
    expiry and grace means Free. Cached on the profile object for the
    request."""
    if profile is None:
        return catalog.DEFAULT_PLAN
    cached = getattr(profile, '_plan_key', None)
    if cached is None:
        from billing.services import entitled_plan
        cached = entitled_plan(subscription_of(profile))
        if cached not in catalog.PLAN_LABELS:
            cached = catalog.DEFAULT_PLAN
        profile._plan_key = cached
    return cached


def forget_plan(profile):
    if hasattr(profile, '_plan_key'):
        del profile._plan_key


def feature_level(profile, feature):
    if profile is None:
        return catalog.ADVANCED
    return catalog.feature_level(side_of(profile), plan_of(profile), feature)


def has_feature(profile, feature, level=BASIC):
    return feature_level(profile, feature) >= level


def limit(profile, key):
    if profile is None:
        return None
    return catalog.limit(side_of(profile), plan_of(profile), key)


def month_start():
    return timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_window_start(profile, now=None):
    """Where the company's current monthly-limit window began: 30-day
    windows counted from the start of its current billing period (a Free
    company's period starts when it joined or last moved to Free). A
    renewal or a new paid period starts a new window; an upgrade keeps it,
    so usage carries over and only the limit rises."""
    from datetime import timedelta
    now = now or timezone.now()
    subscription = subscription_of(profile)
    anchor = subscription.current_period_start if subscription else None
    if anchor is None or anchor > now:
        return month_start()
    window = timedelta(days=catalog.USAGE_WINDOW_DAYS)
    return anchor + ((now - anchor) // window) * window


def usage(profile, key):
    """How much of `key` the company has used (this month, for monthly
    limits)."""
    from . import storage
    if key == catalog.USERS:
        return team.users_counted(profile)
    if key == catalog.STORAGE_BYTES:
        return storage.used_bytes(profile)
    if key == catalog.RFQS_PER_MONTH:
        from marketplace.models import Requirement
        member_ids = [profile.user_id, *profile.team_members.values_list('user_id', flat=True)]
        # Deleted RFQs still count, otherwise delete-and-repost would bypass the limit.
        return Requirement.objects.filter(user_id__in=member_ids, created_at__gte=usage_window_start(profile)).count()
    if key == catalog.QUOTES_PER_MONTH:
        from marketplace.models import Quote
        # Withdrawn quotes still count, for the same reason.
        return Quote.objects.filter(supplier=profile, submitted_at__gte=usage_window_start(profile)).count()
    if key == catalog.RFQS_RECEIVED_PER_MONTH:
        from .models import RFQReceipt
        return RFQReceipt.objects.filter(supplier=profile, received_at__gte=usage_window_start(profile)).count()
    raise ValueError(f"unknown limit {key!r}")


def remaining(profile, key):
    """How much is left under the limit, or None when it's unlimited."""
    cap = limit(profile, key)
    if cap is None:
        return None
    return max(cap - usage(profile, key), 0)


def has_room(profile, key, amount=1):
    left = remaining(profile, key)
    return left is None or left >= amount


_LIMIT_MESSAGES = {
    catalog.USERS: "Your plan includes {limit} users and they're all in use (open invitations count too).",
    catalog.RFQS_PER_MONTH: "You've posted all {limit} RFQs your plan includes this month.",
    catalog.QUOTES_PER_MONTH: "Your company has submitted all {limit} quotations its plan includes this month.",
    catalog.RFQS_RECEIVED_PER_MONTH: "Your company has received all {limit} RFQs its plan includes this month.",
    catalog.STORAGE_BYTES: "Your company has used all {limit} of file storage its plan includes.",
}


def _upgrade_for_limit(side, plan, key):
    current = catalog.limit(side, plan, key)
    for candidate in catalog.PLAN_ORDER[catalog.PLAN_ORDER.index(plan) + 1:]:
        cap = catalog.limit(side, candidate, key)
        if cap is None or (current is not None and cap > current):
            return candidate
    return ''


def check(user, action):
    """Role, then plan feature, then limit. Returns a Decision."""
    profile = team.company(user)
    role_actions = ROLE_FOR.get(action, action)
    if isinstance(role_actions, str):
        role_actions = (role_actions,)
    if not any(team.allows(user, role_action) for role_action in role_actions):
        return Decision(False, f"Your role ({team.role_label(user) or 'none'}) on this company account can't do that.")
    if profile is None:
        return ALLOWED
    side, plan = side_of(profile), plan_of(profile)
    rule = PLAN_RULES[side].get(action, {})
    if 'feature' in rule:
        feature, level = rule['feature']
        if catalog.feature_level(side, plan, feature) < level:
            upgrade = catalog.cheapest_plan_with(side, feature, level) or ''
            return Decision(False, f"This is part of the {catalog.PLAN_LABELS.get(upgrade, 'higher')} plan.", upgrade)
    if 'limit' in rule:
        key = rule['limit']
        if not has_room(profile, key):
            message = _LIMIT_MESSAGES[key].format(limit=catalog.describe_limit(key, limit(profile, key)))
            return Decision(False, message + " Upgrade the plan for more.", _upgrade_for_limit(side, plan, key))
    return ALLOWED


def can(user, action):
    return bool(check(user, action))


def with_plan_levels(suppliers, *features):
    """Annotates a ManufacturerProfile queryset with each supplier's plan
    (`plan_key`) and, per feature, its level (`<feature>_level`), so the
    directory can filter and rank by plan in the database."""
    from django.db.models import Case, IntegerField, OuterRef, Subquery, Value, When
    from django.db.models.functions import Coalesce

    from datetime import timedelta

    from django.conf import settings
    from django.db.models import Q

    from accounts.models import SubscriptionPlan
    # The same rule as billing.services.entitled_plan, in SQL.
    now = timezone.now()
    entitled = (
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        | Q(status=SubscriptionPlan.PAST_DUE, grace_until__gt=now)
        | Q(status=SubscriptionPlan.ACTIVE, auto_renew=True, expires_at__gt=now - timedelta(days=settings.BILLING_GRACE_DAYS))
    )
    active_plan = SubscriptionPlan.objects.filter(entitled, user_profile_id=OuterRef('user__email'), is_active=True).values('plan_type')[:1]
    suppliers = suppliers.annotate(plan_key=Coalesce(Subquery(active_plan), Value(catalog.DEFAULT_PLAN)))
    for feature in features:
        suppliers = suppliers.annotate(**{f'{feature}_level': Case(
            *[When(plan_key=plan, then=Value(catalog.feature_level(SUPPLIER, plan, feature))) for plan in catalog.PLAN_ORDER],
            default=Value(catalog.feature_level(SUPPLIER, catalog.DEFAULT_PLAN, feature)), output_field=IntegerField(),
        )})
    return suppliers


def usage_rows(profile):
    """This month's usage against the plan's limits, for the dashboard and
    the billing tab."""
    from . import storage
    labels = {
        catalog.USERS: 'Users', catalog.RFQS_PER_MONTH: 'RFQs posted this month',
        catalog.RFQS_RECEIVED_PER_MONTH: 'RFQs received this month', catalog.QUOTES_PER_MONTH: 'Quotes sent this month',
        catalog.STORAGE_BYTES: 'File storage',
    }
    if profile is None:
        return []
    included = catalog.plan_entry(side_of(profile), plan_of(profile))['limits']
    rows = []
    for key, label in labels.items():
        if key not in included:
            continue
        cap, used = limit(profile, key), usage(profile, key)
        rows.append({
            'label': label,
            'used': storage.human(used) if key == catalog.STORAGE_BYTES else used,
            'limit': catalog.describe_limit(key, cap),
            'percent': min(100, used * 100 / cap) if cap else 0,
            'full': cap is not None and used >= cap,
        })
    return rows
