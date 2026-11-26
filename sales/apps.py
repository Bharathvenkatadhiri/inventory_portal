from django.apps import AppConfig


class SalesConfig(AppConfig):
    """Selling: replying to purchase orders customers issue on MakeSetu
    (accept turns one into a sales order), sales orders, dispatches
    (delivery challans) out of stock, and GST tax invoices."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "sales"
