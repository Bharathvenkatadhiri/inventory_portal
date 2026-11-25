from django.apps import AppConfig
from django.db.models.signals import post_migrate


class OrganizationsConfig(AppConfig):
    """The ERP platform layer: organizations' members and roles
    (role-based access), addresses and bank accounts, and the buyer/supplier
    relationships between organizations. The organization itself is
    accounts.Company."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "organizations"

    def ready(self):
        from .catalog import sync_permissions
        post_migrate.connect(sync_permissions, sender=self)
