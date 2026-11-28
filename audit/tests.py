"""The audit log: changes recorded with who and where, and the Pro-only view."""
from organizations.tests import add_member
from plans import erp
from purchase.tests import PurchaseTestCase

from .models import AuditEvent


class AuditTestCase(PurchaseTestCase):
    plan = erp.PRO

    def events(self, **filters):
        return AuditEvent.objects.filter(company=self.company, **filters).order_by('id')


class RecordingTests(AuditTestCase):
    def test_document_created_changed_and_deleted(self):
        order = self.draft()
        [created] = self.events(object_type='purchase.purchaseorder', action='create')
        self.assertEqual((created.object_label, created.user, created.ip_address, created.changes['status']),
                         (order['number'], self.admin.user, '127.0.0.1', 'draft'))
        self.ok(self.api('post', f"/purchase-orders/{order['id']}/submit"))
        changed = self.events(object_type='purchase.purchaseorder', action='update').last()
        self.assertEqual(changed.changes['status'], ['draft', 'issued'])
        draft = self.draft()
        self.ok(self.api('delete', f"/purchase-orders/{draft['id']}"), 204)
        [deleted] = self.events(object_type='purchase.purchaseorder', action='delete')
        self.assertEqual(deleted.object_label, draft['number'])

    def test_only_real_changes_and_no_secrets(self):
        self.ok(self.api('put', f"/items/{self.bolt.pk}", {'code': 'BOLT', 'name': 'Hex bolt M10', 'kind': 'component', 'unit': 'NOS',
                                                            'hsn_code': '7318', 'gst_rate': '18', 'purchase_price': '6.25'}))
        change = self.events(object_type='inventory.item', action='update').last()
        self.assertEqual(change.changes, {'name': ['Hex bolt', 'Hex bolt M10']})
        self.admin.user.set_password('An0ther-long-passphrase!')
        self.admin.user.save()
        self.assertFalse(AuditEvent.objects.filter(changes__has_key='password').exists())

    def test_each_company_only_sees_its_own(self):
        self.draft()
        self.login(self.supplier_admin.user)
        self.supplier.erp_plan = erp.PRO
        self.supplier.save(update_fields=['erp_plan'])
        seen = self.ok(self.api('get', '/audit'))['events']
        self.assertFalse(any(e['object_type'] == 'purchase.purchaseorder' for e in seen))


class ViewTests(AuditTestCase):
    def test_filters_and_pages(self):
        order = self.draft()
        self.ok(self.api('post', f"/purchase-orders/{order['id']}/submit"))
        page = self.ok(self.api('get', f"/audit?object_type=purchase.purchaseorder&object_id={order['id']}"))
        self.assertEqual([e['action'] for e in page['events']], ['update', 'create'])
        self.assertEqual((page['events'][0]['object_name'], page['events'][0]['user_name']), ('Purchase order', self.admin.user.get_full_name() or self.admin.user.email))
        self.assertEqual(len(self.ok(self.api('get', f"/audit?q={order['number']}"))['events']), 2)

    def test_pro_plan_and_admins_only(self):
        manager = add_member(self.company, 'pm@vaigai.example', 'purchase_manager')
        self.login(manager.user)
        self.assertEqual(self.api('get', '/audit').status_code, 403)
        self.login(self.admin.user)
        self.company.erp_plan = erp.GROWTH
        self.company.save(update_fields=['erp_plan'])
        self.assertEqual(self.api('get', '/audit').status_code, 403)
