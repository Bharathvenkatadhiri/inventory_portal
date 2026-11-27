"""QC: goods under inspection, inspections, and where stock ends up."""
from decimal import Decimal

from django.utils import timezone

from inventory.models import Item, StockEntry, Warehouse
from organizations.tests import add_member
from plans import erp
from purchase.tests import PurchaseTestCase

from .models import InspectionLot


class QcTestCase(PurchaseTestCase):
    """Vaigai on Growth (QC included) buys bolts that need inspection."""
    plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        Item.objects.filter(pk=self.bolt.pk).update(inspection_required=True)
        self.order = self.issued()

    def receive_bolts(self, qty='500'):
        return self.ok(self.receive(self.order, [qty, '0']))

    def stock_in(self, item, kind=None, warehouse=None):
        entries = StockEntry.objects.filter(item=item)
        if kind:
            entries = entries.filter(warehouse__kind=kind)
        if warehouse:
            entries = entries.filter(warehouse=warehouse)
        return sum((e.quantity for e in entries), Decimal('0'))

    def lot(self):
        return self.ok(self.api('get', '/qc/lots'))[0]

    def inspect(self, lot, accepted='0', rejected='0', remarks=''):
        return self.api('post', f"/qc/lots/{lot['id']}/inspections", {'accepted': accepted, 'rejected': rejected, 'remarks': remarks})


class ReceiptInspectionTests(QcTestCase):
    def test_received_goods_wait_under_inspection(self):
        self.receive_bolts()
        self.assertEqual(self.stock_in(self.bolt, warehouse=self.store), 0)
        self.assertEqual(self.stock_in(self.bolt, kind='qc_hold'), Decimal('500'))
        lot = self.lot()
        self.assertEqual((lot['status'], Decimal(lot['pending_quantity']), lot['destination'], lot['supplier']),
                         ('pending', Decimal('500'), 'Main store', 'Kaveri'))
        self.assertTrue(lot['number'].startswith('QC-'))
        # The system locations aren't offered as warehouses, but show with include_system.
        self.assertEqual([w['name'] for w in self.ok(self.api('get', '/warehouses'))['warehouses']], ['Main store'])
        names = {w['name']: w['kind'] for w in self.ok(self.api('get', '/warehouses?include_system=true'))['warehouses']}
        self.assertEqual(names['Under inspection'], 'qc_hold')
        # Bars don't need inspection: straight to the store.
        self.ok(self.receive(self.order, ['0', '50']))
        self.assertEqual(self.stock_in(self.bar, warehouse=self.store), Decimal('50'))

    def test_partial_then_full_inspection(self):
        self.receive_bolts()
        lot = self.lot()
        self.assertIn("what was wrong", self.refused(self.inspect(lot, '400', '100')))
        lot = self.ok(self.inspect(lot, '300', '50', 'Thread damage on 50'))
        self.assertEqual((lot['status'], Decimal(lot['pending_quantity'])), ('partly', Decimal('150')))
        self.assertEqual(self.stock_in(self.bolt, warehouse=self.store), Decimal('300'))
        self.assertEqual(self.stock_in(self.bolt, kind='rejected'), Decimal('50'))
        self.assertIn("Only 150", self.refused(self.inspect(lot, '151')))
        lot = self.ok(self.inspect(lot, '150'))
        self.assertEqual(lot['status'], 'done')
        self.assertEqual(len(lot['inspections']), 2)
        self.assertEqual(self.stock_in(self.bolt, kind='qc_hold'), 0)
        self.assertEqual(self.ok(self.api('get', '/qc/lots')), [])  # nothing open
        self.refused(self.inspect(lot, '1'))

    def test_rejected_goods_can_go_back_to_the_supplier(self):
        self.receive_bolts()
        self.ok(self.inspect(self.lot(), '450', '50', 'Wrong grade'))
        po = self.ok(self.api('get', f"/purchase-orders/{self.order['id']}"))
        bill = self.ok(self.api('post', '/purchase-bills', self.bill_body(po, ['500', '0'])))
        bill = self.ok(self.api('post', f"/purchase-bills/{bill['id']}/submit"))
        rejected = Warehouse.objects.get(company=self.company, kind='rejected')
        line = self.ok(self.api('get', f"/notes/room?side=purchase&document_id={bill['id']}"))['lines'][0]['line_id']
        note = self.ok(self.api('post', '/notes', {
            'side': 'purchase', 'kind': 'credit', 'reason': 'goods_returned', 'document_id': bill['id'], 'warehouse_id': rejected.pk,
            'party_note_number': 'KC-CN-9', 'lines': [{'line_id': line, 'quantity': '50'}]}))
        self.ok(self.api('post', f"/notes/{note['id']}/post"))
        self.assertEqual(self.stock_in(self.bolt, kind='rejected'), 0)

    def test_starter_has_no_qc(self):
        from accounts.models import Company
        Company.objects.filter(pk=self.company.pk).update(erp_plan=erp.STARTER)
        self.receive_bolts()
        self.assertEqual(self.stock_in(self.bolt, warehouse=self.store), Decimal('500'))
        self.assertFalse(InspectionLot.objects.exists())


