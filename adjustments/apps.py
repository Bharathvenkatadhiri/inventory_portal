from django.apps import AppConfig


class AdjustmentsConfig(AppConfig):
    """Credit and debit notes (CGST Act s.34): ones the organization issues
    to customers against its tax invoices, and ones suppliers issue to it,
    recorded against their bills. They change what's owed, and returns move
    stock."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "adjustments"
