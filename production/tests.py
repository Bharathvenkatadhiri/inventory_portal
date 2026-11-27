"""Bills of materials and production entries through /api/v1."""
from decimal import Decimal

from django.utils import timezone

from inventory.models import Item, StockEntry, Warehouse
from inventory.tests import InventoryApiTestCase
from marketplace.documents import financial_year
from organizations.tests import add_member, make_organization
from plans import erp

FY = financial_year(timezone.localdate())


class ProductionTestCase(InventoryApiTestCase):
    """Gear shafts made from EN8 bar (2.5 kg each) and cutting oil (0.1 L each).
    Stock: 100 kg of bar at ₹80 and 10 L of oil at ₹200 in the Main store."""
    plan = erp.STARTER

    def setUp(self):
        super().setUp()
        self.main = Warehouse.objects.create(company=self.company, name="Main store", is_default=True)
        self.gear = Item.objects.create(company=self.company, code="GEAR", name="Gear shaft", kind="finished_good", unit="NOS", hsn_code="8483")
        self.bar = Item.objects.create(company=self.company, code="BAR", name="EN8 bar", kind="raw_material", unit="KGS",
                                       hsn_code="7214", purchase_price=80)
        self.oil = Item.objects.create(company=self.company, code="OIL", name="Cutting oil", kind="consumable", unit="LTR",
                                       hsn_code="3403", purchase_price=200)
        for item, qty, rate in ((self.bar, 100, 80), (self.oil, 10, 200)):
            StockEntry.objects.create(company=self.company, item=item, warehouse=self.main, kind='opening', quantity=qty,
                                      rate=Decimal(rate), entry_date=timezone.localdate())
        self.bom = self.ok(self.api('post', '/boms', self.bom_body()))

    def bom_body(self, **extra):
        return {'item_id': self.gear.pk, 'name': 'Standard', 'output_quantity': '1',
                'components': [{'item_id': self.bar.pk, 'quantity': '2.5'}, {'item_id': self.oil.pk, 'quantity': '0.1'}], **extra}

    def record(self, good, rejected=0, **extra):
        return self.api('post', '/production/entries', {'bom_id': self.bom['id'], 'quantity_produced': str(good),
                                                         'quantity_rejected': str(rejected), 'source_warehouse_id': self.main.pk, **extra})

    def stock(self, item):
        return sum(e.quantity for e in StockEntry.objects.filter(item=item, warehouse=self.main))


class BomTests(ProductionTestCase):
    def test_first_bom_is_the_default_and_a_new_default_moves_it(self):
        self.assertTrue(self.bom['is_default'])
        self.assertEqual(Decimal(self.bom['estimated_unit_cost']), Decimal('220'))  # 2.5 x 80 + 0.1 x 200
        second = self.ok(self.api('post', '/boms', self.bom_body(name='Rev B')))
        self.assertFalse(second['is_default'])
        second = self.ok(self.api('put', f"/boms/{second['id']}", self.bom_body(name='Rev B', is_default=True)))
        self.assertTrue(second['is_default'])
        self.assertFalse(self.ok(self.api('get', f"/boms/{self.bom['id']}"))['is_default'])
        self.assertIn("already has a BOM called", self.refused(self.api('post', '/boms', self.bom_body(name='Standard'))))

    def test_what_a_bom_can_make_and_contain(self):
        self.assertIn("finished good", self.refused(self.api('post', '/boms', self.bom_body(item_id=self.bar.pk, name='x'))))
        self.assertIn("itself", self.refused(self.api('post', '/boms', self.bom_body(
            name='x', components=[{'item_id': self.gear.pk, 'quantity': '1'}]))))
        service = Item.objects.create(company=self.company, code="SVC", name="Heat treatment", kind="service", hsn_code="998898")
        self.assertIn("service", self.refused(self.api('post', '/boms', self.bom_body(
            name='x', components=[{'item_id': service.pk, 'quantity': '1'}]))))
        self.assertIn("twice", self.refused(self.api('post', '/boms', self.bom_body(
            name='x', components=[{'item_id': self.bar.pk, 'quantity': '1'}, {'item_id': self.bar.pk, 'quantity': '2'}]))))
        self.assertIn("at least one", self.refused(self.api('post', '/boms', self.bom_body(name='x', components=[]))))


