from django.apps import AppConfig


class ProductionConfig(AppConfig):
    """Making things: single-level bills of materials, and production
    entries that use components from stock and add the finished item."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "production"
