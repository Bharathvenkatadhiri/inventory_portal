from django.apps import AppConfig


class QualityConfig(AppConfig):
    """QC: goods received or produced that need inspection wait in an
    'Under inspection' location; an inspection passes them to their store
    or moves them to 'Rejected goods'."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "quality"
    verbose_name = "QC"
