"""Quotations and RFQs through /api/v1: quoting a customer (send, revise,
accept on MakeSetu, convert to a sales order) and asking suppliers to quote
(their quotes, the comparison, awarding into purchase orders)."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from marketplace.documents import financial_year
from organizations.tests import add_member, make_organization
from purchase.models import PurchaseOrder
from purchase.tests import INTERSTATE_GSTIN
from sales.models import SalesOrder
from sales.tests import SalesTestCase

from .models import Quotation

TODAY = timezone.localdate()
FY = financial_year(TODAY)


class QuotationTestCase(SalesTestCase):
    """Kaveri (the supplier) quotes Vaigai (the buyer). Tests start signed in as Kaveri."""

    def setUp(self):
        super().setUp()
        self.as_supplier()

    def quote_body(self, **extra):
        return {'customer_id': self.company.pk, 'dispatch_from_id': self.fs.pk, 'enquiry_reference': 'Email of 20 Nov',
                'delivery_days': 10, 'terms': 'Freight extra.',
                'lines': [{'item_id': self.s_bolt.pk, 'quantity': '1000', 'rate': '6.50'}, {'item_id': self.s_bar.pk, 'quantity': '100'}],
                **extra}

    def quote(self, **extra):
        return self.ok(self.api('post', '/quotations', self.quote_body(**extra)))

    def sent(self, **extra):
        q = self.quote(**extra)
        return self.ok(self.api('post', f"/quotations/{q['id']}/send"))


class QuotationTests(QuotationTestCase):
    def test_draft_send_and_the_customer_sees_it(self):
        q = self.quote()
        self.assertEqual((q['number'], q['status'], q['valid_until']), (f"QT-{FY}-0001", 'draft', (TODAY + timedelta(days=30)).isoformat()))
        self.assertEqual([Decimal(l['rate']) for l in q['lines']], [Decimal('6.50'), Decimal('0')])  # bar has no sale price
        self.assertEqual(Decimal(q['totals']['taxable_value']), Decimal('6500'))
        self.login(self.admin.user)
        self.assertEqual(self.ok(self.api('get', '/purchase/quotations-received')), [])  # drafts stay private
        self.as_supplier()
        self.ok(self.api('post', f"/quotations/{q['id']}/send"))
        self.login(self.admin.user)
        [row] = self.ok(self.api('get', '/purchase/quotations-received'))
        self.assertEqual((row['number'], row['party']['name'], row['status']), (q['number'], 'Kaveri', 'sent'))
        seen = self.ok(self.api('get', f"/purchase/quotations-received/{q['id']}"))
        self.assertTrue(seen['actions']['respond'])
        self.assertFalse(seen['actions']['convert'])
        self.assertEqual(seen['notes'], '')  # internal notes stay with the seller

    def test_customer_accepts_and_the_seller_raises_the_sales_order(self):
        q = self.sent()
        self.login(self.admin.user)
        self.assertIn("why", self.refused(self.api('post', f"/purchase/quotations-received/{q['id']}/response", {'accepted': False})))
        self.assertEqual(self.ok(self.api('post', f"/purchase/quotations-received/{q['id']}/response", {'accepted': True}))['status'], 'accepted')
        self.as_supplier()
        result = self.ok(self.api('post', f"/quotations/{q['id']}/convert"))
        order = SalesOrder.objects.get(pk=result['sales_order_id'])
        self.assertEqual((order.status, order.customer, order.customer_po_number), ('draft', self.company, 'Email of 20 Nov'))
        self.assertEqual([l.rate for l in order.lines.order_by('position')], [Decimal('6.50'), Decimal('0')])
        self.assertIn(q['number'], order.notes)
        q = self.ok(self.api('get', f"/quotations/{q['id']}"))
        self.assertEqual((q['status'], q['sales_order_number']), ('converted', order.number))
        self.refused(self.api('post', f"/quotations/{q['id']}/convert"))

    def test_revisions(self):
        q = self.sent()
        r2 = self.ok(self.api('post', f"/quotations/{q['id']}/revise"))
        self.assertEqual((r2['number'], r2['status'], r2['revisions'][0]['status']), (f"{q['number']}-R2", 'draft', 'superseded'))
        self.assertEqual([x['number'] for x in self.ok(self.api('get', '/quotations'))], [r2['number']])
        body = self.quote_body(lines=[{'item_id': self.s_bolt.pk, 'quantity': '1000', 'rate': '6.25'}])
        self.ok(self.api('put', f"/quotations/{r2['id']}", body))
        self.refused(self.api('put', f"/quotations/{q['id']}", body))  # the sent one can't change
        # Deleting the new draft brings back the one that was sent.
        self.ok(self.api('delete', f"/quotations/{r2['id']}"), 204)
        self.assertEqual(Quotation.objects.get(pk=q['id']).status, 'sent')

    def test_expiry_and_recorded_answers(self):
        q = self.sent()
        Quotation.objects.filter(pk=q['id']).update(valid_until=TODAY - timedelta(days=1))
        q = self.ok(self.api('get', f"/quotations/{q['id']}"))
        self.assertEqual((q['status'], q['actions']['convert'], q['actions']['revise']), ('expired', False, True))
        self.assertIn("expired", self.refused(self.api('post', f"/quotations/{q['id']}/response", {'accepted': True})))
        q2 = self.sent()
        self.assertIn("Say why", self.refused(self.api('post', f"/quotations/{q2['id']}/response", {'accepted': False})))
        q2 = self.ok(self.api('post', f"/quotations/{q2['id']}/response", {'accepted': False, 'note': 'Price too high'}))
        self.assertEqual((q2['status'], q2['response_note']), ('declined', 'Price too high'))
        self.assertIn("before it's dated", self.refused(self.api('post', '/quotations', self.quote_body(
            quote_date=TODAY.isoformat(), valid_until=(TODAY - timedelta(days=1)).isoformat()))))

    def test_pdf_and_access(self):
        q = self.quote()
        response = self.client.get(f"/api/v1/quotations/{q['id']}/pdf")
        self.assertEqual((response.status_code, response['Content-Type']), (200, 'application/pdf'))
        self.login(self.admin.user)
        self.assertEqual(self.client.get(f"/api/v1/quotations/{q['id']}/pdf").status_code, 404)  # a draft
        self.assertEqual(self.api('get', f"/quotations/{q['id']}").status_code, 404)  # not ours
        self.as_supplier()
        self.ok(self.api('post', f"/quotations/{q['id']}/send"))
        self.login(self.admin.user)
        self.assertEqual(self.client.get(f"/api/v1/quotations/{q['id']}/pdf").status_code, 200)
        keeper = add_member(self.supplier, 'keeper@kaveri.example', 'store_keeper')
        self.login(keeper.user)
        self.assertEqual(self.api('get', '/quotations').status_code, 403)


class RfqTestCase(SalesTestCase):
    """Vaigai (buyer, signed in) asks Kaveri and Tapti to quote for 1000 bolts and 250 kg of bar."""

    def setUp(self):
        super().setUp()
        self.tapti, self.tapti_admin = make_organization("Tapti", INTERSTATE_GSTIN, "admin@tapti.example")
        self.connect(self.tapti)
        self.rfq = self.ok(self.api('post', '/rfqs', self.rfq_body()))

    def rfq_body(self, **extra):
        return {'title': 'Fasteners and bar for December', 'ship_to_id': self.store.pk,
                'respond_by': (TODAY + timedelta(days=5)).isoformat(), 'required_by': (TODAY + timedelta(days=20)).isoformat(),
                'lines': [{'item_id': self.bolt.pk, 'quantity': '1000', 'description': 'Grade 8.8, zinc plated'},
                          {'item_id': self.bar.pk, 'quantity': '250'}],
                'supplier_ids': [self.supplier.pk, self.tapti.pk], **extra}

    def lines(self):
        return [l['line']['id'] for l in self.rfq['lines']]

    def invite(self, company):
        return next(s['id'] for s in self.rfq['suppliers'] if s['supplier']['id'] == company.pk)


class RfqTests(RfqTestCase):
    def test_send_quote_compare_and_award(self):
        self.assertEqual((self.rfq['number'], self.rfq['status'], len(self.rfq['suppliers'])), (f"RFQ-{FY}-0001", 'draft', 2))
        self.as_supplier()
        self.assertEqual(self.ok(self.api('get', '/sales/rfqs-received')), [])  # not sent yet
        self.login(self.admin.user)
        self.rfq = self.ok(self.api('post', f"/rfqs/{self.rfq['id']}/send"))
        bolt, bar = self.lines()

        # Kaveri quotes on MakeSetu for both lines.
        self.as_supplier()
        [got] = self.ok(self.api('get', '/sales/rfqs-received'))
        self.assertEqual((got['rfq_number'], got['buyer']['name'], got['lines'][0]['description'], got['can_quote']),
                         (self.rfq['number'], 'Vaigai', 'Grade 8.8, zinc plated', True))
        self.ok(self.api('post', f"/sales/rfqs-received/{got['invite_id']}/quote", {
            'their_reference': 'KC-Q-88', 'delivery_days': 7, 'payment_terms_days': 30,
            'lines': [{'rfq_line_id': bolt, 'rate': '6.00'}, {'rfq_line_id': bar, 'rate': '75'}]}))

        # Tapti sent theirs by email: bolts only, cheaper.
        self.login(self.admin.user)
        self.rfq = self.ok(self.api('post', f"/rfqs/{self.rfq['id']}/suppliers/{self.invite(self.tapti)}/quote", {
            'delivery_days': 12, 'lines': [{'rfq_line_id': bolt, 'rate': '5.80'}, {'rfq_line_id': bar}]}))
        compare = {r['line']['item_code']: r['offers'] for r in self.rfq['lines']}
        lowest = {o['invite_id'] for o in compare['BOLT'] if o['lowest']}
        self.assertEqual(lowest, {self.invite(self.tapti)})
        self.assertEqual([o['lowest'] for o in compare['BAR']], [False])  # one quote: nothing to compare
        totals = {s['supplier']['name']: (s['lines_quoted'], s['complete'], Decimal(s['taxable_value'])) for s in self.rfq['suppliers']}
        self.assertEqual(totals, {'Kaveri': (2, True, Decimal('24750')), 'Tapti': (1, False, Decimal('5800'))})

        self.assertIn("didn't quote for", self.refused(self.api('post', f"/rfqs/{self.rfq['id']}/award", {
            'awards': [{'rfq_line_id': bar, 'invite_id': self.invite(self.tapti)}]})))
        result = self.ok(self.api('post', f"/rfqs/{self.rfq['id']}/award", {'awards': [
            {'rfq_line_id': bolt, 'invite_id': self.invite(self.tapti)}, {'rfq_line_id': bar, 'invite_id': self.invite(self.supplier)}]}))
        orders = {o.supplier.name: o for o in PurchaseOrder.objects.filter(pk__in=result['purchase_order_ids'])}
        self.assertEqual({k: [(l.item.code, l.rate) for l in o.lines.all()] for k, o in orders.items()},
                         {'Tapti': [('BOLT', Decimal('5.80'))], 'Kaveri': [('BAR', Decimal('75.00'))]})
        self.assertEqual((orders['Kaveri'].status, orders['Kaveri'].supplier_reference, orders['Kaveri'].payment_terms_days),
                         ('draft', 'KC-Q-88', 30))
        self.assertEqual(result['rfq']['status'], 'awarded')

        # Kaveri sees what it won, and can't change its quote now.
        self.as_supplier()
        got = self.ok(self.api('get', f"/sales/rfqs-received/{got['invite_id']}"))
        self.assertEqual(([l['awarded_to_you'] for l in got['lines']], got['status'], got['can_quote']), ([False, True], 'awarded', False))
        self.assertIn("awarded", self.refused(self.api('post', f"/sales/rfqs-received/{got['invite_id']}/quote", {
            'lines': [{'rfq_line_id': bolt, 'rate': '5'}]})))

    def test_award_everything_to_one_supplier_and_declines(self):
        self.rfq = self.ok(self.api('post', f"/rfqs/{self.rfq['id']}/send"))
        bolt, bar = self.lines()
        self.as_supplier()
        [got] = self.ok(self.api('get', '/sales/rfqs-received'))
        self.ok(self.api('post', f"/sales/rfqs-received/{got['invite_id']}/decline", {'reason': 'No capacity this month'}))
        self.login(self.admin.user)
        self.ok(self.api('post', f"/rfqs/{self.rfq['id']}/suppliers/{self.invite(self.tapti)}/quote", {
            'lines': [{'rfq_line_id': bolt, 'rate': '5.80'}, {'rfq_line_id': bar, 'rate': '74'}]}))
        result = self.ok(self.api('post', f"/rfqs/{self.rfq['id']}/award", {'invite_id': self.invite(self.tapti)}))
        [order] = PurchaseOrder.objects.filter(pk__in=result['purchase_order_ids'])
        self.assertEqual((order.supplier, order.lines.count()), (self.tapti, 2))
        statuses = {s['supplier']['name']: s['status'] for s in result['rfq']['suppliers']}
        self.assertEqual(statuses, {'Kaveri': 'declined', 'Tapti': 'awarded'})

    def test_rules(self):
        self.assertIn("title", self.refused(self.api('put', f"/rfqs/{self.rfq['id']}", self.rfq_body(title=''))))
        empty = self.ok(self.api('post', '/rfqs', self.rfq_body(supplier_ids=[])))
        self.assertIn("at least one supplier", self.refused(self.api('post', f"/rfqs/{empty['id']}/send")))
        stranger, _ = make_organization("Stranger", "27DDDDD0000D1Z5", "admin@stranger.example")
        self.refused(self.api('put', f"/rfqs/{self.rfq['id']}", self.rfq_body(supplier_ids=[stranger.pk])))
        self.rfq = self.ok(self.api('post', f"/rfqs/{self.rfq['id']}/send"))
        self.refused(self.api('put', f"/rfqs/{self.rfq['id']}", self.rfq_body()))  # sent: no more edits
        self.login(self.tapti_admin.user)
        [got] = self.ok(self.api('get', '/sales/rfqs-received'))
        self.assertEqual(got['rfq_number'], self.rfq['number'])
        self.assertEqual(self.api('get', f"/rfqs/{self.rfq['id']}").status_code, 404)  # the buyer's view isn't theirs
        self.login(self.admin.user)
        response = self.client.get(f"/api/v1/rfqs/{self.rfq['id']}/pdf")
        self.assertEqual((response.status_code, response['Content-Type']), (200, 'application/pdf'))
        self.assertIn("Say why", self.refused(self.api('post', f"/rfqs/{self.rfq['id']}/cancel", {'reason': ''})))
        self.assertEqual(self.ok(self.api('post', f"/rfqs/{self.rfq['id']}/cancel", {'reason': 'Order dropped'}))['status'], 'cancelled')
