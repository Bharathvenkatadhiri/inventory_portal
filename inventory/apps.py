from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """Masters every ERP module builds on: items (with their GST unit, HSN
    code and rate), warehouses, and the stock ledger, which so far holds
    opening stock. Goods receipts, issues and dispatches will post to the
    same ledger."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"
