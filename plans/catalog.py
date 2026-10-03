"""The MakeSetu plans: what each plan costs, how much of each limit it
includes, and which features it unlocks — for buyer and supplier companies.

This is the only place plans are described. Who on a team may do what is a
separate question (accounts.team, roles); plans.access combines the two.

Feature levels: NONE (not included), BASIC, STANDARD ("✓" / "Full" /
"Enhanced") and ADVANCED ("Advanced" / "Priority"). A feature missing from a
plan's map is NONE. Features every plan includes in full aren't listed.
"""
FREE, STARTER, BUSINESS = 'free', 'starter', 'business'
PLAN_ORDER = [FREE, STARTER, BUSINESS]
PLAN_LABELS = {FREE: 'Free', STARTER: 'Starter', BUSINESS: 'Business'}
DEFAULT_PLAN = FREE

MONTHLY, YEARLY = 'monthly', 'yearly'
BILLING_CYCLES = [(MONTHLY, 'Monthly'), (YEARLY, 'Yearly')]

BUYER, SUPPLIER = 'buyer', 'supplier'

NONE, BASIC, STANDARD, ADVANCED = 0, 1, 2, 3
LEVEL_LABELS = {NONE: '—', BASIC: 'Basic', STANDARD: '✓', ADVANCED: 'Advanced'}

GB = 1024 ** 3

# Limit keys. None means unlimited.
USERS = 'users'                          # active users incl. owner and viewers, plus open invitations
RFQS_PER_MONTH = 'rfqs_per_month'        # buyer: RFQs posted
RFQS_RECEIVED_PER_MONTH = 'rfqs_received_per_month'  # supplier: relevant RFQs delivered to the inbox
QUOTES_PER_MONTH = 'quotes_per_month'    # supplier: quotations submitted
STORAGE_BYTES = 'storage_bytes'          # files the company uploaded

_PRICES = {
    FREE: {MONTHLY: 0, YEARLY: 0},
    STARTER: {MONTHLY: 999, YEARLY: 9990},
    BUSINESS: {MONTHLY: 2999, YEARLY: 29990},
}

PLANS = {
    BUYER: {
        FREE: {
            'limits': {USERS: 2, RFQS_PER_MONTH: 5, STORAGE_BYTES: 1 * GB},
            'features': {
                'supplier_directory': BASIC, 'quote_comparison': BASIC, 'order_management': BASIC,
                'invoice_management': BASIC, 'audit_logs': BASIC,
            },
        },
        STARTER: {
            'limits': {USERS: 5, RFQS_PER_MONTH: 25, STORAGE_BYTES: 5 * GB},
            'features': {
                'supplier_directory': STANDARD, 'quote_comparison': BASIC, 'order_management': STANDARD,
                'invoice_management': STANDARD, 'audit_logs': STANDARD, 'export': STANDARD,
            },
        },
        BUSINESS: {
            'limits': {USERS: 15, RFQS_PER_MONTH: 100, STORAGE_BYTES: 25 * GB},
            'features': {
                'supplier_directory': STANDARD, 'quote_comparison': ADVANCED, 'supplier_matching': STANDARD,
                'advanced_analytics': STANDARD, 'advanced_reports': STANDARD, 'approval_workflows': STANDARD,
                'order_management': STANDARD, 'invoice_management': STANDARD, 'audit_logs': ADVANCED,
                'export': STANDARD,
            },
        },
    },
    SUPPLIER: {
        FREE: {
            'limits': {USERS: 2, RFQS_RECEIVED_PER_MONTH: 10, QUOTES_PER_MONTH: 10, STORAGE_BYTES: 1 * GB},
            'features': {
                'supplier_profile': BASIC, 'quote_management': BASIC, 'directory_listing': BASIC,
                'search_visibility': BASIC, 'rfq_alerts': BASIC, 'analytics': BASIC, 'order_management': BASIC,
                'invoice_management': BASIC, 'audit_logs': BASIC, 'contacts': BASIC,
            },
        },
        STARTER: {
            'limits': {USERS: 5, RFQS_RECEIVED_PER_MONTH: 50, QUOTES_PER_MONTH: 50, STORAGE_BYTES: 5 * GB},
            'features': {
                'supplier_profile': STANDARD, 'quote_management': STANDARD, 'quote_templates': STANDARD,
                'directory_listing': STANDARD, 'search_visibility': STANDARD, 'supplier_matching': STANDARD,
                'matched_rfq_recommendations': STANDARD, 'rfq_alerts': STANDARD, 'analytics': STANDARD,
                'quote_analytics': STANDARD, 'order_management': STANDARD, 'invoice_management': STANDARD,
                'audit_logs': STANDARD, 'export': STANDARD, 'contacts': STANDARD,
            },
        },
        BUSINESS: {
            'limits': {USERS: 15, RFQS_RECEIVED_PER_MONTH: 200, QUOTES_PER_MONTH: 200, STORAGE_BYTES: 25 * GB},
            'features': {
                'supplier_profile': ADVANCED, 'quote_management': ADVANCED, 'quote_templates': STANDARD,
                'quote_comparison': STANDARD, 'directory_listing': ADVANCED, 'search_visibility': ADVANCED,
                'supplier_matching': ADVANCED, 'matched_rfq_recommendations': STANDARD, 'featured_profile': STANDARD,
                'rfq_alerts': ADVANCED, 'analytics': ADVANCED, 'quote_analytics': STANDARD,
                'order_management': STANDARD, 'invoice_management': STANDARD, 'audit_logs': ADVANCED,
                'export': STANDARD, 'contacts': STANDARD, 'performance_insights': STANDARD,
            },
        },
    },
}

