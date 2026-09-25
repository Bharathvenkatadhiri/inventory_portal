from django.db import migrations


def remove_staff_flag(apps, schema_editor):
    # Registration used to save the Supplier/Buyer radio into is_staff, so
    # every self-registered manufacturer became a Django staff user.
    User = apps.get_model('core', 'User')
    User.objects.filter(role='manufacturer', is_staff=True, is_superuser=False).update(is_staff=False)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(remove_staff_flag, migrations.RunPython.noop),
    ]
