"""Paying micro and small suppliers on time (MSMED Act s.15-16, Income Tax
Act s.43B(h)) through /api/v1."""
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from organizations.models import BusinessRelationship
from purchase.models import PurchaseBill

from . import msme
from .tests import FinanceTestCase

TODAY = timezone.localdate()


class MsmeTestCase(FinanceTestCase):
    """Kaveri is a micro manufacturer with 30 days' credit; Vaigai's bill for its
    invoice (dated today) is posted in setUp. Signed in as Vaigai."""
    category, activity = 'micro', 'manufacturing'

    def connect(self, supplier, status=BusinessRelationship.Status.ACTIVE, terms=30):
        relationship = super().connect(supplier, status, terms)
        relationship.msme_category, relationship.msme_activity = self.category, self.activity
        relationship.udyam_number = 'UDYAM-MH-26-0012345'
        relationship.save()
        return relationship

    def setUp(self):
        super().setUp()
        self.login(self.admin.user)
        self.bill_obj = PurchaseBill.objects.get(pk=self.bill['id'])

    def report(self, fy=''):
        return self.ok(self.api('get', f"/accounts/msme?fy={fy}"))

    def pay_bill(self, amount, day):
        return self.ok(self.api('post', '/payments', {
            'direction': 'made', 'party_id': self.supplier.pk, 'amount': str(amount), 'payment_date': day.isoformat(),
            'mode': 'bank', 'reference': 'UTR1', 'allocations': [{'document_id': self.bill['id'], 'amount': str(amount)}]}))


class DueDateTests(MsmeTestCase):
    def test_bill_is_covered_and_due_in_its_credit_period(self):
        self.assertEqual((self.bill_obj.msme_category, self.bill_obj.msme_due_date), ('micro', self.bill_obj.invoice_date + timedelta(days=30)))
        self.assertLessEqual(self.bill_obj.due_date, self.bill_obj.msme_due_date)
        report = self.report()
        [row] = report['bills']
        self.assertEqual((row['effect'], Decimal(row['unpaid']), row['category']), ('open', Decimal(row['payable']), 'micro'))
        self.assertEqual([d['id'] for d in report['due_soon']], [])  # due in 30 days
        payables = self.ok(self.api('get', '/accounts/outstanding?direction=made'))
        self.assertEqual((payables['documents'][0]['msme'], payables['documents'][0]['msme_due_date']),
                         (True, self.bill_obj.msme_due_date.isoformat()))

    def test_credit_is_capped_at_45_days_and_15_without_terms(self):
        relationship = BusinessRelationship.objects.get(buyer=self.company, supplier=self.supplier)
        relationship.payment_terms_days = 90
        self.assertEqual(msme.allowed_days(relationship), 45)
        relationship.payment_terms_days = None
        self.assertEqual(msme.allowed_days(relationship), 15)
        relationship.msme_category = 'medium'
        self.assertFalse(msme.covered(relationship))
        relationship.msme_category, relationship.msme_activity = 'small', 'trading'
        self.assertFalse(msme.covered(relationship))

    def test_interest_is_compound_at_three_times_the_bank_rate(self):
        due = date(2026, 5, 1)
        self.assertEqual(msme.interest(Decimal('10000'), due, due), 0)
        # 31 days late at 3 x 5.75% = 17.25% a year, monthly rests.
        self.assertEqual(msme.interest(Decimal('10000'), due, due + timedelta(days=31)), Decimal('146.44'))
        self.assertGreater(msme.interest(Decimal('10000'), due, due + timedelta(days=365)), Decimal('1725'))  # compounding


