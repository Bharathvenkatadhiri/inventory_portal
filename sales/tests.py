"""Selling through /api/v1: replying to a customer's PO, sales orders,
dispatches and GST invoices; and what the buyer sees of them."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from inventory.models import Item, StockEntry, Warehouse
from marketplace.documents import financial_year
from organizations.models import BusinessRelationship, Membership
from organizations.tests import add_member, make_organization
from plans import erp
from purchase.models import PurchaseOrder
from purchase.tests import INTERSTATE_GSTIN, PurchaseTestCase

FY = financial_year(timezone.localdate())


class SalesTestCase(PurchaseTestCase):
    """The buyer (Vaigai) from PurchaseTestCase, plus the supplier's (Kaveri's) own
    items and stock. Tests start signed in as the buyer."""
    supplier_plan = erp.STARTER

    def setUp(self):
        super().setUp()
        self.supplier.erp_plan = self.supplier_plan
        self.supplier.save(update_fields=['erp_plan'])
        self.fs = Warehouse.objects.create(company=self.supplier, name="Foundry store", is_default=True)
        self.s_bolt = Item.objects.create(company=self.supplier, code="KC-BOLT", name="Bolt (ours)", kind="finished_good",
                                          unit="NOS", hsn_code="7318", gst_rate=18, sale_price=7)
        self.s_bar = Item.objects.create(company=self.supplier, code="KC-BAR", name="Bar (ours)", kind="finished_good",
                                         unit="KGS", hsn_code="7214", gst_rate=18)
        for item, quantity, rate in ((self.s_bolt, 800, '5'), (self.s_bar, 300, '70')):
            StockEntry.objects.create(company=self.supplier, item=item, warehouse=self.fs, kind='opening', quantity=quantity,
                                      rate=Decimal(rate), entry_date=timezone.localdate())

    def as_supplier(self, user=None):
        self.login(user or self.supplier_admin.user)

    def accept(self, po, mapping=None, **extra):
        mapping = mapping or [self.s_bolt.pk, self.s_bar.pk]
        lines = [{'purchase_order_line_id': line['id'], 'item_id': item} for line, item in zip(po['lines'], mapping)]
        return self.api('post', f"/sales/orders-received/{po['id']}/accept", {'dispatch_from_id': self.fs.pk, 'lines': lines, **extra})

    def accepted(self):
        """A PO issued by the buyer and accepted by the supplier; signed in as the supplier."""
        po = self.issued()
        self.as_supplier()
        result = self.ok(self.accept(po))
        return po, self.ok(self.api('get', f"/sales-orders/{result['sales_order_id']}"))

    def send(self, order, quantities, **extra):
        lines = [{'order_line_id': line['id'], 'quantity': q} for line, q in zip(order['lines'], quantities)]
        return self.api('post', f"/sales-orders/{order['id']}/dispatches", {'lines': lines, **extra})

    def invoice(self, order, quantities):
        lines = [{'order_line_id': line['id'], 'quantity': q} for line, q in zip(order['lines'], quantities)]
        return self.api('post', '/sales-invoices', {'order_id': order['id'], 'lines': lines})


class ReplyTests(SalesTestCase):
    def test_accepting_makes_a_confirmed_sales_order_at_the_pos_terms(self):
        po = self.issued()
        self.as_supplier()
        received = self.ok(self.api('get', '/sales/orders-received'))[0]
        self.assertTrue(received['can_reply'])
        self.assertEqual(received['supplier_response'], '')
        result = self.ok(self.accept(po))
        order = self.ok(self.api('get', f"/sales-orders/{result['sales_order_id']}"))
        self.assertEqual((order['status'], order['number'], order['customer_po_number']), ('confirmed', f"SO-{FY}-0001", po['number']))
        self.assertTrue(order['from_purchase_order'])
        self.assertEqual([(l['item_code'], Decimal(l['quantity']), Decimal(l['rate'])) for l in order['lines']],
                         [('KC-BOLT', Decimal('1000'), Decimal('6.25')), ('KC-BAR', Decimal('250'), Decimal('76.50'))])
        self.assertEqual(order['lines'][0]['customer_item'], 'BOLT Hex bolt')
        self.assertEqual(order['payment_terms_days'], 30)
        self.assertIn("accepted", self.refused(self.accept(po)))
        # The buyer sees the reply.
        self.login(self.admin.user)
        mine = self.ok(self.api('get', f"/purchase-orders/{po['id']}"))
        self.assertEqual((mine['supplier_response'], mine['supplier_response_label'], mine['supplier_on_network']), ('accepted', 'Accepted', True))

    def test_the_next_order_suggests_the_same_items(self):
        self.accepted()
        self.login(self.admin.user)
        second = self.issued()
        self.as_supplier()
        received = next(o for o in self.ok(self.api('get', '/sales/orders-received')) if o['id'] == second['id'])
        self.assertEqual(received['suggested_items'], {str(second['lines'][0]['id']): self.s_bolt.pk, str(second['lines'][1]['id']): self.s_bar.pk})

    def test_every_line_needs_an_item_in_the_same_unit(self):
        po = self.issued()
        self.as_supplier()
        self.assertIn("Choose your own item", self.refused(self.accept(po, mapping=[self.s_bolt.pk, None])))
        self.assertIn("kept in KGS", self.refused(self.accept(po, mapping=[self.s_bar.pk, self.s_bar.pk])))
        self.assertFalse(PurchaseOrder.objects.get(pk=po['id']).supplier_response)

    def test_decline_with_a_reason_and_the_buyer_can_only_cancel(self):
        po = self.issued()
        self.as_supplier()
        self.assertIn("why", self.refused(self.api('post', f"/sales/orders-received/{po['id']}/decline", {'reason': ' '})))
        self.ok(self.api('post', f"/sales/orders-received/{po['id']}/decline", {'reason': 'No capacity this month'}), 204)
        self.assertFalse(self.ok(self.api('get', '/sales/orders-received'))[0]['can_reply'])
        self.login(self.admin.user)
        mine = self.ok(self.api('get', f"/purchase-orders/{po['id']}"))
        self.assertEqual((mine['supplier_response'], mine['decline_reason']), ('declined', 'No capacity this month'))
        self.assertFalse(mine['actions']['receive'])
        self.assertIn("declined", self.refused(self.receive(mine, ['1', '0'])))
        self.assertEqual(self.ok(self.api('post', f"/purchase-orders/{po['id']}/cancel"))['status'], 'cancelled')

    def test_drafts_and_other_suppliers_orders_cant_be_answered(self):
        draft = self.draft()
        self.as_supplier()
        self.refused(self.accept(draft), 404)
        other, other_admin = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.login(self.admin.user)
        po = self.issued()
        self.login(other_admin.user)
        self.refused(self.api('post', f"/sales/orders-received/{po['id']}/decline", {'reason': 'x'}), 404)

    def test_the_relationship_must_still_be_active(self):
        po = self.issued()
        BusinessRelationship.objects.filter(buyer=self.company, supplier=self.supplier).update(status=BusinessRelationship.Status.BLOCKED)
        self.as_supplier()
        self.refused(self.accept(po))


class GrowthReplyTests(SalesTestCase):
    supplier_plan = erp.GROWTH

    def test_replying_needs_so_approve_on_plans_with_approvals(self):
        po = self.issued()
        executive = add_member(self.supplier, "exec@kaveri.example", "sales_executive").user
        self.as_supplier(executive)
        self.assertFalse(self.ok(self.api('get', '/sales/orders-received'))[0]['can_reply'])
        self.refused(self.accept(po), 403)
        manager = add_member(self.supplier, "manager@kaveri.example", "sales_manager").user
        self.as_supplier(manager)
        self.ok(self.accept(po))

    def test_manual_orders_go_for_approval(self):
        executive = add_member(self.supplier, "exec@kaveri.example", "sales_executive").user
        self.as_supplier(executive)
        body = {'customer_id': self.company.pk, 'dispatch_from_id': self.fs.pk, 'lines': [{'item_id': self.s_bolt.pk, 'quantity': '10'}]}
        order = self.ok(self.api('post', '/sales-orders', body))
        self.assertEqual(Decimal(order['lines'][0]['rate']), Decimal('7'))  # the item's sale price
        self.assertEqual(self.ok(self.api('post', f"/sales-orders/{order['id']}/submit"))['status'], 'pending_approval')
        self.refused(self.api('post', f"/sales-orders/{order['id']}/approve"), 403)
        self.as_supplier()
        self.assertEqual(self.ok(self.api('post', f"/sales-orders/{order['id']}/approve"))['status'], 'confirmed')


class ManualOrderTests(SalesTestCase):
    def test_only_active_customers(self):
        stranger, _ = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.as_supplier()
        self.assertEqual([c['name'] for c in self.ok(self.api('get', '/sales/customers'))], ['Vaigai'])
        body = {'customer_id': stranger.pk, 'dispatch_from_id': self.fs.pk, 'lines': [{'item_id': self.s_bolt.pk, 'quantity': '1'}]}
        self.assertIn("active customer", self.refused(self.api('post', '/sales-orders', body)))

    def test_interstate_customer_gets_igst(self):
        vega, _ = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        BusinessRelationship.objects.create(buyer=vega, supplier=self.supplier, initiated_by=self.supplier, status='active')
        self.as_supplier()
        body = {'customer_id': vega.pk, 'dispatch_from_id': self.fs.pk, 'lines': [{'item_id': self.s_bolt.pk, 'quantity': '100', 'rate': '10'}]}
        order = self.ok(self.api('post', '/sales-orders', body))
        self.assertTrue(order['is_interstate'])
        self.assertEqual(Decimal(order['igst']), Decimal('180'))


class DispatchTests(SalesTestCase):
    def test_dispatch_takes_stock_out_at_average_cost(self):
        po, order = self.accepted()
        dc = self.ok(self.send(order, ['600', '0'], vehicle_number='tn 37 x 1'))
        self.assertEqual((dc['number'], dc['vehicle_number']), (f"DC-{FY}-0001", 'TN 37 X 1'))
        out = StockEntry.objects.get(kind='dispatch')
        self.assertEqual((out.quantity, out.rate, out.reference), (Decimal('-600'), Decimal('5.00'), dc['number']))
        order = self.ok(self.api('get', f"/sales-orders/{order['id']}"))
        self.assertEqual(order['status'], 'partially_dispatched')
        self.assertEqual(Decimal(order['lines'][0]['on_hand']), Decimal('200'))
        # More than is still to send, or more than is in stock.
        self.assertIn("only 400 NOS is still to send", self.refused(self.send(order, ['401', '0'])))
        self.assertIn("only 250 KGS is still to send", self.refused(self.send(order, ['0', '250.5'])))
        self.assertIn("only 200 NOS in Foundry store", self.refused(self.send(order, ['400', '0'])))

    def test_dispatches_until_stock_runs_out_and_the_buyer_sees_them(self):
        po, order = self.accepted()  # 1000 bolts ordered, 800 in stock
        self.ok(self.send(order, ['500', '250']))
        self.ok(self.send(order, ['300', '0']))
        self.assertIn("only 0 NOS", self.refused(self.send(order, ['1', '0'])))
        order = self.ok(self.api('get', f"/sales-orders/{order['id']}"))
        self.assertEqual(order['status'], 'partially_dispatched')  # 200 bolts still to send
        self.assertEqual(len(order['dispatches']), 2)
        self.login(self.admin.user)
        mine = self.ok(self.api('get', f"/purchase-orders/{po['id']}"))
        self.assertEqual(len(mine['supplier_dispatches']), 2)

    def test_close_and_cancel_rules(self):
        po, order = self.accepted()
        self.assertIn("Tell the customer why", self.refused(self.api('post', f"/sales-orders/{order['id']}/cancel", {'reason': ''})))
        self.ok(self.send(order, ['10', '0']))
        self.assertIn("Close it instead", self.refused(self.api('post', f"/sales-orders/{order['id']}/cancel", {'reason': 'x'})))
        self.assertEqual(self.ok(self.api('post', f"/sales-orders/{order['id']}/close"))['status'], 'closed')
        self.refused(self.send(order, ['10', '0']))

    def test_cancelling_an_accepted_order_tells_the_buyer(self):
        po, order = self.accepted()
        self.ok(self.api('post', f"/sales-orders/{order['id']}/cancel", {'reason': 'Raw material shortage'}))
        self.login(self.admin.user)
        mine = self.ok(self.api('get', f"/purchase-orders/{po['id']}"))
        self.assertEqual(mine['supplier_response'], 'declined')
        self.assertIn("Raw material shortage", mine['decline_reason'])


class InvoiceTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.po, self.order = self.accepted()
        self.ok(self.send(self.order, ['600', '250']))

    def test_drafts_have_no_number_and_posting_numbers_without_gaps(self):
        throwaway = self.ok(self.invoice(self.order, ['100', '0']))
        self.assertEqual(throwaway['number'], '')
        self.ok(self.api('delete', f"/sales-invoices/{throwaway['id']}"), 204)
        first = self.ok(self.invoice(self.order, ['600', '0']))
        first = self.ok(self.api('post', f"/sales-invoices/{first['id']}/post"))
        self.assertEqual((first['number'], first['status']), (f"INV-{FY}-0001", 'posted'))
        self.assertEqual(Decimal(first['taxable_value']), Decimal('3750'))
        self.assertEqual(first['lines'][0]['hsn_code'], '7318')
        self.assertEqual(first['due_date'], (timezone.localdate() + timedelta(days=30)).isoformat())
        second = self.ok(self.invoice(self.order, ['0', '250']))
        self.assertEqual(self.ok(self.api('post', f"/sales-invoices/{second['id']}/post"))['number'], f"INV-{FY}-0002")

    def test_cant_invoice_more_than_dispatched_and_not_invoiced(self):
        self.assertIn("only 600", self.refused(self.invoice(self.order, ['601', '0'])))
        draft = self.ok(self.invoice(self.order, ['600', '0']))
        self.assertIn("only 0", self.refused(self.invoice(self.order, ['1', '0'])))
        self.ok(self.api('post', f"/sales-invoices/{draft['id']}/post"))

    def test_cancel_keeps_the_number_and_frees_the_quantity(self):
        inv = self.ok(self.invoice(self.order, ['600', '250']))
        inv = self.ok(self.api('post', f"/sales-invoices/{inv['id']}/post"))
        self.assertIn("why", self.refused(self.api('post', f"/sales-invoices/{inv['id']}/cancel", {'reason': ''})))
        cancelled = self.ok(self.api('post', f"/sales-invoices/{inv['id']}/cancel", {'reason': 'Wrong rate'}))
        self.assertEqual((cancelled['status'], cancelled['number']), ('cancelled', inv['number']))
        again = self.ok(self.invoice(self.order, ['600', '250']))
        self.assertEqual(self.ok(self.api('post', f"/sales-invoices/{again['id']}/post"))['number'], f"INV-{FY}-0002")

    def test_the_buyer_sees_posted_invoices_on_their_order(self):
        draft = self.ok(self.invoice(self.order, ['100', '0']))
        self.login(self.admin.user)
        self.assertEqual(self.ok(self.api('get', f"/purchase-orders/{self.po['id']}"))['supplier_invoices'], [])
        self.as_supplier()
        self.ok(self.api('post', f"/sales-invoices/{draft['id']}/post"))
        self.login(self.admin.user)
        invoices = self.ok(self.api('get', f"/purchase-orders/{self.po['id']}"))['supplier_invoices']
        self.assertEqual([i['number'] for i in invoices], [f"INV-{FY}-0001"])

    def test_other_companies_and_roles(self):
        other, other_admin = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.login(other_admin.user)
        self.refused(self.api('get', f"/sales-orders/{self.order['id']}"), 404)
        operator = add_member(self.supplier, "op@kaveri.example", "machine_operator").user
        self.as_supplier(operator)
        self.refused(self.api('get', '/sales-orders'), 403)
        self.assertTrue(Membership.objects.filter(user=operator).exists())
