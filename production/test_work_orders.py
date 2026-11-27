"""Multi-level BOMs and work orders."""
from decimal import Decimal

from django.utils import timezone

from accounts.models import Company
from inventory.models import Item, StockEntry
from organizations.tests import add_member
from plans import erp

from .models import WorkOrder
from .tests import ProductionTestCase


class MultiLevelTestCase(ProductionTestCase):
    """Growth. A gear assembly = 1 gear shaft (made: 2.5 kg bar + 0.1 L oil) + 2 bearings (bought at ₹150).
    Stock: 100 kg bar, 10 L oil; no shafts, no bearings."""
    plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        self.bearing = Item.objects.create(company=self.company, code="BRG", name="Bearing", kind="component", unit="NOS",
                                           hsn_code="8482", purchase_price=150)
        self.assembly = Item.objects.create(company=self.company, code="ASSY", name="Gear assembly", kind="finished_good",
                                            unit="NOS", hsn_code="8483")
        self.assy_bom = self.ok(self.api('post', '/boms', {
            'item_id': self.assembly.pk, 'components': [{'item_id': self.gear.pk, 'quantity': '1'}, {'item_id': self.bearing.pk, 'quantity': '2'}]}))

    def work_order(self, qty='4', **extra):
        return self.api('post', '/work-orders', {'item_id': self.assembly.pk, 'quantity': qty, 'source_warehouse_id': self.main.pk, **extra})

    def record_against(self, wo, good, rejected='0'):
        return self.api('post', '/production/entries', {'work_order_id': wo['id'], 'quantity_produced': good, 'quantity_rejected': rejected})


class MultiLevelBomTests(MultiLevelTestCase):
    def test_explode_and_rolled_cost(self):
        rows = self.ok(self.api('get', f"/boms/{self.assy_bom['id']}/explode?quantity=4&warehouse_id={self.main.pk}"))
        tree = [(r['level'], r['item_code'], Decimal(r['quantity']), r['made']) for r in rows]
        self.assertEqual(tree, [(1, 'GEAR', Decimal('4'), True), (2, 'BAR', Decimal('10'), False), (2, 'OIL', Decimal('0.4'), False),
                                (1, 'BRG', Decimal('8'), False)])
        self.assertEqual(Decimal(rows[0]['short']), Decimal('4'))  # no shafts in stock
        self.assertEqual(Decimal(self.assy_bom['estimated_unit_cost']), Decimal('520'))  # shaft 2.5x80 + 0.1x200 = 220, + 2 x 150
        self.assertEqual(self.assy_bom['made_in_house'], [self.gear.pk])

    def test_loops_are_refused(self):
        message = self.refused(self.api('put', f"/boms/{self.bom['id']}", self.bom_body(
            components=[{'item_id': self.bar.pk, 'quantity': '2.5'}, {'item_id': self.assembly.pk, 'quantity': '1'}])))
        self.assertIn("loop", message)


