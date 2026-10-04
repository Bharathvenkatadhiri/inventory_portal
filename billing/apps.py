from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"
    verbose_name = "Billing"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in
        from .views import forget_dismissed_alerts
        user_logged_in.connect(forget_dismissed_alerts, dispatch_uid='billing.forget_dismissed_alerts')
