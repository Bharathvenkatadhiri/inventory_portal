"""The Celery app for background jobs. Settings are the CELERY_* names in
core/settings.py; tasks live in each app's tasks.py.

Run a worker with: celery -A core worker -l info
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

app = Celery("makesetu")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
