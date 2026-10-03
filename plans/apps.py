from django.apps import AppConfig


class PlansConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "plans"
    verbose_name = "Plans and usage"

    def ready(self):
        from . import signals  # noqa: F401  (keeps the storage ledger in step with uploads)
