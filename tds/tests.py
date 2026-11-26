"""TDS on supplier bills: sections, thresholds with catch-up, rates by PAN,
the net payable, deposits, and the 26Q rows."""
from datetime import date
from decimal import Decimal

from django.utils import timezone

from accounts.models import Company
from organizations.models import BusinessRelationship
from purchase.tests import PurchaseTestCase

from . import rules, services


class TdsTestCase(PurchaseTestCase):
    """Vaigai (buyer, signed in) buys from Kaveri, PAN BBBBB0000B (a company)."""

    def setUp(self):
        super().setUp()
        self.rel = BusinessRelationship.objects.get(buyer=self.company, supplier=self.supplier)

    def set_section(self, section):
        return self.api('patch', f"/partners/{self.rel.pk}", {'tds_section': section})

    def bill_for(self, taxable, invoice):
        """Order, receive and bill `taxable` rupees (before GST) of bolts at ₹1,000 each."""
        qty = str(Decimal(taxable) / 1000)
        order = self.issued(lines=[{'item_id': self.bolt.pk, 'quantity': qty, 'rate': '1000'}])
        self.ok(self.receive(order, [qty]))
        bill = self.ok(self.api('post', '/purchase-bills', self.bill_body(order, [qty], invoice=invoice)))
        return self.ok(self.api('post', f"/purchase-bills/{bill['id']}/submit"))


class SectionTests(TdsTestCase):
    def test_194c_single_bill_then_yearly_catch_up(self):
        self.ok(self.set_section('194C'))
        small = self.bill_for(20000, 'K-1')
        self.assertEqual((small['tds_section'], Decimal(small['tds_amount'])), ('194C', 0))
        single = self.bill_for(40000, 'K-2')  # over ₹30,000 on its own: 2% (a company)
        self.assertEqual((Decimal(single['tds_base']), Decimal(single['tds_rate']), Decimal(single['tds_amount'])),
                         (Decimal('40000'), Decimal('2'), Decimal('800')))
        third = self.bill_for(50000, 'K-3')  # the year passes ₹1 lakh: the ₹20,000 bill is caught up too
        self.assertEqual((Decimal(third['tds_base']), Decimal(third['tds_amount'])), (Decimal('70000'), Decimal('1400')))

    def test_individual_or_huf_pays_1_percent_and_no_pan_20(self):
        self.ok(self.set_section('194C'))
        Company.objects.filter(pk=self.supplier.pk).update(pan='BBBPB0000B')
        self.assertEqual(Decimal(self.bill_for(40000, 'K-1')['tds_rate']), Decimal('1'))
        Company.objects.filter(pk=self.supplier.pk).update(pan='', gstin=None)
        self.assertEqual(Decimal(self.bill_for(40000, 'K-2')['tds_rate']), Decimal('20'))

    def test_194j_professional_after_50000_a_year(self):
        self.ok(self.set_section('194J_PROF'))
        self.assertEqual(Decimal(self.bill_for(30000, 'J-1')['tds_amount']), 0)
        second = self.bill_for(30000, 'J-2')
        self.assertEqual((Decimal(second['tds_base']), Decimal(second['tds_amount'])), (Decimal('60000'), Decimal('6000')))

    def test_194q_only_when_switched_on_and_above_50_lakh(self):
        self.ok(self.set_section('194Q'))
        self.assertEqual(self.bill_for(6000000, 'Q-1')['tds_section'], '')  # company hasn't switched 194Q on
        self.ok(self.api('patch', '/organization', {'tds_194q': True}))
        bill = self.bill_for(1000000, 'Q-2')  # the year: ₹70 lakh, of which ₹20 lakh is above ₹50 lakh
        self.assertEqual((Decimal(bill['tds_base']), Decimal(bill['tds_amount'])), (Decimal('2000000'), Decimal('2000')))

    def test_no_section_no_tds_and_section_must_be_known(self):
        self.assertEqual(self.bill_for(500000, 'N-1')['tds_amount'], '0.00')
        self.refused(self.set_section('194Z'))
        self.ok(self.set_section(''))

    def test_only_the_buyer_sets_the_section(self):
        self.login(self.supplier_admin.user)
        self.assertIn("other company", self.refused(self.set_section('194C')))


