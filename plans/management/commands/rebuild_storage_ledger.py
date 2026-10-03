"""Rebuilds plans.StoredFile from every tracked upload. New uploads keep it
up to date on their own (plans.signals); run this once after deploying
plan limits, so files uploaded before then count toward storage too:

    python manage.py rebuild_storage_ledger
"""
from django.apps import apps
from django.core.management.base import BaseCommand

from plans import storage


class Command(BaseCommand):
    help = "Record the size of every uploaded file against its company's storage."

    def handle(self, *args, **options):
        recorded = 0
        for label, (fields, _owner) in storage.TRACKED.items():
            model = apps.get_model(label)
            query = model.objects.none()
            for field in fields:
                query = query | model.objects.exclude(**{field: ''}).exclude(**{f'{field}__isnull': True})
            for instance in query.distinct().iterator():
                storage.record(instance)
                recorded += 1
        self.stdout.write(f"Checked {recorded} record(s) with files.")
