"""Purchasing through /api/v1: order -> goods receipt -> bill."""
import json
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from billing.models import OrganizationSubscription
from inventory.models import Item, StockEntry, Warehouse
from marketplace.documents import financial_year
from organizations.models import BusinessRelationship
from organizations.tests import GSTIN, PASSWORD, add_member, make_organization
from plans import erp

from .models import PurchaseBill, PurchaseOrder

SUPPLIER_GSTIN = "33BBBBB0000B1Z5"   # Tamil Nadu, like the buyer
INTERSTATE_GSTIN = "29CCCCC0000C1Z5"  # Karnataka
Status = BusinessRelationship.Status


class PurchaseTestCase(TestCase):
    plan = erp.STARTER

    def setUp(self):
        cache.clear()
        self.company, self.admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example", plan=self.plan)
        self.supplier, self.supplier_admin = make_organization("Kaveri", SUPPLIER_GSTIN, "admin@kaveri.example")
        self.connect(self.supplier)
        self.store = Warehouse.objects.create(company=self.company, name="Main store", is_default=True)
        self.bolt = Item.objects.create(company=self.company, code="BOLT", name="Hex bolt", kind="component", unit="NOS",
                                        hsn_code="7318", gst_rate=18, purchase_price=Decimal("6.25"))
        self.bar = Item.objects.create(company=self.company, code="BAR", name="EN8 bar", kind="raw_material", unit="KGS",
                                       hsn_code="7214", gst_rate=18, purchase_price=78)
        self.login(self.admin.user)

    def connect(self, supplier, status=Status.ACTIVE, terms=30):
        return BusinessRelationship.objects.create(buyer=self.company, supplier=supplier, initiated_by=self.company,
                                                   status=status, payment_terms_days=terms)

    def login(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.email, password=PASSWORD))

    def api(self, method, path, body=None):
        kwargs = {'content_type': 'application/json'}
        if body is not None:
            kwargs['data'] = json.dumps(body)
        return getattr(self.client, method)(f'/api/v1{path}', **kwargs)

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content)
        return response.json() if response.content else None

    def refused(self, response, status=400):
        self.assertEqual(response.status_code, status, response.content)
        return response.json()['detail']

    def order_body(self, supplier=None, lines=None, **extra):
        return {
            'supplier_id': (supplier or self.supplier).pk, 'ship_to_id': self.store.pk,
            'lines': lines if lines is not None else [{'item_id': self.bolt.pk, 'quantity': '1000'}, {'item_id': self.bar.pk, 'quantity': '250', 'rate': '76.5'}],
            **extra,
        }

    def draft(self, **kwargs):
        return self.ok(self.api('post', '/purchase-orders', self.order_body(**kwargs)))

    def issued(self, **kwargs):
        order = self.draft(**kwargs)
        return self.ok(self.api('post', f"/purchase-orders/{order['id']}/submit"))

    def receive(self, order, quantities, **extra):
        lines = [{'order_line_id': line['id'], 'quantity': q} for line, q in zip(order['lines'], quantities)]
        return self.api('post', f"/purchase-orders/{order['id']}/receipts", {'lines': lines, **extra})

    def bill_body(self, order, quantities, invoice='INV-101', **extra):
        lines = [{'order_line_id': line['id'], 'quantity': q} for line, q in zip(order['lines'], quantities)]
        return {'order_id': order['id'], 'supplier_invoice_number': invoice,
                'invoice_date': timezone.localdate().isoformat(), 'lines': lines, **extra}


