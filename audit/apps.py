from django.apps import AppConfig


class AuditConfig(AppConfig):
    """Who changed what: an audit log of the ERP's documents and masters."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'audit'
    verbose_name = 'Audit log'

    def ready(self):
        from .recorder import connect
        connect()