class SystemLocationTests(QcTestCase):
    def test_system_locations_are_fixed_and_dont_count_toward_the_plan(self):
        self.receive_bolts()
        hold = Warehouse.objects.get(company=self.company, kind='qc_hold')
        self.refused(self.api('put', f'/warehouses/{hold.pk}', {'name': 'Mine'}))
        self.refused(self.api('delete', f'/warehouses/{hold.pk}'))
        self.ok(self.api('post', '/warehouses', {'name': 'Second'}))
        self.ok(self.api('post', '/warehouses', {'name': 'Third'}))  # Growth: 3 stores, the QC location not counted
        # Can't receive into, or open stock in, a system location.
        self.refused(self.receive(self.order, ['1', '0'], warehouse_id=hold.pk))
        self.refused(self.api('put', '/stock/opening', {'item_id': self.bar.pk, 'warehouse_id': hold.pk, 'quantity': '1'}))


class ProductionInspectionTests(QcTestCase):
    def setUp(self):
        super().setUp()
        self.gear = Item.objects.create(company=self.company, code="GEAR", name="Gear", kind="finished_good", unit="NOS",
                                        hsn_code="8483", inspection_required=True)
        StockEntry.objects.create(company=self.company, item=self.bar, warehouse=self.store, kind='opening', quantity=100,
                                  rate=Decimal('80'), entry_date=timezone.localdate())
        self.bom = self.ok(self.api('post', '/boms', {'item_id': self.gear.pk, 'components': [{'item_id': self.bar.pk, 'quantity': '2'}]}))

    def produce(self, qty):
        return self.ok(self.api('post', '/production/entries', {'bom_id': self.bom['id'], 'quantity_produced': qty,
                                                                 'source_warehouse_id': self.store.pk}))

    def test_production_output_is_inspected_and_cancel_rules(self):
        entry = self.produce('10')
        self.assertEqual(self.stock_in(self.gear, kind='qc_hold'), Decimal('10'))
        lot = next(l for l in self.ok(self.api('get', '/qc/lots')) if l['source'] == 'production')
        self.assertEqual(lot['reference'], entry['number'])
        # Not inspected yet: cancelling takes it back out from under inspection.
        self.ok(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'Wrong BOM'}))
        self.assertEqual(self.stock_in(self.gear), 0)
        self.assertEqual(InspectionLot.objects.get(production_entry_id=entry['id']).status, 'cancelled')
        entry = self.produce('5')
        lot = next(l for l in self.ok(self.api('get', '/qc/lots')) if l['reference'] == entry['number'])
        self.ok(self.inspect(lot, '5'))
        self.assertIn("already inspected", self.refused(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'x'})))


class AccessTests(QcTestCase):
    def test_qc_inspector_inspects_and_others_cant(self):
        self.receive_bolts()
        lot = self.lot()
        inspector = add_member(self.company, "qc@vaigai.example", "qc_inspector").user
        self.login(inspector)
        self.assertTrue(self.lot()['can_inspect'])
        self.ok(self.inspect(lot, '500'))
        operator = add_member(self.company, "op@vaigai.example", "machine_operator").user
        self.login(operator)
        self.refused(self.api('get', '/qc/lots'), 403)
