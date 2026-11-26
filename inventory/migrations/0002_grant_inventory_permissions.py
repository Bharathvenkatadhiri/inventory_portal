"""Gives organizations that already exist the new inventory permissions on
their built-in roles, the same ones a new organization's roles start with
(organizations.catalog.DEFAULT_ROLES). Only roles still marked is_system
are touched, and permissions are only added, never removed, so an Org
Admin's own changes stay. The Org Admin gets every permission from
catalog.sync_permissions after migrate."""
from django.db import migrations

NEW = {
    'items.manage': ("Create and edit items", 'Store'),
    'warehouses.manage': ("Manage warehouses", 'Store'),
    'stock.adjust': ("Enter opening stock and stock adjustments", 'Store'),
}
GRANTS = {
    'purchase_manager': ['items.manage'],
    'store': ['items.manage', 'warehouses.manage', 'stock.adjust'],
    'production_planner': ['items.manage'],
}


def grant(apps, schema_editor):
    Permission = apps.get_model('organizations', 'Permission')
    Role = apps.get_model('organizations', 'Role')
    # sync_permissions creates these after migrate, too late for this step.
    permissions = {
        code: Permission.objects.get_or_create(code=code, defaults={'label': label, 'module': module})[0]
        for code, (label, module) in NEW.items()
    }
    for key, codes in GRANTS.items():
        for role in Role.objects.filter(key=key, is_system=True):
            role.permissions.add(*[permissions[c] for c in codes])


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0001_initial'),
        ('organizations', '0004_invitations_and_blocking'),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
