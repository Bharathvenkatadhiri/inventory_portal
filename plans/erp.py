"""The MakeSetu ERP plans: price, users, warehouses, support and which
features each one includes. The pricing page, the Features page, sign-up,
the /api/v1/plans endpoint and organizations.access all read from here, so
this is the only place to change them.

The older marketplace plans for buyer and supplier accounts are in
plans/catalog.py; existing marketplace accounts are still billed on those.

Prices are per organization per month, excluding GST. Billing for ERP plans
isn't switched on yet: an organization records the plan it chose at sign-up
(accounts.Company.erp_plan).
"""
STARTER, GROWTH, PRO = 'starter', 'growth', 'pro'
PLAN_ORDER = [STARTER, GROWTH, PRO]
DEFAULT_PLAN = STARTER

# How a plan has a feature. A feature missing from a plan's map isn't on it.
INCLUDED, ADDON = 'included', 'addon'

# Feature keys. organizations.catalog maps every permission to one.
ADMIN = 'admin'                          # company settings, people, roles, partners: every plan
PURCHASE = 'purchase'                    # PO, GRN, purchase bills
SALES = 'sales'                          # quotation, sales order, dispatch
INVENTORY = 'inventory'                  # stock, warehouses
GST_INVOICING = 'gst_invoicing'          # GST invoices, payments, ledgers
CHALLANS_RETURNS = 'challans_returns'    # delivery challan, returns, debit/credit notes
GSTR_TALLY = 'gstr_tally'                # GSTR reports, Tally export
EWAY_BILL = 'eway_bill'
EINVOICE = 'einvoice'
BOM = 'bom'                              # single-level BOM, production entry
MULTI_LEVEL_BOM = 'multi_level_bom'      # multi-level BOM, work orders
QC = 'qc'
BATCH_SERIAL = 'batch_serial'            # batch/serial tracking, barcode
JOB_WORK = 'job_work'                    # job-work challan, ITC-04
PRODUCTION_COSTING = 'production_costing'  # production costing, scrap
APPROVALS = 'approvals'                  # basic (single-level) approvals
MULTI_LEVEL_APPROVALS = 'multi_level_approvals'
MRP = 'mrp'                              # MRP planning, scheduling
MAINTENANCE_AUDIT = 'maintenance_audit'  # maintenance, audit log

# The rows of the pricing table, in order: (label, feature keys). A row is
# ticked for a plan when the plan has every key in it.
FEATURE_ROWS = [
    ('Purchase, Sales, Inventory, GST invoicing', [PURCHASE, SALES, INVENTORY, GST_INVOICING]),
    ('Delivery challan, returns, debit/credit notes', [CHALLANS_RETURNS]),
    ('GSTR reports + Tally export', [GSTR_TALLY]),
    ('E-way bill', [EWAY_BILL]),
    ('E-invoice', [EINVOICE]),
    ('Single-level BOM + production entry', [BOM]),
    ('Multi-level BOM + work orders', [MULTI_LEVEL_BOM]),
    ('QC and inspection', [QC]),
    ('Batch/serial tracking, barcode', [BATCH_SERIAL]),
    ('Job work (challan + ITC-04)', [JOB_WORK]),
    ('Production costing, scrap', [PRODUCTION_COSTING]),
    ('Basic approvals', [APPROVALS]),
    ('Multi-level approvals', [MULTI_LEVEL_APPROVALS]),
    ('MRP planning, scheduling', [MRP]),
    ('Maintenance, audit log', [MAINTENANCE_AUDIT]),
]

_ON_EVERY_PLAN = [PURCHASE, SALES, INVENTORY, GST_INVOICING, CHALLANS_RETURNS, GSTR_TALLY, EWAY_BILL, BOM]
_GROWTH_ADDS = [EINVOICE, MULTI_LEVEL_BOM, QC, BATCH_SERIAL, JOB_WORK, PRODUCTION_COSTING, APPROVALS]
_PRO_ADDS = [MULTI_LEVEL_APPROVALS, MRP, MAINTENANCE_AUDIT]


def _features(included, addons=()):
    return {**{f: ADDON for f in addons}, **{f: INCLUDED for f in included}}


