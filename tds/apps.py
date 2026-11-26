from django.apps import AppConfig


class TdsConfig(AppConfig):
    """TDS the organization deducts when it pays suppliers: worked out on
    each posted bill (tds.services), deposited with the government by
    challan (TdsDeposit), and reported each quarter in Form 26Q."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "tds"
    verbose_name = "TDS"
