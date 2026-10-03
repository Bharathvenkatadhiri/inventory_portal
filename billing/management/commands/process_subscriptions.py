"""Expiry reminder emails (2 days before and on the day), renewals, retries, the end of grace periods, scheduled changes and
abandoned checkouts (billing.services.run_due). Run hourly from cron:

    5 * * * *  cd /app && python manage.py process_subscriptions
"""
from django.core.management.base import BaseCommand

from billing import services


class Command(BaseCommand):
    help = "Send expiry reminders and process subscription renewals, retries, expiries and stale checkouts."

    def handle(self, *args, **options):
        counts = services.run_due()
        self.stdout.write(", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in counts.items()))
