"""Gives existing organizations' built-in Store and Production Planner
roles the new job work permission, as a new organization's roles get it
(organizations.catalog.DEFAULT_ROLES). Only adds; changed roles keep their
changes. The Org Admin gets it from catalog.sync_permissions after migrate."""
from django.db import migrations


def grant(apps, schema_editor):
    Permission = apps.get_model('organizations', 'Permission')
    Role = apps.get_model('organizations', 'Role')
    permission, _ = Permission.objects.get_or_create(
        code='job_work.manage', defaults={'label': "Send goods for job work and receive them back", 'module': 'Production'})
    for role in Role.objects.filter(key__in=['store', 'production_planner'], is_system=True):
        role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [
        ('jobwork', '0001_initial'),
        ('organizations', '0005_businessrelationship_tds_section'),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
