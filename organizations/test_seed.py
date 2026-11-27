import tempfile
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from accounts.models import Company
from billing.models import OrganizationSubscription as Sub
from core.models import User
from organizations.management.commands import seed_erp_demo as seed
from organizations.models import BusinessRelationship, Membership


@override_settings(DEBUG=True, MEDIA_ROOT=tempfile.mkdtemp(prefix='seed-tests-'))
class SeedERPDemoTests(TestCase):
    def run_seed(self, *args):
        out = StringIO()
        call_command('seed_erp_demo', *args, stdout=out)
        return out.getvalue()

    def test_seeds_organizations_people_partners_and_subscriptions(self):
        output = self.run_seed()
        self.assertEqual(Company.objects.filter(erp_plan__gt='').count(), len(seed.ORGANIZATIONS))
        self.assertTrue(Company.objects.filter(gstin=seed.UNCLAIMED_GSTIN, memberships__isnull=True).exists())
        self.assertEqual(set(Sub.objects.values_list('status', flat=True)),
                         {Sub.ACTIVE, Sub.TRIALING, Sub.PAST_DUE, Sub.LAPSED})
        self.assertEqual(set(BusinessRelationship.objects.values_list('status', flat=True)),
                         set(BusinessRelationship.Status.values))
        shared = User.objects.get(email=seed.demo_email('lakshmi.accounts'))
        self.assertEqual(Membership.objects.filter(user=shared).count(), 2)
        admin = User.objects.get(email=seed.demo_email('arjun'))
        self.assertTrue(admin.check_password(seed.PASSWORD))
        from inventory.models import Item, StockEntry, Warehouse
        sri = Company.objects.get(gstin=seed.ORGANIZATIONS['sri_lakshmi'][0])
        self.assertEqual(Item.objects.filter(company=sri).count(), len(seed.ITEMS['sri_lakshmi']))
        self.assertEqual(list(Warehouse.objects.filter(company=sri, is_default=True).values_list('name', flat=True)), ['Plant store'])
        self.assertTrue(Warehouse.objects.filter(company=sri, address__label='Ambattur warehouse').exists())
        self.assertTrue(StockEntry.objects.filter(company=sri, item__code='BO-BOLT-M10').count() == 2)
        from purchase.models import PurchaseBill, PurchaseOrder
        self.assertEqual(sorted(PurchaseOrder.objects.filter(company=sri).values_list('status', flat=True)),
                         ['draft', 'partially_received', 'pending_approval'])
        self.assertTrue(PurchaseBill.objects.filter(company=sri, status='posted').exists())
        self.assertTrue(PurchaseOrder.objects.filter(supplier=sri, status='issued').exists())  # Tejas' order
        self.assertTrue(StockEntry.objects.filter(company=sri, kind='receipt').exists())
        from sales.models import SalesInvoice, SalesOrder
        kaveri = Company.objects.get(gstin=seed.ORGANIZATIONS['kaveri'][0])
        castings = PurchaseOrder.objects.get(company=sri, supplier=kaveri)
        self.assertEqual(castings.supplier_response, 'accepted')
        self.assertEqual(SalesOrder.objects.get(purchase_order=castings).status, 'partially_dispatched')
        invoice = SalesInvoice.objects.get(company=kaveri, status='posted')
        self.assertTrue(PurchaseBill.objects.filter(company=sri, supplier_invoice_number=invoice.number).exists())
        self.assertTrue(StockEntry.objects.filter(company=kaveri, kind='dispatch').exists())
        from adjustments.models import CreditDebitNote
        credit = CreditDebitNote.objects.get(company=kaveri, side='sales', status='posted')
        self.assertTrue(CreditDebitNote.objects.filter(company=sri, side='purchase', party_note_number=credit.number,
                                                       status='posted').exists())
        self.assertTrue(StockEntry.objects.filter(company=sri, kind='purchase_return').exists())
        self.assertTrue(sri.__class__.objects.get(pk=sri.pk).logo)
        from production.models import ProductionEntry
        run = ProductionEntry.objects.get(company=sri)
        self.assertEqual((run.created_by.email, run.quantity_produced), (seed.demo_email('murugan'), 40))
        self.assertTrue(StockEntry.objects.filter(company=sri, kind='production_receipt').exists())
        from finance.services import balance
        self.assertEqual(balance(invoice)['payment_status'], 'partly_paid')
        self.assertEqual(balance(PurchaseBill.objects.get(company=sri, supplier=kaveri))['payment_status'], 'partly_paid')
        self.assertIn("Sample GSTINs still free", output)
        self.assertIn("27AAGCN2468V1ZG", output)
        self.assertNotIn("33AABCS1234K1Z7  ", output.split("still free")[1])

    def test_refuses_to_seed_twice_and_flush_reseeds(self):
        self.run_seed()
        with self.assertRaises(CommandError):
            self.run_seed()
        self.run_seed('--flush')
        self.assertEqual(Company.objects.filter(erp_plan__gt='').count(), len(seed.ORGANIZATIONS))

    @override_settings(DEBUG=False)
    def test_needs_debug_or_force(self):
        with self.assertRaises(CommandError):
            self.run_seed()
        self.assertFalse(User.objects.exists())
