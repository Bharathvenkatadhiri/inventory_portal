"""Django Ninja names each schema in the OpenAPI document by its class name
and silently keeps only one when two modules use the same name. The
TypeScript client is generated from that document, so a clash quietly
gives one endpoint the other's types (it happened twice: billing's and
finance's PaymentOut, and the default-roles and roles-page RoleOut)."""
import importlib
import inspect

from django.test import SimpleTestCase
from ninja import Schema

# Every module core/api.py adds a router from. Add new ones here too.
API_MODULES = ['organizations.api', 'organizations.app_api', 'billing.api', 'inventory.api', 'purchase.api',
               'sales.api', 'finance.api', 'printouts.api', 'adjustments.api', 'gstreturns.api', 'tds.api', 'production.api', 'quality.api', 'jobwork.api', 'quotations.api']


class SchemaNameTests(SimpleTestCase):
    def test_schema_names_are_unique_across_api_modules(self):
        seen = {}
        clashes = []
        for module_name in API_MODULES:
            module = importlib.import_module(module_name)
            for name, obj in vars(module).items():
                if not (inspect.isclass(obj) and issubclass(obj, Schema) and obj.__module__ == module_name):
                    continue
                fields = tuple(sorted(obj.model_fields))
                if name in seen and seen[name][1] != fields:
                    clashes.append(f"{name}: {seen[name][0]} and {module_name}")
                seen.setdefault(name, (module_name, fields))
        self.assertEqual(clashes, [], "Two different schemas share a name; rename one so the OpenAPI document keeps both.")
