from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = [
        ("consumer", "Consumer"),
        ("manufacturer", "Manufacturer"),
        ("admin", "Admin"),
    ]

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="consumer")


class AuditLogEntry(models.Model):
    """One staff action that changed another account's state: deactivating
    or reactivating a buyer or supplier, editing their company details or
    subscription, moderating feedback. Order and RFQ changes already have
    their own history (OrderEvent etc.); these didn't, so a compromised or
    careless staff account left no trace. Written by core.audit.record;
    read-only in the Django admin."""
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    actor_email = models.EmailField(blank=True)  # kept if the actor's account is later deleted
    action = models.CharField(max_length=40)
    target_type = models.CharField(max_length=40)
    target_id = models.CharField(max_length=40)
    target_repr = models.CharField(max_length=200, blank=True)
    detail = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['target_type', 'target_id'])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_email or 'unknown'} {self.action} {self.target_repr}"
