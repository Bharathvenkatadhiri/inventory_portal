import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_emailverification'),
    ]

    operations = [
        # The old EmailVerification rows are short-lived OTP codes (10
        # minute TTL) tied to a User; dropping them is harmless — any still
        # "pending" are already expired by the time this runs.
        migrations.RemoveIndex(
            model_name='emailverification',
            name='accounts_em_user_id_8b7b60_idx',
        ),
        migrations.DeleteModel(
            name='EmailVerification',
        ),
        migrations.CreateModel(
            name='PendingRegistration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('username', models.CharField(max_length=150)),
                ('first_name', models.CharField(max_length=150)),
                ('last_name', models.CharField(max_length=150)),
                ('password', models.CharField(max_length=128)),
                ('role', models.CharField(max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='EmailVerification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code_hash', models.CharField(max_length=128)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('pending_registration', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='codes', to='accounts.pendingregistration')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='emailverification',
            index=models.Index(fields=['pending_registration', 'consumed_at'], name='accounts_em_pending_539f2f_idx'),
        ),
    ]
