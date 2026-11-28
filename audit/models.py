"""The audit log: who changed what, when, from where. Written by
audit.recorder from model signals; never edited or deleted through the
app."""
from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATE = 'create', 'Created'
        UPDATE = 'update', 'Changed'
        DELETE = 'delete', 'Deleted'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='audit_events')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    user_name = models.CharField(max_length=150, blank=True)  # kept if the user is later removed
    action = models.CharField(max_length=6, choices=Action.choices)
    object_type = models.CharField(max_length=60)  # app_label.model, e.g. purchase.purchaseorder
    object_id = models.CharField(max_length=40)
    object_label = models.CharField(max_length=120, blank=True)  # e.g. PO-2627-0001
    # {field: [before, after]} for changes; {field: value} for creates and deletes.
    changes = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    path = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['company', 'object_type', 'object_id']), models.Index(fields=['company', '-created_at'])]

    def __str__(self):
        return f"{self.get_action_display()} {self.object_type} {self.object_label or self.object_id}"