class EntryTests(ProductionTestCase):
    def test_plan_shows_needs_and_shortfall(self):
        lines = self.ok(self.api('get', f"/production/plan?bom_id={self.bom['id']}&quantity=50&warehouse_id={self.main.pk}"))
        bar = next(l for l in lines if l['item_code'] == 'BAR')
        self.assertEqual((Decimal(bar['planned']), Decimal(bar['on_hand']), Decimal(bar['short'])), (Decimal('125'), Decimal('100'), Decimal('25')))

    def test_recording_moves_stock_and_costs_the_good_units(self):
        entry = self.ok(self.record(9, 1, shift='A'))
        self.assertEqual(entry['number'], f"PE-{FY}-0001")
        self.assertEqual(self.stock(self.bar), Decimal('75'))   # 10 made x 2.5 kg
        self.assertEqual(self.stock(self.oil), Decimal('9'))    # 10 x 0.1 L
        self.assertEqual(self.stock(self.gear), Decimal('9'))   # only the good ones
        self.assertEqual(Decimal(entry['material_cost']), Decimal('2200'))  # 25 x 80 + 1 x 200
        self.assertEqual(Decimal(entry['unit_cost']), Decimal('244.44'))   # the rejected one's materials carried by the good 9
        receipt = StockEntry.objects.get(kind='production_receipt')
        self.assertEqual((receipt.rate, receipt.reference), (Decimal('244.44'), entry['number']))

    def test_cant_use_more_than_is_in_stock(self):
        self.assertIn("needs 125", self.refused(self.record(50)))
        self.assertEqual(self.stock(self.bar), Decimal('100'))
        self.assertIn("how many", self.refused(self.record(0, 0)))

    def test_actual_consumption_can_differ_from_the_bom(self):
        entry = self.ok(self.record(10, consumption=[{'item_id': self.bar.pk, 'quantity': '26'}]))
        bar = next(c for c in entry['consumption'] if c['item_code'] == 'BAR')
        self.assertEqual((Decimal(bar['planned_quantity']), Decimal(bar['quantity'])), (Decimal('25'), Decimal('26')))
        self.assertEqual(self.stock(self.bar), Decimal('74'))
        other = Item.objects.create(company=self.company, code="X", name="Other", kind="raw_material")
        self.assertIn("this BOM's components", self.refused(self.record(1, consumption=[{'item_id': other.pk, 'quantity': '1'}])))

    def test_cancel_reverses_stock_unless_the_output_is_gone(self):
        entry = self.ok(self.record(10))
        self.assertIn("why", self.refused(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': ''})))
        self.ok(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'Wrong BOM used'}))
        self.assertEqual((self.stock(self.bar), self.stock(self.gear)), (Decimal('100'), Decimal('0')))
        entry = self.ok(self.record(4))
        StockEntry.objects.create(company=self.company, item=self.gear, warehouse=self.main, kind='dispatch', quantity=-3,
                                  rate=Decimal('220'), entry_date=timezone.localdate())
        self.assertIn("can't be undone", self.refused(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'x'})))

    def test_bom_with_production_cant_be_deleted_and_inactive_cant_be_used(self):
        self.ok(self.record(1))
        self.assertIn("inactive instead", self.refused(self.api('delete', f"/boms/{self.bom['id']}")))
        self.ok(self.api('put', f"/boms/{self.bom['id']}", self.bom_body(is_active=False)))
        self.assertIn("inactive", self.refused(self.record(1)))


class AccessTests(ProductionTestCase):
    def test_machine_operator_records_but_doesnt_manage(self):
        operator = add_member(self.company, "op@vaigai.example", "machine_operator").user
        self.login(operator)
        entry = self.ok(self.record(2))
        self.assertEqual(len(self.ok(self.api('get', '/production/entries'))), 1)
        self.ok(self.api('get', '/boms'))
        self.refused(self.api('post', '/boms', self.bom_body(name='Mine')), 403)
        self.refused(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'x'}), 403)
        keeper = add_member(self.company, "keeper@vaigai.example", "store_keeper").user
        self.login(keeper)
        self.refused(self.api('get', '/production/entries'), 403)

    def test_other_companies_cant_reach_boms_or_entries(self):
        entry = self.ok(self.record(1))
        other, other_admin = make_organization("Kaveri", "33BBBBB0000B1Z5", "admin@kaveri.example")
        self.login(other_admin.user)
        self.assertEqual(self.ok(self.api('get', '/boms')), [])
        self.refused(self.api('get', f"/boms/{self.bom['id']}"), 404)
        self.refused(self.api('post', f"/production/entries/{entry['id']}/cancel", {'reason': 'x'}), 404)
