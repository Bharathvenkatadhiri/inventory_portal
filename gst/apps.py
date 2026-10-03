from django.apps import AppConfig


class GstConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gst"
    verbose_name = "GST verification"

    def ready(self):
        from . import checks  # noqa: F401  (registers the provider check)
