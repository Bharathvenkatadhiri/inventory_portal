"""Items, warehouses and opening stock through /api/v1 (inventory.api)."""
import json
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from billing.models import OrganizationSubscription
from organizations import catalog
from organizations.tests import GSTIN, PASSWORD, add_member, make_organization
from plans import erp

from .models import Item, StockEntry, Warehouse

OTHER_GSTIN = "33BBBBB0000B1Z5"

BOLT = {'code': 'bolt-m8', 'name': 'Hex bolt M8 x 40', 'kind': 'component', 'unit': 'NOS', 'hsn_code': '7318',
        'gst_rate': '18', 'purchase_price': '4.50', 'reorder_level': '500'}


class InventoryApiTestCase(TestCase):
    plan = erp.STARTER

    def setUp(self):
        cache.clear()
        self.company, self.admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example", plan=self.plan)
        self.login(self.admin.user)

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

    def item(self, **overrides):
        return self.ok(self.api('post', '/items', {**BOLT, **overrides}))

    def warehouse(self, name='Main store', **fields):
        return self.ok(self.api('post', '/warehouses', {'name': name, **fields}))


class ItemTests(InventoryApiTestCase):
    def test_create_list_update_and_delete(self):
        bolt = self.item()
        self.assertEqual(bolt['code'], 'BOLT-M8')  # codes are kept upper-case
        self.assertEqual(Decimal(bolt['gst_rate']), Decimal('18'))
        listed = self.ok(self.api('get', '/items'))
        self.assertEqual([i['code'] for i in listed], ['BOLT-M8'])
        self.assertEqual(Decimal(listed[0]['on_hand']), 0)  # the Org Admin can see stock
        updated = self.ok(self.api('put', f"/items/{bolt['id']}", {**BOLT, 'name': 'Hex bolt M8 x 50'}))
        self.assertEqual(updated['name'], 'Hex bolt M8 x 50')
        self.ok(self.api('delete', f"/items/{bolt['id']}"), 204)
        self.assertFalse(Item.objects.exists())

    def test_search_and_filters(self):
        self.item()
        self.item(code='MS-ROD-25', name='MS round bar 25 mm', kind='raw_material', unit='KGS', hsn_code='7214')
        self.assertEqual(len(self.ok(self.api('get', '/items?q=round'))), 1)
        self.assertEqual(len(self.ok(self.api('get', '/items?q=7214'))), 1)
        self.assertEqual(len(self.ok(self.api('get', '/items?kind=component'))), 1)

    def test_codes_are_unique_per_company_ignoring_case(self):
        self.item()
        self.assertIn("already uses the code", self.refused(self.api('post', '/items', {**BOLT, 'code': 'Bolt-M8'})))
        # Another company can use the same code.
        other, other_admin = make_organization("Kaveri", OTHER_GSTIN, "admin@kaveri.example")
        self.login(other_admin.user)
        self.item()

    def test_hsn_sac_and_gst_rate_are_checked(self):
        self.assertIn("HSN code", self.refused(self.api('post', '/items', {**BOLT, 'hsn_code': '731'})))
        self.assertIn("SAC", self.refused(self.api('post', '/items', {**BOLT, 'code': 'SVC', 'kind': 'service', 'hsn_code': '7318'})))
        self.item(code='MACHINING', name='CNC machining charges', kind='service', hsn_code='998898')
        self.assertIn("GST rate", self.refused(self.api('post', '/items', {**BOLT, 'code': 'X1', 'gst_rate': '15'})))
        self.item(code='X2', gst_rate='40')
        self.item(code='X3', gst_rate='0.25')
        self.assertIn("negative", self.refused(self.api('post', '/items', {**BOLT, 'code': 'X4', 'purchase_price': '-1'})))

    def test_an_item_with_stock_cant_be_deleted_or_become_a_service(self):
        bolt, store = self.item(), self.warehouse()
        self.ok(self.api('put', '/stock/opening', {'item_id': bolt['id'], 'warehouse_id': store['id'], 'quantity': '100'}))
        self.assertIn("inactive instead", self.refused(self.api('delete', f"/items/{bolt['id']}")))
        self.assertIn("service", self.refused(self.api('put', f"/items/{bolt['id']}", {**BOLT, 'kind': 'service', 'hsn_code': ''})))
        self.ok(self.api('put', f"/items/{bolt['id']}", {**BOLT, 'is_active': False}))

    def test_every_member_can_read_items_but_only_some_roles_change_them(self):
        bolt = self.item()
        operator = add_member(self.company, "op@vaigai.example", "machine_operator").user
        self.login(operator)
        listed = self.ok(self.api('get', '/items'))
        self.assertEqual(len(listed), 1)
        self.assertIsNone(listed[0]['on_hand'])  # no stock.view
        self.refused(self.api('post', '/items', {**BOLT, 'code': 'NEW'}), 403)
        self.refused(self.api('put', f"/items/{bolt['id']}", BOLT), 403)
        store = add_member(self.company, "store@vaigai.example", "store").user
        self.login(store)
        self.item(code='NEW')

    def test_another_companys_items_cant_be_reached(self):
        bolt = self.item()
        other, other_admin = make_organization("Kaveri", OTHER_GSTIN, "admin@kaveri.example")
        self.login(other_admin.user)
        self.assertEqual(self.ok(self.api('get', '/items')), [])
        self.refused(self.api('put', f"/items/{bolt['id']}", BOLT), 404)
        self.refused(self.api('delete', f"/items/{bolt['id']}"), 404)

    def test_a_lapsed_subscription_is_read_only(self):
        self.item()
        OrganizationSubscription.objects.filter(company=self.company).update(
            status=OrganizationSubscription.LAPSED, trial_ends_at=timezone.now() - timedelta(days=1))
        self.ok(self.api('get', '/items'))
        self.refused(self.api('post', '/items', {**BOLT, 'code': 'NEW'}), 402)


