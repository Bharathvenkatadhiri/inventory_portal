from django.apps import AppConfig


class FinanceConfig(AppConfig):
    """Money in and out: receipts from customers against sales invoices,
    payments to suppliers against purchase bills, what's outstanding and
    overdue. Kept separate from billing, which is MakeSetu charging the
    organization for its plan."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "finance"