class OrderTests(PurchaseTestCase):
    def test_draft_totals_with_cgst_and_sgst_within_the_state(self):
        order = self.draft()
        self.assertEqual(order['status'], 'draft')
        self.assertEqual(order['number'], f"PO-{financial_year(timezone.localdate())}-0001")
        bolt, bar = order['lines']
        self.assertEqual(Decimal(bolt['rate']), Decimal('6.25'))  # from the item when not given
        self.assertEqual(Decimal(bolt['taxable_value']), Decimal('6250'))
        self.assertEqual(Decimal(bar['taxable_value']), Decimal('19125'))
        self.assertFalse(order['is_interstate'])
        self.assertEqual(Decimal(order['taxable_value']), Decimal('25375'))
        self.assertEqual(Decimal(order['cgst']) + Decimal(order['sgst']), Decimal('4567.50'))
        self.assertEqual(Decimal(order['igst']), 0)
        self.assertEqual(Decimal(order['total']), Decimal('29943'))  # 29942.50 rounded
        self.assertEqual(Decimal(order['round_off']), Decimal('0.50'))
        self.assertEqual(order['payment_terms_days'], 30)  # from the relationship

    def test_igst_across_states(self):
        other, _ = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.connect(other)
        order = self.draft(supplier=other)
        self.assertTrue(order['is_interstate'])
        self.assertEqual(Decimal(order['igst']), Decimal('4567.50'))
        self.assertEqual(Decimal(order['cgst']), 0)

    def test_only_active_suppliers(self):
        invited, _ = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.connect(invited, status=Status.INVITED)
        self.assertIn("active supplier", self.refused(self.api('post', '/purchase-orders', self.order_body(supplier=invited))))
        self.assertEqual([s['name'] for s in self.ok(self.api('get', '/purchase/suppliers'))], ['Kaveri'])
        # A supplier blocked after a draft can't be issued to.
        order = self.draft()
        BusinessRelationship.objects.filter(supplier=self.supplier).update(status=Status.BLOCKED)
        self.refused(self.api('post', f"/purchase-orders/{order['id']}/submit"))

    def test_lines_are_checked(self):
        self.assertIn("at least one", self.refused(self.api('post', '/purchase-orders', self.order_body(lines=[]))))
        self.refused(self.api('post', '/purchase-orders', self.order_body(lines=[{'item_id': self.bolt.pk, 'quantity': '0'}])))
        self.refused(self.api('post', '/purchase-orders', self.order_body(lines=[{'item_id': self.bolt.pk, 'quantity': '1', 'gst_rate': '15'}])))
        other, _ = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        foreign = Item.objects.create(company=other, code="X", name="Not ours")
        self.assertIn("your items", self.refused(self.api('post', '/purchase-orders', self.order_body(lines=[{'item_id': foreign.pk, 'quantity': '1'}]))))

    def test_starter_issues_at_once_and_only_drafts_change(self):
        order = self.draft()
        self.ok(self.api('put', f"/purchase-orders/{order['id']}", self.order_body(notes='Deliver by noon')))
        order = self.ok(self.api('post', f"/purchase-orders/{order['id']}/submit"))
        self.assertEqual(order['status'], 'issued')
        self.assertEqual(order['approved_by'], 'admin@vaigai.example')
        self.assertIn("draft", self.refused(self.api('put', f"/purchase-orders/{order['id']}", self.order_body())))
        self.refused(self.api('delete', f"/purchase-orders/{order['id']}"))

    def test_delete_draft_and_cancel_issued(self):
        draft = self.draft()
        self.ok(self.api('delete', f"/purchase-orders/{draft['id']}"), 204)
        order = self.issued()
        self.assertEqual(self.ok(self.api('post', f"/purchase-orders/{order['id']}/cancel"))['status'], 'cancelled')

    def test_numbers_run_per_company(self):
        self.draft(), self.draft()
        self.assertEqual(self.draft()['number'][-4:], '0003')

    def test_another_company_cant_reach_our_orders(self):
        order = self.draft()
        other, other_admin = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.login(other_admin.user)
        self.refused(self.api('get', f"/purchase-orders/{order['id']}"), 404)
        self.assertEqual(self.ok(self.api('get', '/purchase-orders')), [])

    def test_lapsed_subscription_is_read_only(self):
        order = self.draft()
        OrganizationSubscription.objects.filter(company=self.company).update(
            status=OrganizationSubscription.LAPSED, trial_ends_at=timezone.now() - timedelta(days=1))
        self.ok(self.api('get', f"/purchase-orders/{order['id']}"))
        self.refused(self.api('post', f"/purchase-orders/{order['id']}/submit"), 402)