class WarehouseTests(InventoryApiTestCase):
    plan = erp.GROWTH  # 3 warehouses

    def test_first_is_default_and_default_can_move(self):
        main = self.warehouse()
        self.assertTrue(main['is_default'])
        second = self.warehouse('Finished goods', code='fg')
        self.assertFalse(second['is_default'])
        self.assertEqual(second['code'], 'FG')
        self.ok(self.api('put', f"/warehouses/{second['id']}", {'name': 'Finished goods', 'is_default': True}))
        self.assertEqual(list(Warehouse.objects.filter(is_default=True).values_list('name', flat=True)), ['Finished goods'])

    def test_default_cant_be_unset_or_deactivated_directly(self):
        main = self.warehouse()
        self.warehouse('Second')
        self.assertIn("another warehouse the default", self.refused(self.api('put', f"/warehouses/{main['id']}", {'name': 'Main store', 'is_default': False})))
        self.assertIn("default warehouse", self.refused(self.api('put', f"/warehouses/{main['id']}", {'name': 'Main store', 'is_active': False})))

    def test_plan_limit_counts_active_warehouses(self):
        self.warehouse('A'), self.warehouse('B')
        c = self.warehouse('C')
        message = self.refused(self.api('post', '/warehouses', {'name': 'D'}))
        self.assertIn("Your plan includes 3 warehouses", message)
        self.assertIn("Pro plan allows 10", message)
        listed = self.ok(self.api('get', '/warehouses'))
        self.assertEqual((listed['used'], listed['included']), (3, 3))
        # Deactivating one frees a place; reactivating needs one.
        self.ok(self.api('put', f"/warehouses/{c['id']}", {'name': 'C', 'is_active': False}))
        self.warehouse('D')
        self.refused(self.api('put', f"/warehouses/{c['id']}", {'name': 'C', 'is_active': True}))

    def test_names_are_unique_and_addresses_must_be_the_companys(self):
        self.warehouse()
        self.assertIn("already have", self.refused(self.api('post', '/warehouses', {'name': 'main STORE'})))
        other, _ = make_organization("Kaveri", OTHER_GSTIN, "admin@kaveri.example")
        foreign = other.addresses.first()
        if foreign is None:
            from organizations.models import Address
            foreign = Address.objects.create(company=other, kind='warehouse', line1='Elsewhere')
        self.assertIn("company's addresses", self.refused(self.api('post', '/warehouses', {'name': 'X', 'address_id': foreign.pk})))

    def test_store_keeper_cant_manage_warehouses(self):
        keeper = add_member(self.company, "keeper@vaigai.example", "store_keeper").user
        self.login(keeper)
        self.refused(self.api('post', '/warehouses', {'name': 'X'}), 403)
        self.ok(self.api('get', '/warehouses'))


class StarterWarehouseLimitTests(InventoryApiTestCase):
    def test_starter_has_one(self):
        self.warehouse()
        self.assertIn("Growth plan allows 3", self.refused(self.api('post', '/warehouses', {'name': 'Second'})))


