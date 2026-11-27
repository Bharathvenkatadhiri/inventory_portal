"""Production costing and scrap through /api/v1: rates, machines, routing,
costed entries, scrap into stock, cost sheets and the report."""
from decimal import Decimal

from django.utils import timezone

from inventory.models import Item, StockEntry
from organizations.tests import add_member
from plans import erp

from .models import WorkOrder
from .tests import ProductionTestCase

TODAY = timezone.localdate()


class CostingTestCase(ProductionTestCase):
    """Growth. Labour ₹200/h, overheads 50%, CNC-1 at ₹500/h. A gear shaft (2.5 kg bar at ₹80 + 0.1 L oil
    at ₹200 = ₹220 of material) takes 0.25 labour hours and 0.2 hours on CNC-1, and leaves 0.3 kg of
    turnings that sell at ₹20/kg. Standard: 220 + 50 + 100 + 75 - 6 = ₹439 a shaft."""
    plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        self.turnings = Item.objects.create(company=self.company, code="SCR-TURN", name="MS turnings", kind="scrap",
                                            unit="KGS", hsn_code="7204", sale_price=20)
        self.ok(self.api('put', '/production/cost-settings', {'labour_hour_rate': '200', 'overhead_percent': '50'}))
        self.cnc = self.ok(self.api('post', '/machines', {'code': 'cnc-1', 'name': 'CNC lathe', 'hour_rate': '500'}))
        self.bom = self.ok(self.api('put', f"/boms/{self.bom['id']}", self.routed()))

    def routed(self, **extra):
        return self.bom_body(machine_id=self.cnc['id'], machine_hours='0.2', labour_hours='0.25',
                             scrap_item_id=self.turnings.pk, scrap_quantity='0.3', **extra)


class RoutingTests(CostingTestCase):
    def test_standard_cost_of_a_unit(self):
        self.assertEqual(self.cnc['code'], 'CNC-1')
        std = {k: Decimal(v) for k, v in self.bom['standard_cost'].items()}
        self.assertEqual(std, {'material': Decimal('220'), 'labour': Decimal('50'), 'machine': Decimal('100'),
                               'overhead': Decimal('75'), 'scrap': Decimal('6'), 'total': Decimal('439')})
        self.assertEqual((self.bom['machine_name'], self.bom['scrap_item_name']), ('CNC-1 CNC lathe', 'MS turnings'))

    def test_routing_checks(self):
        self.assertIn("which machine", self.refused(self.api('put', f"/boms/{self.bom['id']}", self.bom_body(machine_hours='1'))))
        self.assertIn("which item the scrap", self.refused(self.api('put', f"/boms/{self.bom['id']}", self.bom_body(scrap_quantity='1'))))
        self.assertIn("already has the code", self.refused(self.api('post', '/machines', {'code': 'CNC-1', 'name': 'Another'})))
        self.assertIn("can't be negative", self.refused(self.api('put', '/production/cost-settings',
                                                                 {'labour_hour_rate': '-1', 'overhead_percent': '0'})))


class CostedEntryTests(CostingTestCase):
    def test_blank_hours_and_scrap_come_from_the_bom(self):
        entry = self.ok(self.record(10))
        costs = {k: Decimal(entry[k]) for k in ('material_cost', 'labour_cost', 'machine_cost', 'overhead_cost', 'scrap_value', 'total_cost', 'unit_cost')}
        self.assertEqual(costs, {'material_cost': Decimal('2200'), 'labour_cost': Decimal('500'), 'machine_cost': Decimal('1000'),
                                 'overhead_cost': Decimal('750'), 'scrap_value': Decimal('60'), 'total_cost': Decimal('4390'),
                                 'unit_cost': Decimal('439')})
        self.assertEqual((Decimal(entry['labour_hours']), Decimal(entry['machine_hours']), entry['machine_name']),
                         (Decimal('2.5'), Decimal('2'), 'CNC-1 CNC lathe'))
        # The shafts go into stock at the full cost; the turnings at what they fetch.
        made = StockEntry.objects.get(item=self.gear, kind='production_receipt')
        self.assertEqual(made.rate, Decimal('439'))
        scrap = StockEntry.objects.get(item=self.turnings)
        self.assertEqual((scrap.kind, scrap.quantity, scrap.rate, scrap.warehouse), ('scrap', Decimal('3'), Decimal('20'), self.main))

    def test_actual_hours_and_scrap(self):
        entry = self.ok(self.record(9, 1, labour_hours='3', machine_hours='2.5',
                                    scrap=[{'item_id': self.turnings.pk, 'quantity': '4', 'rate': '18'}]))
        # 2200 + 600 + 1250 + 925 - 72 = 4903 over 9 good shafts.
        self.assertEqual((Decimal(entry['total_cost']), Decimal(entry['unit_cost'])), (Decimal('4903'), Decimal('544.78')))
        self.assertEqual([(s['item_code'], Decimal(s['quantity']), Decimal(s['value'])) for s in entry['scrap']],
                         [('SCR-TURN', Decimal('4'), Decimal('72'))])
        # No scrap this time, and no machine.
        entry = self.ok(self.record(1, scrap=[], machine_hours='0'))
        self.assertEqual((Decimal(entry['scrap_value']), Decimal(entry['machine_cost'])), (Decimal('0'), Decimal('0')))

    def test_scrap_checks(self):
        self.assertIn("record rejects as rejected", self.refused(self.record(1, scrap=[{'item_id': self.gear.pk, 'quantity': '1'}])))
        self.assertIn("worth more than everything", self.refused(self.record(1, scrap=[{'item_id': self.turnings.pk, 'quantity': '100', 'rate': '20'}])))

    def test_cancel_takes_the_scrap_back_unless_it_has_gone(self):
        entry = self.ok(self.record(10))
        self.ok(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'Wrong BOM'}))
        self.assertEqual(sum(e.quantity for e in StockEntry.objects.filter(item=self.turnings)), 0)
        entry = self.ok(self.record(10))
        StockEntry.objects.create(company=self.company, item=self.turnings, warehouse=self.main, kind='dispatch', quantity=-2,
                                  rate=20, entry_date=TODAY)
        self.assertIn("scrap from this entry has been sold", self.refused(
            self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'x'})))


