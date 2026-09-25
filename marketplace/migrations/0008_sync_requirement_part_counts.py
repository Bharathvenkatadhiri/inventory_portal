from django.db import migrations
from django.db.models import Count


def sync_part_counts(apps, schema_editor):
    # `parts` used to be typed by the buyer and could disagree with the part
    # rows actually attached; it's now always the number of part rows.
    Requirement = apps.get_model('marketplace', 'Requirement')
    for requirement in Requirement.objects.annotate(row_count=Count('requirement_parts')):
        if requirement.parts != requirement.row_count:
            Requirement.objects.filter(pk=requirement.pk).update(parts=requirement.row_count)


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0007_requirement_currency_inr'),
    ]

    operations = [
        migrations.RunPython(sync_part_counts, migrations.RunPython.noop),
    ]