# users: full users (office staff), one shared pool for the whole company
# whatever team they're in. teams: teams / departments the company can
# create. light_users: light users included
# (shop-floor and support staff: organizations.catalog.LIGHT_PERMISSIONS
# only); each one beyond that costs EXTRA_LIGHT_USER_MONTHLY. None means no
# limit.
EXTRA_LIGHT_USER_MONTHLY = 150
PLANS = {
    STARTER: {
        'label': 'Starter', 'monthly': 1500, 'users': 3, 'light_users': 5, 'teams': 2, 'warehouses': 1, 'support': 'Email/chat',
        'summary': 'Buy, sell, keep stock, make simple products and file GST.',
        'features': _features(_ON_EVERY_PLAN, addons=[EINVOICE]),
    },
    GROWTH: {
        'label': 'Growth', 'monthly': 4000, 'users': 10, 'light_users': 20, 'teams': 5, 'warehouses': 3, 'support': 'Phone',
        'summary': 'Add work orders, QC, batch tracking, job work and approvals.',
        'features': _features(_ON_EVERY_PLAN + _GROWTH_ADDS),
    },
    PRO: {
        'label': 'Pro', 'monthly': 10000, 'users': 25, 'light_users': 50, 'teams': None, 'warehouses': 10, 'support': 'Priority',
        'summary': 'Plan with MRP, approve in stages, and run maintenance.',
        'features': _features(_ON_EVERY_PLAN + _GROWTH_ADDS + _PRO_ADDS),
    },
}

PLAN_CHOICES = [(key, PLANS[key]['label']) for key in PLAN_ORDER]


def plan(key):
    return PLANS[key]


def includes(plan_key, feature):
    """Whether `plan_key` includes `feature` (an add-on doesn't count until
    it's bought). ADMIN is on every plan; no plan includes nothing else."""
    if feature == ADMIN:
        return True
    entry = PLANS.get(plan_key)
    return entry is not None and entry['features'].get(feature) == INCLUDED


def first_plan_with(feature):
    """The smallest plan that includes `feature`, for "upgrade to ..." hints."""
    return next((key for key in PLAN_ORDER if includes(key, feature)), None)


def _row_level(plan_key, keys):
    levels = {PLANS[plan_key]['features'].get(k) for k in keys}
    if levels == {INCLUDED}:
        return INCLUDED
    if None not in levels:
        return ADDON
    return None


def _count(value, noun):
    return f"Unlimited {noun}s" if value is None else f"{value} {noun}{'' if value == 1 else 's'}"


def feature_labels(plan_key):
    """The labels of the pricing rows this plan includes."""
    return [label for label, keys in FEATURE_ROWS if _row_level(plan_key, keys) == INCLUDED]


def cards():
    """One entry per plan for the pricing page and sign-up: price, limits,
    what it adds over the plan before it, and its add-ons."""
    result, previous = [], None
    for key in PLAN_ORDER:
        entry = PLANS[key]
        added = [label for label, keys in FEATURE_ROWS
                 if _row_level(key, keys) == INCLUDED and (previous is None or _row_level(previous, keys) != INCLUDED)]
        result.append({
            'key': key,
            'label': entry['label'],
            'monthly': entry['monthly'],
            'summary': entry['summary'],
            'users': entry['users'],
            'light_users': entry['light_users'],
            'teams': entry['teams'],
            'extra_light_user_monthly': EXTRA_LIGHT_USER_MONTHLY,
            'warehouses': entry['warehouses'],
            'support': entry['support'],
            'users_label': _count(entry['users'], 'user'),
            'light_users_label': _count(entry['light_users'], 'light user'),
            'teams_label': _count(entry['teams'], 'team'),
            'warehouses_label': _count(entry['warehouses'], 'warehouse'),
            'includes_previous': PLANS[previous]['label'] if previous else None,
            'added_features': added,
            'addons': [label for label, keys in FEATURE_ROWS if _row_level(key, keys) == ADDON],
            'features': [f for f, level in entry['features'].items() if level == INCLUDED],
        })
        previous = key
    return result


def comparison_rows():
    """(label, [cell per plan]) rows for the pricing page's compare table.
    Cells are '✓', '—', 'Add-on', or a number or text."""
    marks = {INCLUDED: '✓', ADDON: 'Add-on', None: '—'}
    rows = [
        ('Users (shared pool)', ['Unlimited' if PLANS[k]['users'] is None else str(PLANS[k]['users']) for k in PLAN_ORDER]),
        ('Light users included', ['Unlimited' if PLANS[k]['light_users'] is None else str(PLANS[k]['light_users']) for k in PLAN_ORDER]),
        ('Extra light users', [f"₹{EXTRA_LIGHT_USER_MONTHLY} / month each" for k in PLAN_ORDER]),
        ('Teams / departments', ['Unlimited' if PLANS[k]['teams'] is None else str(PLANS[k]['teams']) for k in PLAN_ORDER]),
        ('Warehouses', ['Unlimited' if PLANS[k]['warehouses'] is None else str(PLANS[k]['warehouses']) for k in PLAN_ORDER]),
    ]
    rows += [(label, [marks[_row_level(k, keys)] for k in PLAN_ORDER]) for label, keys in FEATURE_ROWS]
    rows.append(('Support', [PLANS[k]['support'] for k in PLAN_ORDER]))
    return rows
