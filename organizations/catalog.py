"""Every permission in the ERP and the default roles a new organization
starts with. A role is a set of these permission codes; organizations can
change their roles, or add their own, later.

Each permission belongs to a plan feature (plans.erp), and to a side when
it only makes sense for a company that buys (purchase) or sells (sales).
organizations.access checks all three: the role, the side being switched
on, and the plan.
"""
from plans import erp

BUYING, SELLING = 'buying', 'selling'

# code: (label, module shown in the roles screen, plan feature, side or None)
PERMISSIONS = {
    'org.manage': ("Manage company settings", 'Admin', erp.ADMIN, None),
    'users.manage': ("Invite people and assign roles", 'Admin', erp.ADMIN, None),
    'roles.manage': ("Create and edit roles", 'Admin', erp.ADMIN, None),
    'partners.manage': ("Invite and approve customers and suppliers", 'Admin', erp.ADMIN, None),
    'billing.manage': ("Manage the plan and payments", 'Admin', erp.ADMIN, None),

    'po.create': ("Create purchase orders", 'Purchase', erp.PURCHASE, BUYING),
    'po.approve': ("Approve purchase orders", 'Purchase', erp.APPROVALS, BUYING),
    'purchase_bill.create': ("Enter purchase bills", 'Purchase', erp.PURCHASE, BUYING),
    'purchase_bill.approve': ("Approve purchase bills", 'Purchase', erp.APPROVALS, BUYING),

    'quotation.create': ("Create quotations", 'Sales', erp.SALES, SELLING),
    'so.create': ("Create sales orders", 'Sales', erp.SALES, SELLING),
    'so.approve': ("Approve sales orders", 'Sales', erp.APPROVALS, SELLING),

    'grn.post': ("Post goods receipts (GRN)", 'Store', erp.INVENTORY, BUYING),
    'stock.issue': ("Issue stock", 'Store', erp.INVENTORY, None),
    'dispatch.post': ("Post dispatches", 'Store', erp.INVENTORY, SELLING),
    'stock.view': ("View stock", 'Store', erp.INVENTORY, None),
    'items.manage': ("Create and edit items", 'Store', erp.INVENTORY, None),
    'warehouses.manage': ("Manage warehouses", 'Store', erp.INVENTORY, None),
    'stock.adjust': ("Enter opening stock and stock adjustments", 'Store', erp.INVENTORY, None),
    'gate_entry.record': ("Record gate entries (vehicles in and out)", 'Store', erp.INVENTORY, None),

    'bom.manage': ("Manage bills of materials", 'Production', erp.BOM, None),
    'production.record': ("Record production", 'Production', erp.BOM, None),
    'work_order.manage': ("Create and update work orders", 'Production', erp.MULTI_LEVEL_BOM, None),

    'inspection.record': ("Record QC inspections", 'QC', erp.QC, None),

    'invoice.create': ("Raise GST invoices", 'Accounts', erp.GST_INVOICING, SELLING),
    'payment.record': ("Record payments", 'Accounts', erp.GST_INVOICING, None),
    'payment.approve': ("Approve payments", 'Accounts', erp.APPROVALS, None),
    'ledger.view': ("View ledgers", 'Accounts', erp.GST_INVOICING, None),
}

ORG_ADMIN = 'org_admin'

# Full users are office staff. Light users are shop-floor and support staff
# who do one or two simple tasks; they cost much less, so a factory gives
# everyone their own login instead of sharing one. A light role can only
# ever hold these permissions (enforced on Role, and again by access.check).
FULL, LIGHT = 'full', 'light'
USER_TYPE_CHOICES = [(FULL, 'Full user'), (LIGHT, 'Light user')]
LIGHT_PERMISSIONS = {
    'grn.post', 'stock.issue', 'stock.view', 'production.record', 'inspection.record', 'gate_entry.record',
    'so.create',
}

# key: (name, description, permission codes, user type). The Org Admin has everything.
DEFAULT_ROLES = {
    ORG_ADMIN: ("Org Admin", "Manages company settings, people and roles", list(PERMISSIONS), FULL),
    'purchase_manager': ("Purchase Manager", "Creates and approves purchase orders and bills", [
        'po.create', 'po.approve', 'purchase_bill.create', 'purchase_bill.approve', 'stock.view', 'items.manage',
    ], FULL),
    'purchase_executive': ("Purchase Executive", "Creates purchase orders and enters bills", [
        'po.create', 'purchase_bill.create', 'stock.view',
    ], FULL),
    'sales_manager': ("Sales Manager", "Handles and approves quotations and sales orders", [
        'quotation.create', 'so.create', 'so.approve', 'invoice.create', 'stock.view',
    ], FULL),
    'sales_executive': ("Sales Executive", "Prepares quotations and sales orders", [
        'quotation.create', 'so.create', 'stock.view',
    ], FULL),
    'store': ("Store / Warehouse", "Receives goods, issues stock and dispatches", [
        'grn.post', 'stock.issue', 'dispatch.post', 'stock.view', 'items.manage', 'warehouses.manage', 'stock.adjust',
    ], FULL),
    'production_planner': ("Production Planner", "Maintains BOMs and work orders", [
        'bom.manage', 'production.record', 'work_order.manage', 'stock.view', 'items.manage',
    ], FULL),
    'accounts': ("Accounts", "Invoices, payments and ledgers", [
        'invoice.create', 'purchase_bill.create', 'purchase_bill.approve', 'payment.record', 'payment.approve',
        'ledger.view',
    ], FULL),
    'qc': ("QC", "Records inspections", ['inspection.record', 'stock.view'], FULL),

    # Light users.
    'store_keeper': ("Store Keeper", "Records material received (GRN) and stock issued", [
        'grn.post', 'stock.issue', 'stock.view',
    ], LIGHT),
    'machine_operator': ("Machine Operator", "Enters quantity produced and rejected", ['production.record'], LIGHT),
    'qc_inspector': ("QC Inspector", "Marks inspections pass or fail", ['inspection.record'], LIGHT),
    'security': ("Security / Gate", "Records vehicles in and out at the gate", ['gate_entry.record'], LIGHT),
    'field_sales': ("Field Sales", "Checks stock and books orders on the phone", ['stock.view', 'so.create'], LIGHT),
}


def feature_of(code):
    return PERMISSIONS[code][2]


def side_of(code):
    return PERMISSIONS[code][3]


def sync_permissions(**kwargs):
    """Makes the Permission table match PERMISSIONS after every migrate, so
    adding a permission here needs no data migration. Codes no longer listed
    are left alone (roles may still hold them); they just never pass a check."""
    from django.db import connections

    from .models import Permission

    using = kwargs.get('using', 'default')
    # post_migrate also fires after migrating backwards (migration tests),
    # when this table may not exist yet.
    if Permission._meta.db_table not in connections[using].introspection.table_names():
        return
    for code, (label, module, _feature, _side) in PERMISSIONS.items():
        Permission.objects.using(using).update_or_create(code=code, defaults={'label': label, 'module': module})
    # The Org Admin has everything, including permissions added later.
    from .models import Role
    every = list(Permission.objects.using(using).filter(code__in=PERMISSIONS))
    for role in Role.objects.using(using).filter(key=ORG_ADMIN):
        role.permissions.add(*every)