class OpeningStockTests(InventoryApiTestCase):
    plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        self.bolt = self.item(reorder_level='300')
        self.main = self.warehouse()
        self.fg = self.warehouse('Finished goods')

    def opening(self, quantity, warehouse=None, rate='4.50', **extra):
        return self.api('put', '/stock/opening', {'item_id': self.bolt['id'], 'warehouse_id': (warehouse or self.main)['id'],
                                                  'quantity': quantity, 'rate': rate, **extra})

    def test_set_replace_and_remove(self):
        self.ok(self.opening('100'))
        self.ok(self.opening('250', self.fg, rate='5'))
        self.ok(self.opening('120'))  # replaces, doesn't add
        self.assertEqual(StockEntry.objects.count(), 2)
        rows = self.ok(self.api('get', '/stock'))
        self.assertEqual([(r['warehouse_name'], Decimal(r['quantity']), Decimal(r['value'])) for r in rows],
                         [('Finished goods', Decimal('250'), Decimal('1250')), ('Main store', Decimal('120'), Decimal('540'))])
        self.assertEqual(len(self.ok(self.api('get', f"/stock?warehouse_id={self.fg['id']}"))), 1)
        item = self.ok(self.api('get', '/items'))[0]
        self.assertEqual(Decimal(item['on_hand']), Decimal('370'))
        self.assertFalse(item['below_reorder'])
        self.ok(self.opening('0', self.fg), 204)
        self.assertEqual(StockEntry.objects.count(), 1)
        item = self.ok(self.api('get', '/items'))[0]
        self.assertTrue(item['below_reorder'])  # 120 <= 300
        self.assertEqual(len(self.ok(self.api('get', '/stock/opening'))), 1)

    def test_services_negative_and_other_companies_are_refused(self):
        svc = self.item(code='MACHINING', name='Machining', kind='service', hsn_code='998898')
        self.assertIn("service", self.refused(self.api('put', '/stock/opening', {'item_id': svc['id'], 'warehouse_id': self.main['id'], 'quantity': '1'})))
        self.assertIn("negative", self.refused(self.opening('-5')))
        other, other_admin = make_organization("Kaveri", OTHER_GSTIN, "admin@kaveri.example")
        self.login(other_admin.user)
        self.refused(self.opening('5'))
        self.assertEqual(self.ok(self.api('get', '/stock')), [])
        self.assertFalse(StockEntry.objects.exists())

    def test_inactive_item_or_warehouse_refused(self):
        self.ok(self.api('put', f"/items/{self.bolt['id']}", {**BOLT, 'is_active': False}))
        self.assertIn("inactive", self.refused(self.opening('5')))

    def test_stock_needs_stock_view_and_opening_needs_stock_adjust(self):
        operator = add_member(self.company, "op@vaigai.example", "machine_operator").user
        self.login(operator)
        self.refused(self.api('get', '/stock'), 403)
        keeper = add_member(self.company, "keeper@vaigai.example", "store_keeper").user
        self.login(keeper)
        self.ok(self.api('get', '/stock'))
        self.refused(self.opening('5'), 403)  # stock.view but not stock.adjust
        store = add_member(self.company, "store@vaigai.example", "store").user
        self.login(store)
        self.ok(self.opening('5'))


class RoleDefaultsTests(TestCase):
    def test_new_organizations_roles_get_the_inventory_permissions(self):
        company, _ = make_organization("Vaigai", GSTIN, "admin@vaigai.example")
        store = company.roles.get(key='store')
        self.assertTrue({'items.manage', 'warehouses.manage', 'stock.adjust'} <= set(store.permissions.values_list('code', flat=True)))
        self.assertIn('items.manage', company.roles.get(key='purchase_manager').permissions.values_list('code', flat=True))
        for code in ('items.manage', 'warehouses.manage', 'stock.adjust'):
            self.assertNotIn(code, catalog.LIGHT_PERMISSIONS)

    def test_the_migration_grants_existing_system_roles(self):
        from importlib import import_module

        from django.apps import apps as django_apps
        migration = import_module('inventory.migrations.0002_grant_inventory_permissions')
        company, _ = make_organization("Vaigai", GSTIN, "admin@vaigai.example")
        store = company.roles.get(key='store')
        store.permissions.remove(*store.permissions.filter(code__in=['items.manage', 'warehouses.manage', 'stock.adjust']))
        custom = company.roles.get(key='qc')
        migration.grant(django_apps, None)
        self.assertTrue({'items.manage', 'warehouses.manage', 'stock.adjust'} <= set(store.permissions.values_list('code', flat=True)))
        self.assertNotIn('items.manage', custom.permissions.values_list('code', flat=True))
