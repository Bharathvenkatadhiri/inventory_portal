"""PDF downloads and who may have them, and the branding printed on them."""
import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from organizations.tests import add_member, make_organization
from purchase.tests import INTERSTATE_GSTIN
from sales.tests import SalesTestCase

from . import render

MEDIA = tempfile.mkdtemp(prefix='printouts-tests-')
STORAGE = override_settings(MEDIA_ROOT=MEDIA, STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
})


def png(width=120, height=40):
    from PIL import Image
    data = io.BytesIO()
    Image.new('RGB', (width, height), (17, 53, 92)).save(data, format='PNG')
    return data.getvalue()


def text_of(pdf_bytes):
    """The PDF's text. reportlab stores page streams ASCII85-encoded and
    Flate-compressed; undo both to search them."""
    import base64
    import re
    import zlib
    out = []
    for stream in re.findall(rb'stream\r?\n(.*?)endstream', pdf_bytes, re.S):
        data = stream.strip()
        try:
            if data.endswith(b'~>'):  # ASCII85 with only the end marker
                data = base64.a85decode(data[:-2].replace(b'\n', b'').replace(b'\r', b''))
            data = zlib.decompress(data)
        except (ValueError, zlib.error):
            pass
        out.append(data)
    return b' '.join(out).decode('latin-1')


@STORAGE
class PrintoutTestCase(SalesTestCase):
    """Kaveri (supplier, signed in) accepted Vaigai's PO, dispatched and posted an invoice."""

    def setUp(self):
        super().setUp()
        self.po, self.order = self.accepted()
        self.dc = self.ok(self.send(self.order, ['600', '250'], vehicle_number='TN 37 X 1'))
        draft = self.ok(self.invoice(self.order, ['600', '250']))
        self.draft_id = draft['id']

    def post(self):
        return self.ok(self.api('post', f"/sales-invoices/{self.draft_id}/post"))

    def get_pdf(self, path, status=200):
        response = self.client.get(f'/api/v1{path}')
        self.assertEqual(response.status_code, status, response.content[:200])
        if status == 200:
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertTrue(response.content.startswith(b'%PDF'))
            return text_of(response.content)
        return None


class InvoicePdfTests(PrintoutTestCase):
    def test_posted_invoice_has_what_gst_requires(self):
        invoice = self.post()
        text = self.get_pdf(f"/sales-invoices/{invoice['id']}/pdf")
        for expected in ('TAX INVOICE', invoice['number'], 'Original for recipient', self.supplier.gstin, self.company.gstin,
                         'REVERSE CHARGE', 'HSN-WISE TAX SUMMARY', '7318', '7214', 'CGST', 'SGST', 'Amount in words', 'Rupees',
                         'Authorised signatory'):
            self.assertIn(expected, text, expected)
        self.assertNotIn('DRAFT', text)

    def test_three_copies(self):
        invoice = self.post()
        text = self.get_pdf(f"/sales-invoices/{invoice['id']}/pdf?copies=3")
        for copy in render.COPIES:
            self.assertIn(copy, text)
        self.get_pdf(f"/sales-invoices/{invoice['id']}/pdf?copies=2", status=400)

    def test_draft_is_marked_and_has_no_number(self):
        text = self.get_pdf(f"/sales-invoices/{self.draft_id}/pdf")
        self.assertIn('DRAFT', text)
        self.assertIn('not a valid tax invoice', text)

    def test_the_buyer_can_print_posted_invoices_only(self):
        self.login(self.admin.user)
        self.get_pdf(f"/sales-invoices/{self.draft_id}/pdf", status=404)
        self.as_supplier()
        invoice = self.post()
        self.login(self.admin.user)
        self.assertIn(invoice['number'], self.get_pdf(f"/sales-invoices/{invoice['id']}/pdf"))

    def test_strangers_and_roles_without_the_work_cant(self):
        invoice = self.post()
        other, other_admin = make_organization("Vega", INTERSTATE_GSTIN, "admin@vega.example")
        self.login(other_admin.user)
        self.get_pdf(f"/sales-invoices/{invoice['id']}/pdf", status=404)
        self.get_pdf(f"/dispatches/{self.dc['id']}/pdf", status=404)
        self.get_pdf(f"/purchase-orders/{self.po['id']}/pdf", status=404)
        operator = add_member(self.supplier, "op@kaveri.example", "machine_operator").user
        self.as_supplier(operator)
        self.get_pdf(f"/sales-invoices/{invoice['id']}/pdf", status=404)


class ChallanAndOrderPdfTests(PrintoutTestCase):
    def test_delivery_challan_for_both_sides(self):
        text = self.get_pdf(f"/dispatches/{self.dc['id']}/pdf")
        for expected in ('DELIVERY CHALLAN', self.dc['number'], 'TN 37 X 1', 'CONSIGNOR', 'CONSIGNEE', 'Value of goods'):
            self.assertIn(expected, text, expected)
        self.login(self.admin.user)
        self.get_pdf(f"/dispatches/{self.dc['id']}/pdf")

    def test_purchase_order_for_buyer_and_supplier(self):
        text = self.get_pdf(f"/purchase-orders/{self.po['id']}/pdf")  # the supplier
        self.assertIn('PURCHASE ORDER', text)
        self.assertIn(self.po['number'], text)
        self.login(self.admin.user)
        draft = self.draft()
        self.assertIn('DRAFT', self.get_pdf(f"/purchase-orders/{draft['id']}/pdf"))
        self.as_supplier()
        self.get_pdf(f"/purchase-orders/{draft['id']}/pdf", status=404)  # drafts aren't the supplier's to see


class BrandingTests(PrintoutTestCase):
    def upload(self, kind, content, name='logo.png'):
        return self.client.post(f'/api/v1/organization/branding/{kind}', {'file': SimpleUploadedFile(name, content, 'image/png')})

    def test_logo_signature_and_terms_print(self):
        self.assertEqual(self.upload('logo', png()).status_code, 200)
        self.assertEqual(self.upload('signature', png(200, 60), 'sign.png').status_code, 200)
        branding = self.ok(self.api('put', '/organization/branding', {'signatory_name': 'Nikhil Deshpande', 'invoice_terms': 'Interest at 18% after the due date.'}))
        self.assertTrue(branding['logo_url'] and branding['signature_url'])
        invoice = self.post()
        text = self.get_pdf(f"/sales-invoices/{invoice['id']}/pdf")
        self.assertIn('Nikhil Deshpande', text)
        self.assertIn('Interest at 18%', text)
        self.assertIn('/Image', self.client.get(f"/api/v1/sales-invoices/{invoice['id']}/pdf").content.decode('latin-1'))
        self.ok(self.api('delete', '/organization/branding/logo'))
        self.assertEqual(self.ok(self.api('get', '/organization/branding'))['logo_url'], '')

    def test_only_real_small_images(self):
        response = self.upload('logo', b'<script>not an image</script>')
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/v1/organization/branding/logo',
                                    {'file': SimpleUploadedFile('logo.gif', png(), 'image/gif')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.upload('stamp', png()).status_code, 404)

    def test_only_org_admins_change_branding(self):
        sales = add_member(self.supplier, "sales@kaveri.example", "sales_manager").user
        self.as_supplier(sales)
        self.assertEqual(self.upload('logo', png()).status_code, 403)
        self.refused(self.api('put', '/organization/branding', {'signatory_name': 'x'}), 403)
        self.ok(self.api('get', '/organization/branding'))
