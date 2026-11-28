"""Regression tests for the code review of 27 Nov 2026: deleting items and
warehouses in use, cancelling a PO the supplier accepted, deleting a
quotation revision, and TDS deposits and Form 26Q challans."""
from decimal import Decimal

from django.utils import timezone

from inventory import services as inventory
from inventory.models import Warehouse
from inventory.tests import InventoryApiTestCase
from purchase.models import PurchaseOrder
from purchase.tests import PurchaseTestCase
from quotations.models import Quotation
from quotations.tests import QuotationTestCase
from sales.models import SalesOrder
from sales.tests import SalesTestCase
from tds import services as tds
from tds.tests import TdsTestCase

TODAY = timezone.localdate()


class DeletingThingsInUseTests(PurchaseTestCase):
    def test_item_and_warehouse_on_a_draft_order(self):
        self.draft()  # bolts and bar to the Main store; no stock moved
        self.assertIn("Mark it inactive", self.refused(self.api('delete', f"/items/{self.bolt.pk}")))
        self.assertIn("Mark it inactive", self.refused(self.api('delete', f"/warehouses/{self.store.pk}")))


class OnlyStoreTests(InventoryApiTestCase):
    def test_the_only_store_can_go_even_with_system_locations(self):
        store = Warehouse.objects.create(company=self.company, name="Main store", is_default=True)
        inventory.system_location(self.company, Warehouse.Kind.QC_HOLD)
        self.ok(self.api('delete', f"/warehouses/{store.pk}"), 204)


class AcceptedOrderTests(SalesTestCase):
    def test_buyer_cant_cancel_once_the_supplier_has_accepted(self):
        po, order = self.accepted()
        self.login(self.admin.user)
        self.assertIn("Ask them to cancel their sales order", self.refused(self.api('post', f"/purchase-orders/{po['id']}/cancel")))
        # The supplier cancels; the buyer's order shows declined and can then be cancelled.
        self.as_supplier()
        self.ok(self.api('post', f"/sales-orders/{order['id']}/cancel", {'reason': 'Customer asked'}))
        self.login(self.admin.user)
        self.assertEqual(self.ok(self.api('post', f"/purchase-orders/{po['id']}/cancel"))['status'], 'cancelled')

    def test_no_dispatch_against_a_cancelled_purchase_order(self):
        po, order = self.accepted()
        PurchaseOrder.objects.filter(pk=po['id']).update(status=PurchaseOrder.Status.CANCELLED)
        self.assertIn("cancelled their purchase order", self.refused(self.send(order, ['10', '0'])))
        self.assertEqual(SalesOrder.objects.get(pk=order['id']).dispatches.count(), 0)


class DeletedRevisionTests(QuotationTestCase):
    def test_a_declined_quotation_stays_declined(self):
        q = self.sent()
        self.ok(self.api('post', f"/quotations/{q['id']}/response", {'accepted': False, 'note': 'Price'}))
        r2 = self.ok(self.api('post', f"/quotations/{q['id']}/revise"))
        self.ok(self.api('delete', f"/quotations/{r2['id']}"), 204)
        self.assertEqual(Quotation.objects.get(pk=q['id']).status, 'declined')


class TdsDepositTests(TdsTestCase):
    def setUp(self):
        super().setUp()
        self.ok(self.set_section('194C'))

    def deposit(self, amount, serial, on, period=None):
        return self.api('post', '/tds/deposits', {
            'period': (period or TODAY).isoformat(), 'section': '194C', 'amount': str(amount), 'deposited_on': on.isoformat(),
            'bsr_code': '0510308', 'challan_serial': serial})

    def test_period_given_as_any_day_of_the_month(self):
        self.ok(self.deposit(800, '1', TODAY.replace(day=1), period=TODAY), 201)

    def test_26q_matches_deductions_to_challans_by_amount(self):
        for invoice in ('K-1', 'K-2', 'K-3'):
            self.bill_for(40000, invoice)  # ₹800 TDS each
        self.ok(self.deposit(1600, '1', TODAY.replace(day=1)), 201)
        self.ok(self.deposit(800, '2', TODAY), 201)
        q = (TODAY.month - 4) % 12 // 3 + 1
        rows = tds.form_26q_rows(self.company, f"{TODAY.year if TODAY.month >= 4 else TODAY.year - 1}-Q{q}")
        self.assertEqual([(r['bill_reference'][:3], r['challan_serial']) for r in rows],
                         [('K-1', '00001'), ('K-2', '00001'), ('K-3', '00002')])
        self.assertEqual(sum((r['tds_deducted'] for r in rows), Decimal('0')), Decimal('2400'))


class CostVisibilityTests(TdsTestCase):
    """Security review, 28 Nov 2026: conversion costs only for people with costing.manage."""

    def test_machine_operator_sees_materials_but_not_conversion_costs(self):
        from inventory.models import Item, StockEntry
        from organizations.tests import add_member
        from plans import erp

        self.company.erp_plan = erp.GROWTH
        self.company.save(update_fields=['erp_plan'])
        gear = Item.objects.create(company=self.company, code='GEAR', name='Gear', kind='finished_good', unit='NOS', hsn_code='8483')
        StockEntry.objects.create(company=self.company, item=self.bar, warehouse=self.store, kind='opening', quantity=100, rate=80,
                                  entry_date=TODAY)
        self.ok(self.api('put', '/production/cost-settings', {'labour_hour_rate': '200', 'overhead_percent': '50'}))
        machine = self.ok(self.api('post', '/machines', {'code': 'CNC', 'name': 'Lathe', 'hour_rate': '500'}))
        bom = self.ok(self.api('post', '/boms', {'item_id': gear.pk, 'machine_id': machine['id'], 'machine_hours': '0.2', 'labour_hours': '0.25',
                                                 'components': [{'item_id': self.bar.pk, 'quantity': '2'}]}))
        self.assertEqual(Decimal(bom['standard_cost']['total']), Decimal('381'))  # 2 kg bar at 78 + 50 + 100 + 75
        operator = add_member(self.company, 'op@vaigai.example', 'machine_operator')
        self.login(operator.user)
        entry = self.ok(self.api('post', '/production/entries', {'bom_id': bom['id'], 'quantity_produced': '1', 'source_warehouse_id': self.store.pk}))
        self.assertEqual((entry['labour_cost'], entry['machine_cost'], entry['total_cost']), (None, None, None))
        seen = self.ok(self.api('get', f"/boms/{bom['id']}"))['standard_cost']
        self.assertEqual((Decimal(seen['material']), seen['labour'], seen['machine'], seen['total']), (Decimal('156'), None, None, None))


class TallyQuoteTests(TdsTestCase):
    def test_quotes_in_names_are_escaped_in_attributes(self):
        from gstreturns.tally import _x
        self.assertEqual(_x('Acme" ACTION="Delete'), 'Acme&quot; ACTION=&quot;Delete')