# How long the activity log reaches back, by audit_logs level (None = all).
AUDIT_LOG_DAYS = {BASIC: 30, STANDARD: 365, ADVANCED: None}

# Supplier directory on BASIC: browse and keyword search, this many pages.
BASIC_DIRECTORY_PAGES = 3

# The rows of the public pricing tables, in order: (label, feature or limit
# key or None for an "every plan" row). Rendered by homepage's pricing page.
PRICING_ROWS = {
    BUYER: [
        ('Company registration', None), ('GSTIN verification', None),
        ('Users', USERS), ('Owner / Admin / Procurement / Viewer roles', None),
        ('RFQs / month', RFQS_PER_MONTH), ('Quote management', None),
        ('Basic quote comparison', None), ('Advanced quote comparison', ('quote_comparison', ADVANCED)),
        ('Supplier directory', 'supplier_directory'), ('Supplier matching', 'supplier_matching'),
        ('Basic dashboard', None), ('Basic analytics', None), ('Advanced analytics', 'advanced_analytics'),
        ('Approval workflows', 'approval_workflows'), ('Advanced reports', 'advanced_reports'),
        ('Order management', 'order_management'), ('Invoice management', 'invoice_management'),
        ('Audit logs', 'audit_logs'), ('File storage', STORAGE_BYTES), ('Excel / CSV export', 'export'),
        ('Email notifications', None),
    ],
    SUPPLIER: [
        ('Company registration', None), ('GSTIN verification', None),
        ('Users', USERS), ('Owner / Admin / Sales / Operations / Viewer roles', None),
        ('Supplier profile', 'supplier_profile'), ('Manufacturing capabilities, materials, machines', None),
        ('Certifications and company documents', None),
        ('Relevant RFQs received / month', RFQS_RECEIVED_PER_MONTH), ('Quote submissions / month', QUOTES_PER_MONTH),
        ('View RFQ details and submit quotations', None), ('Quote management', 'quote_management'),
        ('Quote templates', 'quote_templates'), ('Quote comparison', 'quote_comparison'),
        ('Supplier directory listing', 'directory_listing'), ('Supplier search visibility', 'search_visibility'),
        ('Supplier matching', 'supplier_matching'), ('Matched RFQ recommendations', 'matched_rfq_recommendations'),
        ('Featured supplier profile', 'featured_profile'), ('RFQ opportunity alerts', 'rfq_alerts'),
        ('Basic dashboard', None), ('Analytics', 'analytics'), ('Quote / response analytics', 'quote_analytics'),
        ('Order management', 'order_management'), ('Invoice management', 'invoice_management'),
        ('Audit logs', 'audit_logs'), ('File storage', STORAGE_BYTES), ('Excel / CSV export', 'export'),
        ('Email notifications', None), ('Customer / supplier contacts', 'contacts'),
        ('Performance insights', 'performance_insights'),
    ],
}

