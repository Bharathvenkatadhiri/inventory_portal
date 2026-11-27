from django.apps import AppConfig


class EinvoiceConfig(AppConfig):
    """E-invoices (IRN) and e-way bills through a GST Suvidha Provider."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'einvoice'
    verbose_name = 'E-invoices and e-way bills'
