"""Gives existing organizations' built-in Production Planner and Accounts
roles the new production costing permission, as a new organization's roles
get it (organizations.catalog.DEFAULT_ROLES). Only adds; changed roles keep
their changes. The Org Admin gets it from catalog.sync_permissions after migrate."""
from django.db import migrations


def grant(apps, schema_editor):
    Permission = apps.get_model('organizations', 'Permission')
    Role = apps.get_model('organizations', 'Role')
    permission, _ = Permission.objects.get_or_create(
        code='costing.manage', defaults={'label': "Set cost rates and see production costs", 'module': 'Production'})
    for role in Role.objects.filter(key__in=['production_planner', 'accounts'], is_system=True):
        role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [
        ('production', '0003_billofmaterials_labour_hours_and_more'),
        ('organizations', '0005_businessrelationship_tds_section'),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
