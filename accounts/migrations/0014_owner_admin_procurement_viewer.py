"""Team roles become Owner / Admin / Procurement / Viewer: existing
supervisors become admins and users become procurement (accounts.team)."""
from django.db import migrations, models

FORWARDS = {'supervisor': 'admin', 'member': 'procurement'}
BACKWARDS = {'admin': 'supervisor', 'procurement': 'member', 'viewer': 'member'}
ROLE_CHOICES = [('admin', 'Admin'), ('procurement', 'Procurement'), ('viewer', 'Viewer')]


def _remap(mapping):
    def run(apps, schema_editor):
        for model_name in ('TeamMember', 'TeamInvitation'):
            model = apps.get_model('accounts', model_name)
            for old, new in mapping.items():
                model.objects.filter(role=old).update(role=new)
    return run


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_company_gst_identity'),
    ]

    operations = [
        migrations.AlterField(
            model_name='teammember', name='role',
            field=models.CharField(choices=ROLE_CHOICES, default='procurement', max_length=20),
        ),
        migrations.AlterField(
            model_name='teaminvitation', name='role',
            field=models.CharField(choices=ROLE_CHOICES, max_length=20),
        ),
        migrations.RunPython(_remap(FORWARDS), _remap(BACKWARDS)),
    ]
