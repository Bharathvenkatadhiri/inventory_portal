from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        from . import session_security  # noqa: F401 — connects the user_logged_in receiver