class YearEndTests(MsmeTestCase):
    def overdue_by(self, days):
        """Moves the bill back so its due date was `days` ago."""
        invoice = TODAY - timedelta(days=30 + days)
        PurchaseBill.objects.filter(pk=self.bill_obj.pk).update(invoice_date=invoice, msme_due_date=invoice + timedelta(days=30),
                                                               due_date=invoice + timedelta(days=30))

    def test_overdue_this_year_then_disallowed_after_the_year_end(self):
        self.overdue_by(10)
        [row] = self.report()['bills']
        self.assertEqual((row['effect'], row['days_overdue']), ('overdue', 10))
        self.assertGreater(Decimal(row['interest']), 0)
        due = self.report()['due_soon']
        self.assertEqual([d['days_left'] for d in due], [-10])
        # Looking back from after the year end: still unpaid, so the expense is disallowed.
        start, end = msme.fy_bounds(msme.current_fy())
        [row] = msme.bill_rows(self.company, start, end, today=end + timedelta(days=5))
        self.assertEqual(row['effect'], 'disallowed')
        self.assertEqual(row['disallowed_expense'], self.bill_obj.taxable_value)

    def test_paid_late_within_the_year_is_allowed_with_interest(self):
        self.overdue_by(10)
        payable = Decimal(self.report()['bills'][0]['payable'])
        self.pay_bill(payable, TODAY)
        [row] = self.report()['bills']
        self.assertEqual((row['effect'], Decimal(row['paid_late']), Decimal(row['unpaid'])), ('allowed', payable, Decimal('0')))
        self.assertGreater(Decimal(row['interest']), 0)

    def test_paid_on_time_and_part_payments(self):
        payable = Decimal(self.report()['bills'][0]['payable'])
        self.pay_bill(Decimal("1000"), TODAY)
        [row] = self.report()['bills']
        self.assertEqual((Decimal(row['paid_on_time']), Decimal(row['unpaid'])), (Decimal('1000'), payable - 1000))
        summary = self.report()['summary']
        self.assertEqual((Decimal(summary['open']), Decimal(summary['overdue'])), (payable - 1000, Decimal('0')))

    def test_form_1_lists_dues_older_than_45_days(self):
        PurchaseBill.objects.filter(pk=self.bill_obj.pk).update(invoice_date=TODAY - timedelta(days=60))
        fy = msme.current_fy()
        half = 'H1' if TODAY.month in (4, 5, 6, 7, 8, 9) else 'H2'
        self.assertEqual(self.client.get(f"/api/v1/accounts/msme/form1.csv?fy={fy}&half=H3").status_code, 400)
        # The half year's end is in the future for the current half, so look at it from its last day.
        period_end = dict((h, e) for h, _s, e in msme.half_years(fy))[half]
        rows = msme.msme1_rows(self.company, period_end)
        self.assertEqual([r['bill'].pk for r in rows], [self.bill_obj.pk])
        response = self.client.get(f"/api/v1/accounts/msme/form1.csv?fy={fy}&half={half}")
        self.assertEqual(response.status_code, 200)
        self.assertIn('UDYAM-MH-26-0012345', response.content.decode())


class NotCoveredTests(MsmeTestCase):
    category, activity = 'small', 'trading'

    def test_traders_bills_arent_tracked(self):
        self.assertEqual(PurchaseBill.objects.get(pk=self.bill['id']).msme_category, '')
        self.assertEqual(self.report()['bills'], [])


class PartnerTermsTests(MsmeTestCase):
    def test_udyam_details_on_the_supplier(self):
        relationship = BusinessRelationship.objects.get(buyer=self.company, supplier=self.supplier)
        path = f"/partners/{relationship.pk}"
        self.assertIn("UDYAM-TN-03-0012345", self.refused(self.api('patch', path, {'udyam_number': 'UDYAM-123'})))
        self.assertIn("Choose", self.refused(self.api('patch', path, {'msme_category': 'large'})))
        partner = self.ok(self.api('patch', path, {'udyam_number': 'udyam-tn-03-0099887', 'msme_category': 'small', 'msme_activity': 'services'}))
        self.assertEqual((partner['udyam_number'], partner['msme_category'], partner['msme_covered']), ('UDYAM-TN-03-0099887', 'small', True))
        # The supplier doesn't see (or set) how the buyer has classified it.
        self.as_supplier()
        theirs = self.ok(self.api('get', '/partners'))
        mine = next(p for p in theirs if p['id'] == relationship.pk)
        self.assertEqual(mine['udyam_number'], '')
        self.refused(self.api('patch', path, {'msme_category': 'micro'}))