class CostSheetTests(CostingTestCase):
    def test_work_order_standard_against_actual_and_the_report(self):
        wo = self.ok(self.api('post', '/work-orders', {'item_id': self.gear.pk, 'quantity': '20', 'source_warehouse_id': self.main.pk}))
        self.ok(self.api('post', f"/work-orders/{wo['id']}/release", {'make_subassemblies': False}))
        self.ok(self.api('post', '/production/entries', {'work_order_id': wo['id'], 'quantity_produced': '10'}))
        self.ok(self.api('post', '/production/entries', {'work_order_id': wo['id'], 'quantity_produced': '9', 'quantity_rejected': '1',
                                                         'labour_hours': '3', 'machine_hours': '2.5',
                                                         'scrap': [{'item_id': self.turnings.pk, 'quantity': '4', 'rate': '18'}]}))
        sheet = self.ok(self.api('get', f"/work-orders/{wo['id']}/costs"))
        rows = {r['part']: (Decimal(r['standard']), Decimal(r['actual']), Decimal(r['variance'])) for r in sheet['rows']}
        # 19 good shafts at standard (439) = 8341; actual 4390 + 4903 = 9293.
        self.assertEqual(rows['total'], (Decimal('8341'), Decimal('9293'), Decimal('952')))
        self.assertEqual(rows['material'], (Decimal('4180'), Decimal('4400'), Decimal('220')))  # the rejected one's material
        self.assertEqual(rows['scrap'], (Decimal('114'), Decimal('132'), Decimal('18')))
        self.assertEqual((Decimal(sheet['yield_percent']), Decimal(sheet['actual_unit_cost'])), (Decimal('95'), Decimal('489.11')))
        wo = self.ok(self.api('get', f"/work-orders/{wo['id']}"))
        self.assertEqual((Decimal(wo['planned_unit_cost']), Decimal(wo['actual_unit_cost'])), (Decimal('439'), Decimal('489.11')))

        [row] = self.ok(self.api('get', f"/production/costs?start={TODAY.isoformat()}&end={TODAY.isoformat()}"))
        self.assertEqual((row['item_code'], row['entries'], Decimal(row['scrap_quantity'])), ('GEAR', 2, Decimal('7')))
        self.assertIn("before the start", self.refused(self.api('get', f"/production/costs?start={TODAY.isoformat()}&end=2000-01-01")))


class PlanAndAccessTests(ProductionTestCase):
    plan = erp.STARTER

    def test_starter_costs_materials_only(self):
        self.assertIn("Growth and Pro", self.refused(self.record(1, labour_hours='1')))
        entry = self.ok(self.record(4))
        self.assertEqual((Decimal(entry['unit_cost']), Decimal(entry['labour_cost'])), (Decimal('220'), Decimal('0')))
        self.assertEqual(self.api('get', '/production/cost-settings').status_code, 403)
        self.assertEqual(Decimal(self.ok(self.api('get', f"/boms/{self.bom['id']}"))['standard_cost']['total']), Decimal('220'))

    def test_only_costing_roles_see_rates(self):
        self.company.erp_plan = erp.GROWTH
        self.company.save(update_fields=['erp_plan'])
        self.ok(self.api('post', '/machines', {'code': 'VMC', 'name': 'VMC', 'hour_rate': '650'}))
        operator = add_member(self.company, 'operator@vaigai.example', 'machine_operator')
        self.login(operator.user)
        self.assertEqual(self.api('get', '/production/cost-settings').status_code, 403)
        [machine] = self.ok(self.api('get', '/machines'))
        self.assertIsNone(machine['hour_rate'])
        planner = add_member(self.company, 'planner@vaigai.example', 'production_planner')
        self.login(planner.user)
        self.assertEqual(Decimal(self.ok(self.api('get', '/machines'))[0]['hour_rate']), Decimal('650'))
