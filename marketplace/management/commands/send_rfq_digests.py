"""Daily RFQ digest for supplier plans with basic RFQ alerts (Free).

Delivers each such supplier's waiting RFQs up to this month's quota
(plans.rfq_inbox), then emails the ones received in the last day. Run once
a day from cron, e.g.:

    0 7 * * *  cd /app && python manage.py send_rfq_digests
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import ManufacturerProfile
from marketplace import emails
from plans import access, catalog, rfq_inbox
from plans.models import RFQReceipt


class Command(BaseCommand):
    help = "Email suppliers on basic RFQ alerts the RFQs they received in the last day."

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=1)
        sent = 0
        suppliers = ManufacturerProfile.objects.filter(is_deleted=False, accepting_rfqs=True, user__is_active=True).select_related('user')
        for supplier in suppliers:
            if access.feature_level(supplier, 'rfq_alerts') != catalog.BASIC:
                continue
            rfq_inbox.deliver(supplier)
            requirements = [
                receipt.requirement for receipt in
                RFQReceipt.objects.filter(supplier=supplier, received_at__gte=since).select_related('requirement')
                if receipt.requirement.is_open_for_quotes()
            ]
            if requirements:
                emails.send_rfq_digest(supplier, requirements)
                sent += 1
        self.stdout.write(f"Sent {sent} RFQ digest(s).")