class NetPayableTests(TdsTestCase):
    def test_paying_the_net_settles_the_bill(self):
        self.ok(self.set_section('194C'))
        bill = self.bill_for(40000, 'K-1')
        net = Decimal(bill['total']) - Decimal('800')
        self.assertEqual(Decimal(bill['net_payable']), net)
        self.assertEqual(Decimal(bill['outstanding']), net)
        payables = self.ok(self.api('get', '/accounts/outstanding?direction=made'))
        self.assertEqual(Decimal(payables['total']), net)
        body = {'direction': 'made', 'party_id': self.supplier.pk, 'amount': str(net),
                'allocations': [{'document_id': bill['id'], 'amount': str(net)}]}
        self.ok(self.api('post', '/payments', body))
        self.assertEqual(self.ok(self.api('get', f"/purchase-bills/{bill['id']}"))['payment_status'], 'paid')


class DepositAndReturnTests(TdsTestCase):
    def setUp(self):
        super().setUp()
        self.ok(self.set_section('194C'))
        self.bill = self.bill_for(40000, 'K-1')
        today = timezone.localdate()
        q = (today.month - 4) % 12 // 3 + 1
        self.quarter = f"{today.year if today.month >= 4 else today.year - 1}-Q{q}"

    def test_register_deposit_and_26q(self):
        register = self.ok(self.api('get', f'/tds/register?quarter={self.quarter}'))
        self.assertEqual([(d['pan'], d['section'], Decimal(d['tds'])) for d in register['deductions']],
                         [('BBBBB0000B', '194C', Decimal('800'))])
        [month] = register['months']
        self.assertEqual((Decimal(month['deducted']), Decimal(month['balance'])), (Decimal('800'), Decimal('800')))
        self.assertEqual(month['due_date'], rules.due_date(timezone.localdate()).isoformat())
        body = {'period': timezone.localdate().replace(day=1).isoformat(), 'section': '194C', 'amount': '800',
                'deposited_on': timezone.localdate().isoformat(), 'bsr_code': '0510308', 'challan_serial': '42'}
        self.assertIn("BSR", self.refused(self.api('post', '/tds/deposits', {**body, 'bsr_code': '123'})))
        self.ok(self.api('post', '/tds/deposits', body), 201)
        self.assertIn("already recorded", self.refused(self.api('post', '/tds/deposits', body)))
        month = self.ok(self.api('get', f'/tds/register?quarter={self.quarter}'))['months'][0]
        self.assertEqual(Decimal(month['balance']), 0)
        response = self.client.get(f'/api/v1/tds/26q?quarter={self.quarter}')
        self.assertEqual(response.status_code, 200)
        csv_text = response.content.decode()
        self.assertIn('BBBBB0000B', csv_text)
        self.assertIn('94C', csv_text)
        self.assertIn('0510308', csv_text)
        self.assertIn('00042', csv_text)

    def test_roles(self):
        from organizations.tests import add_member
        keeper = add_member(self.company, "keeper@vaigai.example", "store_keeper").user
        self.login(keeper)
        self.refused(self.api('get', f'/tds/register?quarter={self.quarter}'), 403)


class RuleTests(TdsTestCase):
    def test_due_dates_interest_and_quarters(self):
        self.assertEqual(rules.due_date(date(2026, 11, 15)), date(2026, 12, 7))
        self.assertEqual(rules.due_date(date(2026, 12, 3)), date(2027, 1, 7))
        self.assertEqual(rules.due_date(date(2027, 3, 31)), date(2027, 4, 30))
        self.assertEqual(services.interest(Decimal('1000'), date(2026, 11, 15), date(2026, 12, 7)), 0)
        # Deducted in November, paid 10 January: Nov, Dec, Jan = 3 months at 1.5%.
        self.assertEqual(services.interest(Decimal('1000'), date(2026, 11, 15), date(2027, 1, 10)), Decimal('45'))
        self.assertEqual(services.quarter_range('2026-Q3'), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(services.quarter_range('2026-Q4'), (date(2027, 1, 1), date(2027, 3, 31)))

    def test_tan_is_checked(self):
        self.assertIn("TAN", self.refused(self.api('patch', '/organization', {'tan': 'ABC123'})))
        self.assertEqual(self.ok(self.api('patch', '/organization', {'tan': 'chea12345b'}))['tan'], 'CHEA12345B')
