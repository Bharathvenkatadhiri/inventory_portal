from django.apps import AppConfig


class PurchaseConfig(AppConfig):
    """Buying: purchase orders to suppliers the organization has an active
    relationship with, goods receipts (GRN) into a warehouse, and the
    supplier's bills against what was received. The supplier sees the same
    purchase order, read-only, as an order received."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "purchase"