class ApprovalTests(PurchaseTestCase):
    plan = erp.GROWTH

    def test_executive_needs_a_managers_approval(self):
        executive = add_member(self.company, "exec@vaigai.example", "purchase_executive").user
        manager = add_member(self.company, "manager@vaigai.example", "purchase_manager").user
        self.login(executive)
        order = self.draft()
        order = self.ok(self.api('post', f"/purchase-orders/{order['id']}/submit"))
        self.assertEqual(order['status'], 'pending_approval')
        self.refused(self.api('post', f"/purchase-orders/{order['id']}/approve"), 403)
        self.login(manager)
        self.assertIn("Say why", self.refused(self.api('post', f"/purchase-orders/{order['id']}/reject", {'reason': ''})))
        order = self.ok(self.api('post', f"/purchase-orders/{order['id']}/reject", {'reason': 'Rate too high'}))
        self.assertEqual((order['status'], order['rejection_reason']), ('draft', 'Rate too high'))
        self.login(executive)
        self.ok(self.api('post', f"/purchase-orders/{order['id']}/submit"))
        self.login(manager)
        order = self.ok(self.api('post', f"/purchase-orders/{order['id']}/approve"))
        self.assertEqual((order['status'], order['approved_by'], order['rejection_reason']), ('issued', 'manager@vaigai.example', ''))

    def test_someone_who_can_approve_issues_directly(self):
        self.assertEqual(self.issued()['status'], 'issued')

    def test_bills_need_approval_too(self):
        order = self.issued()
        self.ok(self.receive(order, ['1000', '250']))
        executive = add_member(self.company, "exec@vaigai.example", "purchase_executive").user
        self.login(executive)
        bill = self.ok(self.api('post', '/purchase-bills', self.bill_body(order, ['1000', '250'])))
        bill = self.ok(self.api('post', f"/purchase-bills/{bill['id']}/submit"))
        self.assertEqual(bill['status'], 'pending_approval')
        self.refused(self.api('post', f"/purchase-bills/{bill['id']}/approve"), 403)
        self.login(self.admin.user)
        self.assertEqual(self.ok(self.api('post', f"/purchase-bills/{bill['id']}/approve"))['status'], 'posted')


class ReceiptTests(PurchaseTestCase):
    def test_partial_then_full_receipt_moves_stock(self):
        order = self.issued()
        receipt = self.ok(self.receive(order, ['600', '0'], supplier_challan='DC-77', vehicle_number='tn 09 ab 1234'))
        self.assertTrue(receipt['number'].startswith('GRN-'))
        self.assertEqual(receipt['vehicle_number'], 'TN 09 AB 1234')
        self.assertEqual(len(receipt['lines']), 1)
        order = self.ok(self.api('get', f"/purchase-orders/{order['id']}"))
        self.assertEqual(order['status'], 'partially_received')
        self.assertEqual(Decimal(order['lines'][0]['received_quantity']), 600)
        entry = StockEntry.objects.get(item=self.bolt)
        self.assertEqual((entry.kind, entry.quantity, entry.rate, entry.reference), ('receipt', Decimal('600'), Decimal('6.25'), receipt['number']))

        self.assertIn("only 400", self.refused(self.receive(order, ['401', '0'])))
        self.ok(self.receive(order, ['400', '250']))
        order = self.ok(self.api('get', f"/purchase-orders/{order['id']}"))
        self.assertEqual(order['status'], 'received')
        self.assertFalse(order['actions']['receive'])
        stock = {(r['item_code'], Decimal(r['quantity'])) for r in self.ok(self.api('get', '/stock'))}
        self.assertEqual(stock, {('BOLT', Decimal('1000')), ('BAR', Decimal('250'))})
        self.assertIn("received", self.refused(self.receive(order, ['1', '0'])))
        self.assertEqual(len(self.ok(self.api('get', '/goods-receipts'))), 2)

    def test_receipts_need_an_issued_order_and_sensible_dates(self):
        draft = self.draft()
        self.refused(self.receive(draft, ['1', '0']))
        order = self.issued()
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertIn("future", self.refused(self.receive(order, ['1', '0'], received_date=tomorrow)))
        self.assertIn("at least one", self.refused(self.receive(order, ['0', '0'])))

    def test_services_are_received_without_stock(self):
        service = Item.objects.create(company=self.company, code="SVC", name="Machining", kind="service", hsn_code="998898", gst_rate=18)
        order = self.issued(lines=[{'item_id': service.pk, 'quantity': '1', 'rate': '5000'}])
        self.ok(self.receive(order, ['1']))
        self.assertFalse(StockEntry.objects.exists())

    def test_store_keeper_receives_but_cant_order(self):
        order = self.issued()
        keeper = add_member(self.company, "keeper@vaigai.example", "store_keeper").user
        self.login(keeper)
        self.refused(self.api('post', '/purchase-orders', self.order_body()), 403)
        listed = self.ok(self.api('get', '/purchase-orders?status=open'))
        self.assertEqual([o['id'] for o in listed], [order['id']])
        self.assertTrue(self.ok(self.api('get', f"/purchase-orders/{order['id']}"))['actions']['receive'])
        self.ok(self.receive(order, ['10', '0']))

    def test_cancel_after_receipt_is_refused_but_close_works(self):
        order = self.issued()
        self.ok(self.receive(order, ['10', '0']))
        self.assertIn("Close it instead", self.refused(self.api('post', f"/purchase-orders/{order['id']}/cancel")))
        self.assertEqual(self.ok(self.api('post', f"/purchase-orders/{order['id']}/close"))['status'], 'closed')
        self.refused(self.receive(order, ['10', '0']))

    def test_roles_without_purchasing_cant_see_orders(self):
        self.issued()
        operator = add_member(self.company, "op@vaigai.example", "machine_operator").user
        self.login(operator)
        self.refused(self.api('get', '/purchase-orders'), 403)


