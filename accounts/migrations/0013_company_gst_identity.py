"""Company becomes the company's current GST identity in GSTN's terms
(gst app): the review-workflow columns go (verification_status, entity_type,
cin, mca_status), registered_address becomes principal_address, and the
GST registration details a provider returns get their own columns. Existing
rows are carried over, not dropped."""
from django.db import migrations, models

CONSTITUTIONS = {
    'private_limited': 'Private Limited Company',
    'public_limited': 'Public Limited Company',
    'llp': 'Limited Liability Partnership',
    'partnership': 'Partnership',
    'proprietorship': 'Proprietorship',
    'other': 'Others',
}


def forwards(apps, schema_editor):
    Company = apps.get_model('accounts', 'Company')
    for company in Company.objects.all():
        company.name = company.trade_name or company.legal_name
        company.business_constitution = CONSTITUTIONS.get(company.entity_type, '')
        company.gst_status = (company.gst_status or '').title()
        company.state_code = (company.gstin or '')[:2]
        company.save(update_fields=['name', 'business_constitution', 'gst_status', 'state_code'])


def backwards(apps, schema_editor):
    Company = apps.get_model('accounts', 'Company')
    entity_types = {label: key for key, label in CONSTITUTIONS.items()}
    for company in Company.objects.all():
        company.entity_type = entity_types.get(company.business_constitution, '')
        company.gst_status = (company.gst_status or '').upper()[:20]
        company.verification_status = 'verified' if company.gst_verified else 'pending'
        company.save(update_fields=['entity_type', 'gst_status', 'verification_status'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_teams_and_approvals'),
    ]

    operations = [
        migrations.AlterModelOptions(name='company', options={'verbose_name_plural': 'companies'}),
        migrations.RemoveIndex(model_name='company', name='accounts_co_gstin_235327_idx'),
        migrations.RemoveIndex(model_name='company', name='accounts_co_verific_ee63a0_idx'),
        migrations.RenameField(model_name='company', old_name='registered_address', new_name='principal_address'),
        migrations.AddField(model_name='company', name='name', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='company', name='gst_registration_date', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='company', name='gst_cancellation_date', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='company', name='taxpayer_type', field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name='company', name='business_constitution', field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name='company', name='state_code', field=models.CharField(blank=True, max_length=10)),
        migrations.AlterField(model_name='company', name='gst_status', field=models.CharField(blank=True, max_length=50)),
        migrations.AlterField(model_name='company', name='state', field=models.CharField(blank=True, max_length=100)),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name='company', name='cin'),
        migrations.RemoveField(model_name='company', name='mca_status'),
        migrations.RemoveField(model_name='company', name='entity_type'),
        migrations.RemoveField(model_name='company', name='verification_status'),
    ]
