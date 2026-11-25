from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from accounts.models import Company
from billing.models import OrganizationSubscription as Sub
from core.models import User
from organizations.management.commands import seed_erp_demo as seed
from organizations.models import BusinessRelationship, Membership


@override_settings(DEBUG=True)
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
