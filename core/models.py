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
    # Set once the OTP sent at registration (accounts.otp) is confirmed.
    # Defaults False for every existing row too, but that's harmless: it's
    # only checked on the registration flow itself (CreateSupplier /
    # CreateCustomer), which a fully-registered user never revisits — see
    # accounts.views.register. db_default (not just default) so the column
    # itself defaults to false in Postgres — an INSERT that doesn't name
    # this column (an older frozen model state in a migration test, e.g.)
    # gets false instead of hitting the NOT NULL constraint.
    email_verified = models.BooleanField(default=False, db_default=False)


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


class PasswordHistory(models.Model):
    """One previously-set password hash for a user, kept only so
    core.validators.PasswordHistoryValidator can reject reusing any of the
    last few — never read for anything else. Written by
    core.password_history.record at every point a password is actually
    saved (registration, change-password, team-join, reset); that module
    also trims each user down to the most recent few rows, so this table
    never grows without bound."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='password_history')
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return f"password set for {self.user_id} at {self.created_at:%Y-%m-%d %H:%M}"
