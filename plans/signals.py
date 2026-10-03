"""Keeps plans.StoredFile in step with every upload plans.storage tracks."""
from django.apps import apps
from django.db.models.signals import post_delete, post_save

from . import storage


def _saved(sender, instance, raw=False, **kwargs):
    if not raw:
        storage.record(instance)


def _deleted(sender, instance, **kwargs):
    storage.forget(instance)


for label in storage.TRACKED:
    model = apps.get_model(label)
    post_save.connect(_saved, sender=model, dispatch_uid=f'plans-storage-save-{label}')
    post_delete.connect(_deleted, sender=model, dispatch_uid=f'plans-storage-delete-{label}')
