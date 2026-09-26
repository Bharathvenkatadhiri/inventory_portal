import io
import shutil
import tempfile
from datetime import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import User
from accounts.models import Company, ConsumerProfile
from marketplace import documents
from marketplace.models import Order, OrderDocument
from marketplace.test_search_reports import Fixtures
from marketplace.tests import DASHBOARD_TEST_STORAGES

MEDIA = tempfile.mkdtemp()


@DASHBOARD_TEST_STORAGES
@override_settings(MEDIA_ROOT=MEDIA)
class OrderDocumentTests(Fixtures, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.buyer = self.make_buyer("pobuyer")
        ConsumerProfile.objects.filter(user=self.buyer).update(
            VAT_number="27AAPFU0939F1ZV", Address="12 MIDC Road", city="Pune", state="Maharashtra",
        )
        self.supplier = self.make_supplier("invmaker")
        self.supplier.company = Company.objects.create(
            legal_name="Invmaker Engineering Pvt Ltd", trade_name="Invmaker", gstin="27AABCI1234F1Z5",
            registered_address="Plot 4, Bhosari", city="Pune", state="Maharashtra", pincode="411026",
            verification_status="verified",
        )
        self.supplier.save()
        self.rfq = self.make_rfq(self.buyer, "Pump housing", quantity=10)
        self.quote = self.rfq.quote.create(supplier=self.supplier, quote_price="250.00", tooling_cost="1000.00", payment_terms="net_30")

    def award(self):
        self.login(self.buyer)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("quote-update-status", kwargs={"pk": self.quote.pk, "status": "Approved"}))
        return Order.objects.get(requirement=self.rfq)

    def dispatch(self, order):
        self.login(self.supplier.user)
        self.client.post(reverse("order-update-status", kwargs={"billno": order.billno, "status": "in_production"}))
        with self.captureOnCommitCallbacks(execute=True):
            for _ in range(4):  # order confirmed -> material -> machining -> finishing & QC -> dispatched
                self.client.post(reverse("order-production-advance", kwargs={"billno": order.billno}))
        order.refresh_from_db()
        self.assertEqual(order.production_stage, "dispatched")
        return order

    def test_award_issues_a_numbered_po_with_frozen_party_details(self):
        order = self.award()
        po = OrderDocument.objects.get(order=order, kind=OrderDocument.PURCHASE_ORDER)
        fy = documents.financial_year(po.issued_at)
        self.assertEqual(po.number, f"PO-{fy}-0001")
        self.assertEqual(po.issuer_key, f"buyer:{order.customer_id}")
        self.assertEqual(po.seller["name"], "Invmaker Engineering Pvt Ltd")
        self.assertEqual(po.seller["gstin"], "27AABCI1234F1Z5")
        self.assertEqual(po.buyer["gstin"], "27AAPFU0939F1ZV")
        self.assertTrue(po.pdf.name.endswith(".pdf"))
        # Lines: 10 x 250 plus tooling; totals match the order's own breakdown.
        breakdown = order.quote.get_breakdown()
        self.assertEqual([line["amount"] for line in po.details["lines"]], ["2500.00", "1000.00"])
        self.assertEqual(Decimal(po.details["tax"]["total"]), breakdown["total"])

        # Later profile / GST edits don't touch the issued document.
        Company.objects.filter(pk=self.supplier.company_id).update(legal_name="Renamed Ltd", gstin="29AABCT1332L1ZL")
        po.refresh_from_db()
        self.assertEqual(po.seller["name"], "Invmaker Engineering Pvt Ltd")

        # The buyer's next PO continues their own series.
        rfq2 = self.make_rfq(self.buyer, "Second part")
        quote2 = rfq2.quote.create(supplier=self.supplier, quote_price="10.00")
        self.client.post(reverse("quote-update-status", kwargs={"pk": quote2.pk, "status": "Approved"}))
        self.assertEqual(OrderDocument.objects.get(order__requirement=rfq2).number, f"PO-{fy}-0002")

    def test_dispatch_issues_the_gst_invoice_once_with_cgst_and_sgst_in_state(self):
        order = self.dispatch(self.award())
        invoice = OrderDocument.objects.get(order=order, kind=OrderDocument.INVOICE)
        self.assertTrue(invoice.number.startswith("INV-"))
        self.assertEqual(invoice.issuer_key, f"supplier:{self.supplier.pk}")
        tax = invoice.details["tax"]
        gst = order.quote.get_breakdown()["gst"]
        self.assertTrue(tax["intra_state"])
        self.assertEqual(Decimal(tax["cgst"]) + Decimal(tax["sgst"]), gst)
        self.assertEqual(Decimal(tax["igst"]), 0)
        self.assertEqual(tax["place_of_supply"], "Maharashtra (27)")
        # Advancing past dispatch doesn't issue a second invoice.
        self.client.post(reverse("order-production-advance", kwargs={"billno": order.billno}))
        self.assertEqual(OrderDocument.objects.filter(order=order, kind=OrderDocument.INVOICE).count(), 1)

    def test_inter_state_supply_uses_igst(self):
        ConsumerProfile.objects.filter(user=self.buyer).update(VAT_number="29AABCT1332L1ZL", state="Karnataka")
        order = self.dispatch(self.award())
        tax = OrderDocument.objects.get(order=order, kind=OrderDocument.INVOICE).details["tax"]
        self.assertFalse(tax["intra_state"])
        self.assertEqual(Decimal(tax["igst"]), order.quote.get_breakdown()["gst"])
        self.assertEqual(tax["place_of_supply"], "Karnataka (29)")

    def test_state_names_decide_when_the_buyer_has_no_gstin(self):
        ConsumerProfile.objects.filter(user=self.buyer).update(VAT_number="EU-VAT-123", state="MH")
        order = self.award()
        po = OrderDocument.objects.get(order=order)
        self.assertEqual((po.buyer["gstin"], po.buyer["tax_id"], po.buyer["state_code"]), ("", "EU-VAT-123", "27"))
        self.assertTrue(po.details["tax"]["intra_state"])

    def test_pdf_download_is_limited_to_the_parties_and_staff(self):
        order = self.award()
        po = OrderDocument.objects.get(order=order)
        url = reverse("order-document", kwargs={"pk": po.pk})
        for user in (self.buyer, self.supplier.user):
            self.login(user)
            response = self.client.get(url)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))
        outsider = self.make_buyer("nosy")
        self.login(outsider)
        self.assertEqual(self.client.get(url).status_code, 404)
        staff = User.objects.create_user(username="docstaff", email="docstaff@example.com", password="pass12345", is_staff=True)
        self.login(staff)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_order_page_and_documents_page_list_the_documents(self):
        order = self.award()
        po = OrderDocument.objects.get(order=order)
        self.login(self.buyer)
        page = self.client.get(reverse("order-detail", kwargs={"billno": order.billno}))
        self.assertContains(page, po.number)
        self.assertContains(page, "issued automatically when the order is marked Dispatched")
        self.assertContains(self.client.get(reverse("document-list")), f"{po.number}.pdf")

    def test_backfill_command_issues_missing_documents(self):
        order = self.make_order(self.make_rfq(self.buyer, "Legacy order"), self.supplier)  # completed, no documents
        Order.objects.filter(pk=order.pk).update(production_stage="delivered")
        out = io.StringIO()
        call_command("issue_order_documents", stdout=out)
        self.assertEqual(set(OrderDocument.objects.filter(order=order).values_list("kind", flat=True)), {"po", "invoice"})
        self.assertIn("Issued tax invoice", out.getvalue())
        call_command("issue_order_documents", stdout=io.StringIO())  # re-running issues nothing new
        self.assertEqual(OrderDocument.objects.filter(order=order).count(), 2)


class DocumentHelperTests(TestCase):
    def test_financial_year_turns_over_in_april(self):
        self.assertEqual(documents.financial_year(datetime(2026, 3, 31)), "2526")
        self.assertEqual(documents.financial_year(datetime(2026, 4, 1)), "2627")

    def test_amount_in_words(self):
        self.assertEqual(documents.amount_in_words(Decimal("118000.50")), "Rupees One Lakh Eighteen Thousand and Fifty Paise Only")
        self.assertEqual(documents.amount_in_words(Decimal("12345678")), "Rupees One Crore Twenty-Three Lakh Forty-Five Thousand Six Hundred Seventy-Eight Only")
        self.assertEqual(documents.amount_in_words(Decimal("0"), "USD"), "USD Zero Only")

    def test_gstin_validation(self):
        self.assertEqual(documents.valid_gstin(" 27aapfu0939f1zv "), "27AAPFU0939F1ZV")
        self.assertEqual(documents.valid_gstin("EU123"), "")