class WorkOrderTests(MultiLevelTestCase):
    def test_release_with_subassemblies_then_build_both_levels(self):
        wo = self.ok(self.work_order(due_date=timezone.localdate().isoformat()))
        self.assertEqual((wo['status'], wo['bom_name'], wo['number'][:3]), ('draft', 'Standard', 'WO-'))
        self.assertIn("release it first", self.refused(self.record_against(wo, '1')))
        wo = self.ok(self.api('post', f"/work-orders/{wo['id']}/release", {'make_subassemblies': True}))
        self.assertEqual(wo['status'], 'released')
        [child] = wo['children']
        self.assertEqual((child['item_name'], Decimal(child['quantity']), child['status']), ('Gear shaft', Decimal('4'), 'released'))
        child = self.ok(self.api('get', f"/work-orders/{child['id']}"))
        self.assertEqual(child['parent']['number'], wo['number'])
        # Build the shafts on the child order: bar and oil used, shafts into the parent's materials store.
        self.ok(self.record_against(child, '4'))
        self.assertEqual(self.ok(self.api('get', f"/work-orders/{child['id']}"))['status'], 'completed')
        self.assertEqual(self.stock(self.gear), Decimal('4'))
        # The assembly still needs bearings.
        self.assertIn("needs 4", self.refused(self.record_against(wo, '2')))
        StockEntry.objects.create(company=self.company, item=self.bearing, warehouse=self.main, kind='opening', quantity=8,
                                  rate=Decimal('150'), entry_date=timezone.localdate())
        self.ok(self.record_against(wo, '2'))
        wo = self.ok(self.api('get', f"/work-orders/{wo['id']}"))
        self.assertEqual((wo['status'], Decimal(wo['made']), Decimal(wo['remaining'])), ('in_progress', Decimal('2'), Decimal('2')))
        brg = next(m for m in wo['materials'] if m['item_code'] == 'BRG')
        self.assertEqual((Decimal(brg['planned']), Decimal(brg['used'])), (Decimal('8'), Decimal('4')))
        self.ok(self.record_against(wo, '2'))
        wo = self.ok(self.api('get', f"/work-orders/{wo['id']}"))
        self.assertEqual(wo['status'], 'completed')
        self.assertEqual(Decimal(wo['actual_unit_cost']), Decimal('520'))  # shafts at their cost (220) + 2 bearings

    def test_close_short_and_cancel(self):
        wo = self.ok(self.work_order())
        wo = self.ok(self.api('post', f"/work-orders/{wo['id']}/release", {'make_subassemblies': True}))
        child_id = wo['children'][0]['id']
        self.ok(self.api('post', f"/work-orders/{wo['id']}/cancel", {'reason': 'Customer cancelled'}))
        self.assertEqual(WorkOrder.objects.get(pk=child_id).status, 'cancelled')  # unstarted children go too
        shafts = self.ok(self.api('post', '/work-orders', {'item_id': self.gear.pk, 'quantity': '10', 'source_warehouse_id': self.main.pk}))
        shafts = self.ok(self.api('post', f"/work-orders/{shafts['id']}/release", {}))
        self.assertIn("in progress", self.refused(self.api('post', f"/work-orders/{shafts['id']}/close", {'reason': 'x'})))
        self.ok(self.record_against(shafts, '3'))
        self.assertIn("Close it short", self.refused(self.api('post', f"/work-orders/{shafts['id']}/cancel", {'reason': 'x'})))
        self.assertIn("why", self.refused(self.api('post', f"/work-orders/{shafts['id']}/close", {'reason': ''})))
        closed = self.ok(self.api('post', f"/work-orders/{shafts['id']}/close", {'reason': 'Bar shortage'}))
        self.assertEqual((closed['status'], Decimal(closed['made'])), ('closed', Decimal('3')))
        self.refused(self.record_against(closed, '1'))

    def test_cancelling_an_entry_reopens_its_order(self):
        shafts = self.ok(self.api('post', '/work-orders', {'item_id': self.gear.pk, 'quantity': '2', 'source_warehouse_id': self.main.pk}))
        shafts = self.ok(self.api('post', f"/work-orders/{shafts['id']}/release", {}))
        entry = self.ok(self.record_against(shafts, '2'))
        self.assertEqual(entry['work_order_number'], shafts['number'])
        self.assertEqual(self.ok(self.api('get', f"/work-orders/{shafts['id']}"))['status'], 'completed')
        self.ok(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'Recorded twice'}))
        self.assertEqual(self.ok(self.api('get', f"/work-orders/{shafts['id']}"))['status'], 'released')


class AccessTests(MultiLevelTestCase):
    def test_operator_records_against_orders_but_doesnt_plan_them(self):
        shafts = self.ok(self.api('post', '/work-orders', {'item_id': self.gear.pk, 'quantity': '2', 'source_warehouse_id': self.main.pk}))
        shafts = self.ok(self.api('post', f"/work-orders/{shafts['id']}/release", {}))
        operator = add_member(self.company, "op@vaigai.example", "machine_operator").user
        self.login(operator)
        self.assertEqual(len(self.ok(self.api('get', '/work-orders'))), 1)
        self.ok(self.record_against(shafts, '1'))
        self.refused(self.work_order(), 403)

    def test_work_orders_need_growth(self):
        Company.objects.filter(pk=self.company.pk).update(erp_plan=erp.STARTER)
        self.refused(self.work_order(), 403)
