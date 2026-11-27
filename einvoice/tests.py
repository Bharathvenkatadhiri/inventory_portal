"""E-invoices and e-way bills through /api/v1, on the mock GSP."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from finance.tests import FinanceTestCase
from jobwork.tests import JobWorkTestCase
from plans import erp
from printouts.tests import text_of
from sales.models import Dispatch, SalesInvoice, SalesInvoiceLine

from .models import EInvoice, EwayBill
from .providers import irn_for
from .services import valid_until, validity_days


class GstPortalTestCase(FinanceTestCase):
    """Kaveri (Growth, e-invoicing on, Pune 411019) has posted an invoice to Vaigai (Madurai 625001)
    and dispatched the goods. Signed in as Kaveri."""
    supplier_plan = erp.GROWTH

    def setUp(self):
        super().setUp()
        self.supplier.einvoice_applicable, self.supplier.pincode, self.supplier.city = True, '411019', 'Pune'
        self.supplier.save()
        self.company.pincode, self.company.city = '625001', 'Madurai'
        self.company.save()
        SalesInvoiceLine.objects.filter(invoice_id=self.inv['id']).update(hsn_code='731815')
        self.dispatch = Dispatch.objects.get(company=self.supplier)

    def register(self):
        return self.api('post', f"/sales-invoices/{self.inv['id']}/einvoice")

    def ewb(self, **extra):
        return self.api('post', f"/dispatches/{self.dispatch.pk}/eway-bill", {'distance_km': 450, 'vehicle_number': 'tn 37 ab 1234', **extra})


class EInvoiceTests(GstPortalTestCase):
    def test_register_print_and_no_second_irn(self):
        state = self.ok(self.register())
        irn = state['current']
        self.assertEqual(irn['irn'], irn_for(self.supplier.gstin, f"20{self.inv['number'][4:6]}-{self.inv['number'][6:8]}", 'INV', self.inv['number']))
        self.assertEqual((len(irn['irn']), len(irn['ack_number']), irn['status'], irn['can_cancel']), (64, 15, 'active', True))
        self.assertFalse(state['can_register'])
        self.assertIn("already has an IRN", self.refused(self.register()))
        saved = EInvoice.objects.get()
        self.assertEqual((saved.request['DocDtls']['Typ'], saved.request['BuyerDtls']['Pin'], saved.request['ItemList'][0]['HsnCd']),
                         ('INV', 625001, '731815'))
        pdf = self.client.get(f"/api/v1/sales-invoices/{self.inv['id']}/pdf")
        self.assertIn(irn['irn'], text_of(pdf.content).replace('\n', ''))

    def test_what_has_to_be_in_place(self):
        SalesInvoiceLine.objects.filter(invoice_id=self.inv['id']).update(hsn_code='7318')
        self.company.pincode = ''
        self.company.save()
        message = self.refused(self.register())
        self.assertIn("6 or 8-digit HSN code", message)
        self.assertIn("Vaigai's PIN code", message)
        self.supplier.einvoice_applicable = False
        self.supplier.save()
        self.assertIn("Settings, Company", self.refused(self.register()))

    def test_cancel_within_24_hours_then_the_invoice(self):
        self.ok(self.register())
        self.assertIn("Cancel its e-invoice", self.refused(self.api('post', f"/sales-invoices/{self.inv['id']}/cancel", {'reason': 'Wrong rate'})))
        path = f"/sales-invoices/{self.inv['id']}/einvoice/cancel"
        self.assertIn("Choose why", self.refused(self.api('post', path, {'reason': '9', 'remark': 'x'})))
        state = self.ok(self.api('post', path, {'reason': '2', 'remark': 'Wrong rate on line 1'}))
        self.assertIsNone(state['current'])
        self.assertEqual(state['history'][0]['cancel_reason_label'], 'Data entry mistake')
        # The number can't be registered again once its IRN is cancelled.
        self.assertIn("can't be used again", self.refused(self.register()))
        self.ok(self.api('post', f"/sales-invoices/{self.inv['id']}/cancel", {'reason': 'Wrong rate'}))

    def test_no_cancelling_after_24_hours(self):
        self.ok(self.register())
        EInvoice.objects.update(ack_date=timezone.now() - timedelta(hours=25))
        state = self.ok(self.api('get', f"/sales-invoices/{self.inv['id']}/einvoice"))
        self.assertFalse(state['current']['can_cancel'])
        self.assertIn("credit note", self.refused(self.api('post', f"/sales-invoices/{self.inv['id']}/einvoice/cancel",
                                                           {'reason': '2', 'remark': 'late'})))

    def test_starter_plan_and_roles(self):
        self.supplier.erp_plan = erp.STARTER
        self.supplier.save()
        self.assertIn("Growth and Pro", self.refused(self.register()))
        self.assertFalse(self.ok(self.api('get', f"/sales-invoices/{self.inv['id']}/einvoice"))['applicable'])


class EwayBillTests(GstPortalTestCase):
    def test_validity_rules(self):
        self.assertEqual([validity_days(d) for d in (1, 200, 201, 450, 4000)], [1, 1, 2, 3, 20])
        self.assertEqual(validity_days(45, 'O'), 3)  # over-dimensional cargo: 20 km a day
        start = timezone.make_aware(timezone.datetime(2026, 11, 27, 22, 0))
        self.assertEqual(timezone.localtime(valid_until(start, 150)).strftime('%Y-%m-%d %H:%M'), '2026-11-28 23:59')

    def test_generate_update_vehicle_and_cancel(self):
        state = self.ok(self.api('get', f"/dispatches/{self.dispatch.pk}/eway-bill"))
        self.assertFalse(state['needed'])  # ₹26,993: under ₹50,000
        self.assertIn("1 to 4000 km", self.refused(self.ewb(distance_km=5000)))
        self.assertIn("TN37AB1234", self.refused(self.ewb(vehicle_number='37 TN 12')))
        state = self.ok(self.ewb())
        ewb = state['current']
        self.assertEqual((len(ewb['number']), ewb['vehicle_number'], ewb['from_pincode'], ewb['to_pincode']), (12, 'TN37AB1234', '411019', '625001'))
        self.assertEqual(timezone.localtime(timezone.datetime.fromisoformat(ewb['valid_until'])).date(), timezone.localdate() + timedelta(days=3))
        self.assertIn("already has an e-way bill", self.refused(self.ewb()))
        vehicle = f"/dispatches/{self.dispatch.pk}/eway-bill/vehicle"
        self.assertIn("Say why", self.refused(self.api('post', vehicle, {'vehicle_number': 'TN38CD4321', 'from_place': 'Salem'})))
        state = self.ok(self.api('post', vehicle, {'vehicle_number': 'TN38CD4321', 'from_place': 'Salem', 'reason': '1'}))
        self.assertEqual([v['vehicle_number'] for v in state['current']['vehicles']], ['TN37AB1234', 'TN38CD4321'])
        pdf = self.client.get(f"/api/v1/dispatches/{self.dispatch.pk}/pdf")
        self.assertIn(ewb['number'], text_of(pdf.content))
        state = self.ok(self.api('post', f"/dispatches/{self.dispatch.pk}/eway-bill/cancel", {'reason': '2', 'remark': 'Order cancelled'}))
        self.assertIsNone(state['current'])
        self.ok(self.ewb())  # a new one can be made

    def test_part_b_later_and_the_24_hour_cancel_limit(self):
        self.assertIn("vehicle number, or the transporter", self.refused(
            self.api('post', f"/dispatches/{self.dispatch.pk}/eway-bill", {'distance_km': 10})))
        state = self.ok(self.ewb(vehicle_number='', transporter_id='27AAACT1234A1Z5', transporter_name='Speed Roadlines'))
        self.assertIsNone(state['current']['valid_until'])  # validity starts with Part B
        state = self.ok(self.api('post', f"/dispatches/{self.dispatch.pk}/eway-bill/vehicle", {'vehicle_number': 'MH12AB1234'}))
        self.assertIsNotNone(state['current']['valid_until'])
        EwayBill.objects.update(generated_at=timezone.now() - timedelta(hours=30))
        self.assertIn("24 hours", self.refused(self.api('post', f"/dispatches/{self.dispatch.pk}/eway-bill/cancel", {'reason': '2', 'remark': 'x'})))

    def test_register_lists_documents_and_whats_pending(self):
        self.supplier.einvoice_applicable = True
        self.supplier.save()
        register = self.ok(self.api('get', '/gst-portal/register'))
        self.assertEqual([p['document_kind'] for p in register['pending']], ['invoice'])
        self.ok(self.register())
        self.ok(self.ewb())
        register = self.ok(self.api('get', '/gst-portal/register'))
        self.assertEqual(sorted(r['kind'] for r in register['rows']), ['einvoice', 'eway_bill'])
        self.assertEqual(register['pending'], [])
        self.assertEqual(register['provider'], 'mock')


class JobWorkEwbTests(JobWorkTestCase):
    def test_job_work_challan_e_way_bill(self):
        self.company.pincode = '625001'
        self.company.save()
        self.supplier.pincode = '641001'
        self.supplier.save()
        challan = self.ok(self.send('40'))
        state = self.ok(self.api('get', f"/job-work/{challan['id']}/eway-bill"))
        self.assertFalse(state['needed'])  # within Tamil Nadu and under ₹50,000
        state = self.ok(self.api('post', f"/job-work/{challan['id']}/eway-bill", {'distance_km': 210, 'vehicle_number': 'TN58Z1234'}))
        self.assertEqual(EwayBill.objects.get().supply_sub_type, '4')
        self.assertEqual(state['current']['distance_km'], 210)