# Wording for levels where the plain Basic/✓/Advanced label reads oddly.
LEVEL_WORDING = {
    'supplier_directory': {BASIC: 'Basic', STANDARD: 'Full'},
    'supplier_profile': {BASIC: 'Basic', STANDARD: 'Full', ADVANCED: 'Advanced'},
    'directory_listing': {BASIC: 'Basic', STANDARD: 'Full', ADVANCED: 'Priority'},
    'search_visibility': {BASIC: 'Basic', STANDARD: 'Enhanced', ADVANCED: 'Priority'},
}


def price(plan, cycle=MONTHLY):
    return _PRICES[plan][cycle]


def plan_entry(side, plan):
    return PLANS[side].get(plan) or PLANS[side][DEFAULT_PLAN]


def feature_level(side, plan, feature):
    return plan_entry(side, plan)['features'].get(feature, NONE)


def limit(side, plan, key):
    return plan_entry(side, plan)['limits'].get(key)


def cheapest_plan_with(side, feature, level=BASIC):
    """The first plan that has `feature` at `level` or better — what an
    upgrade prompt points at."""
    for plan in PLAN_ORDER:
        if feature_level(side, plan, feature) >= level:
            return plan
    return None


def describe_limit(key, value):
    if value is None:
        return 'Unlimited'
    if key == STORAGE_BYTES:
        return f"{value // GB} GB"
    return str(value)


def pricing_table(side):
    """Rows for the public pricing table: [(label, [cell per plan])]."""
    rows = []
    for label, spec in PRICING_ROWS[side]:
        cells = []
        for plan in PLAN_ORDER:
            if spec is None:
                cells.append('✓')
            elif isinstance(spec, tuple):
                feature, level = spec
                cells.append('✓' if feature_level(side, plan, feature) >= level else '—')
            elif spec in (USERS, RFQS_PER_MONTH, RFQS_RECEIVED_PER_MONTH, QUOTES_PER_MONTH, STORAGE_BYTES):
                cells.append(describe_limit(spec, limit(side, plan, spec)))
            else:
                level = feature_level(side, plan, spec)
                cells.append(LEVEL_WORDING.get(spec, {}).get(level) or LEVEL_LABELS[level])
        rows.append((label, cells))
    return rows


def plan_highlights(side, plan):
    """A pricing card's bullet points: the plan's limits, then what it adds
    over the plan below (or the basics, for the first plan)."""
    limits = plan_entry(side, plan)['limits']
    lines = [f"{describe_limit(USERS, limits.get(USERS))} users (all roles)"]
    for key, label in ((RFQS_PER_MONTH, 'RFQs / month'), (RFQS_RECEIVED_PER_MONTH, 'relevant RFQs received / month'),
                       (QUOTES_PER_MONTH, 'quote submissions / month')):
        if key in limits:
            lines.append(f"{describe_limit(key, limits[key])} {label}")
    lines.append(f"{describe_limit(STORAGE_BYTES, limits.get(STORAGE_BYTES))} file storage")
    index = PLAN_ORDER.index(plan)
    previous = PLAN_ORDER[index - 1] if index else None
    for label, spec in PRICING_ROWS[side]:
        if spec is None or spec in (USERS, RFQS_PER_MONTH, RFQS_RECEIVED_PER_MONTH, QUOTES_PER_MONTH, STORAGE_BYTES):
            continue
        feature, needed = spec if isinstance(spec, tuple) else (spec, BASIC)
        level = feature_level(side, plan, feature)
        if level < needed or (previous and feature_level(side, previous, feature) >= level):
            continue
        if previous is None and level == BASIC:
            continue
        wording = LEVEL_WORDING.get(feature, {}).get(level)
        if isinstance(spec, tuple) or (level == STANDARD and not wording and feature_level(side, PLAN_ORDER[0], feature) == NONE):
            lines.append(label)
        else:
            lower = label if label[1:2].isupper() else label[0].lower() + label[1:]
            lines.append(f"{wording or ('Full' if level == STANDARD else LEVEL_LABELS[level])} {lower}")
    return lines
