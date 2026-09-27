from django.core.management.base import BaseCommand

from marketplace import documents
from marketplace.models import Order, OrderDocument


class Command(BaseCommand):
    help = (
        "Issues missing purchase orders (awarded orders) and tax invoices (orders already dispatched), "
        "for orders that predate automatic document generation. Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="List what would be issued without issuing it.")

    def handle(self, *args, dry_run=False, **options):
        awarded = Order.objects.exclude(status__in=['submitted', 'quoted', 'cancelled']).exclude(documents__kind=OrderDocument.PURCHASE_ORDER)
        dispatched = Order.objects.filter(production_stage__in=['dispatched', 'delivered']).exclude(status='cancelled') \
            .exclude(documents__kind=OrderDocument.INVOICE)
        for label, orders, issue in (('purchase order', awarded, documents.issue_purchase_order),
                                     ('tax invoice', dispatched, documents.issue_invoice)):
            for order in orders.select_related('supplier', 'customer', 'quote', 'requirement').order_by('billno'):
                if dry_run:
                    self.stdout.write(f"Would issue {label} for order #{order.billno}")
                    continue
                document = documents.ensure_pdf(issue(order))
                self.stdout.write(f"Issued {label} {document.number} for order #{order.billno}")