class BillTests(PurchaseTestCase):
    def setUp(self):
        super().setUp()
        self.order = self.issued()
        self.ok(self.receive(self.order, ['600', '250']))

    def test_bill_received_quantities_with_due_date_and_price_check(self):
        body = self.bill_body(self.order, ['600', '250'])
        body['lines'][1]['rate'] = '77'
        bill = self.ok(self.api('post', '/purchase-bills', body))
        self.assertEqual(bill['status'], 'draft')
        self.assertTrue(bill['number'].startswith('PB-'))
        self.assertEqual(bill['due_date'], (timezone.localdate() + timedelta(days=30)).isoformat())
        self.assertEqual([l['rate_differs'] for l in bill['lines']], [False, True])
        self.assertEqual(Decimal(bill['taxable_value']), Decimal('3750') + Decimal('19250'))
        bill = self.ok(self.api('post', f"/purchase-bills/{bill['id']}/submit"))
        self.assertEqual(bill['status'], 'posted')  # Starter: no approval step
        order = self.ok(self.api('get', f"/purchase-orders/{self.order['id']}"))
        self.assertEqual([Decimal(l['billable_quantity']) for l in order['lines']], [0, 0])
        self.assertFalse(order['actions']['bill'])

    def test_cant_bill_more_than_received_and_not_billed(self):
        self.assertIn("only 600", self.refused(self.api('post', '/purchase-bills', self.bill_body(self.order, ['601', '0']))))
        self.ok(self.api('post', '/purchase-bills', self.bill_body(self.order, ['400', '0'])))
        # The draft holds those 400: only 200 left.
        self.assertIn("only 200", self.refused(self.api('post', '/purchase-bills', self.bill_body(self.order, ['201', '0'], invoice='INV-102'))))

    def test_an_invoice_number_is_entered_once_per_supplier(self):
        bill = self.ok(self.api('post', '/purchase-bills', self.bill_body(self.order, ['100', '0'])))
        self.assertIn("already entered", self.refused(self.api('post', '/purchase-bills', self.bill_body(self.order, ['100', '0'], invoice='inv-101'))))
        # Once cancelled, the number and quantities are free again.
        self.ok(self.api('post', f"/purchase-bills/{bill['id']}/submit"))
        self.ok(self.api('post', f"/purchase-bills/{bill['id']}/cancel"))
        self.ok(self.api('post', '/purchase-bills', self.bill_body(self.order, ['600', '0'])))

    def test_future_invoice_date_and_unissued_orders_are_refused(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertIn("future", self.refused(self.api('post', '/purchase-bills', self.bill_body(self.order, ['1', '0'], invoice_date=tomorrow))))
        draft = self.draft()
        self.refused(self.api('post', '/purchase-bills', self.bill_body(draft, ['1', '0'])))

    def test_draft_bills_can_be_edited_and_deleted(self):
        bill = self.ok(self.api('post', '/purchase-bills', self.bill_body(self.order, ['100', '0'])))
        bill = self.ok(self.api('put', f"/purchase-bills/{bill['id']}", self.bill_body(self.order, ['150', '0'], invoice='INV-101A')))
        self.assertEqual((bill['supplier_invoice_number'], Decimal(bill['lines'][0]['quantity'])), ('INV-101A', Decimal('150')))
        self.ok(self.api('delete', f"/purchase-bills/{bill['id']}"), 204)
        self.assertFalse(PurchaseBill.objects.exists())


class SupplierViewTests(PurchaseTestCase):
    def test_the_supplier_sees_issued_orders_read_only(self):
        draft = self.draft()
        order = self.issued()
        self.login(self.supplier_admin.user)
        received = self.ok(self.api('get', '/sales/orders-received'))
        self.assertEqual([o['number'] for o in received], [order['number']])  # not the draft
        self.assertEqual(received[0]['buyer']['name'], 'Vaigai')
        self.assertEqual(len(received[0]['lines']), 2)
        # It's the buyer's document: the supplier can't open or change it as a purchase.
        self.refused(self.api('get', f"/purchase-orders/{order['id']}"), 404)
        self.refused(self.api('post', f"/purchase-orders/{draft['id']}/submit"), 404)
        self.assertEqual(PurchaseOrder.objects.filter(supplier=self.supplier).count(), 2)
