import io
import shutil
import tempfile
from datetime import date, datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import User
from accounts.models import SubscriptionPlan, Company, ConsumerProfile
from marketplace import documents
from marketplace.models import ExchangeRate, Order, OrderDocument
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
            principal_address="Plot 4, Bhosari", city="Pune", state="Maharashtra", pincode="411026",
            gst_verified=True,
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
        self.assertEqual(self.client.get(reverse("document-list")).status_code, 402)  # Free: per-order downloads only
        SubscriptionPlan.objects.create(user_profile=self.buyer, plan_type="starter", price=999)
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

    def test_storage_key_is_random_not_the_document_number(self):
        # Two different buyers' first PO both number "PO-2627-0001" (numbering
        # is per issuer, not global) — the stored file must not collide on
        # that name, or a same-named upload could silently overwrite it.
        order = self.award()
        po = OrderDocument.objects.get(order=order)
        self.assertNotIn(po.number, po.pdf.name)
        self.assertTrue(po.pdf.name.endswith(".pdf"))

        other_buyer = self.make_buyer("pobuyer2")
        ConsumerProfile.objects.filter(user=other_buyer).update(
            VAT_number="27AAPFU0939F1ZW", Address="1 Other Road", city="Pune", state="Maharashtra",
        )
        other_rfq = self.make_rfq(other_buyer, "Pump housing 2", quantity=10)
        other_quote = other_rfq.quote.create(supplier=self.supplier, quote_price="250.00", tooling_cost="1000.00", payment_terms="net_30")
        self.login(other_buyer)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("quote-update-status", kwargs={"pk": other_quote.pk, "status": "Approved"}))
        other_po = OrderDocument.objects.get(order__requirement=other_rfq)

        self.assertEqual(po.number, other_po.number)  # same number, different issuer
        self.assertNotEqual(po.pdf.name, other_po.pdf.name)  # never the same storage key
        # Each buyer still downloads a PDF named after their own document number.
        self.login(self.buyer)
        response = self.client.get(reverse("order-document", kwargs={"pk": po.pk}))
        self.assertIn(f'filename="{po.number}.pdf"', response["Content-Disposition"])
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))

    def test_rerender_replaces_the_stored_file_and_keeps_the_number(self):
        order = self.award()
        po = OrderDocument.objects.get(order=order)
        old_name = po.pdf.name
        documents.rerender_pdf(po)
        po.refresh_from_db()
        self.assertNotEqual(po.pdf.name, old_name)
        self.assertFalse(po.pdf.storage.exists(old_name))  # the old file was deleted, not orphaned
        self.assertEqual(po.number, OrderDocument.objects.get(pk=po.pk).number)

    def test_rerender_flag_rewrites_every_stored_document(self):
        order = self.award()
        po = OrderDocument.objects.get(order=order)
        old_name = po.pdf.name
        out = io.StringIO()
        call_command("issue_order_documents", "--rerender", stdout=out)
        po.refresh_from_db()
        self.assertNotEqual(po.pdf.name, old_name)
        self.assertIn(f"Re-rendered Purchase order {po.number}", out.getvalue())


class DocumentHelperTests(TestCase):
    def test_financial_year_turns_over_in_april(self):
        self.assertEqual(documents.financial_year(datetime(2026, 3, 31)), "2526")
        self.assertEqual(documents.financial_year(datetime(2026, 4, 1)), "2627")

    def test_amount_in_words(self):
        self.assertEqual(documents.amount_in_words(Decimal("118000.50")), "Rupees One Lakh Eighteen Thousand and Fifty Paise Only")
        self.assertEqual(documents.amount_in_words(Decimal("12345678")), "Rupees One Crore Twenty-Three Lakh Forty-Five Thousand Six Hundred Seventy-Eight Only")
        self.assertEqual(documents.amount_in_words(Decimal("0"), "USD"), "US Dollars Zero Only")
        self.assertEqual(documents.amount_in_words(Decimal("2500000.25"), "USD"), "US Dollars Two Million Five Hundred Thousand and Twenty-Five Cents Only")
        self.assertEqual(documents.amount_in_words(Decimal("10.50"), "GBP"), "Pounds Sterling Ten and Fifty Pence Only")
        self.assertEqual(documents.amount_in_words(Decimal("1200.40"), "JPY"), "Yen One Thousand Two Hundred Only")

    def test_gstin_validation(self):
        self.assertEqual(documents.valid_gstin(" 27aapfu0939f1zv "), "27AAPFU0939F1ZV")
        self.assertEqual(documents.valid_gstin("EU123"), "")


@DASHBOARD_TEST_STORAGES
@override_settings(MEDIA_ROOT=MEDIA)
class CurrencyAndExportTests(OrderDocumentTests):
    """Reuses OrderDocumentTests' setUp and helpers; its own tests are
    switched off here so they don't run twice."""
    for _name in [n for n in dir(OrderDocumentTests) if n.startswith("test_")]:
        locals()[_name] = None
    del _name

    def make_export(self, lut=True, currency="EUR"):
        ConsumerProfile.objects.filter(user=self.buyer).update(country="Germany", state="Bavaria", VAT_number="DE123456789")
        if lut:
            Company.objects.filter(pk=self.supplier.company_id).update(lut_reference="AD270326000123X")
        self.rfq.quote_currency = currency
        self.rfq.save()

    def test_export_under_lut_is_zero_rated_everywhere(self):
        self.make_export(lut=True)
        quote = self.rfq.quote.get()
        breakdown = quote.get_breakdown()
        self.assertEqual((breakdown["gst"], breakdown["gst_label"]), (Decimal("0.00"), "GST (0%, export under LUT)"))
        self.assertEqual(breakdown["total"], breakdown["subtotal"])
        self.login(self.supplier.user)
        form = self.client.get(reverse("edit-quote", kwargs={"pk": quote.pk}))
        self.assertEqual(form.context["gst_rate"], "0")
        order = self.dispatch(self.award())
        tax = OrderDocument.objects.get(order=order, kind="invoice").details["tax"]
        self.assertEqual(tax["treatment"], "export_lut")
        self.assertEqual((Decimal(tax["igst"]), tax["total"]), (Decimal("0"), tax["subtotal"]))
        self.assertIn("under LUT without payment of IGST (LUT ARN AD270326000123X)", tax["export_declaration"])
        self.assertEqual((tax["country_of_destination"], tax["place_of_supply"]), ("Germany", "Outside India (Germany)"))
        self.assertContains(self.client.get(reverse("order-detail", kwargs={"billno": order.billno})), "GST (0%, export under LUT)")

    def test_export_without_a_valid_lut_charges_igst(self):
        self.make_export(lut=True)
        Company.objects.filter(pk=self.supplier.company_id).update(lut_valid_until=date(2020, 3, 31))  # expired
        order = self.award()
        tax = OrderDocument.objects.get(order=order).details["tax"]
        self.assertEqual(tax["treatment"], "export_igst")
        self.assertEqual(Decimal(tax["igst"]), order.quote.get_breakdown()["gst"])
        self.assertGreater(Decimal(tax["igst"]), 0)
        self.assertEqual(tax["export_declaration"], "Supply meant for export on payment of IGST")

    def test_foreign_currency_documents_carry_frozen_inr_values(self):
        self.rfq.quote_currency = "USD"
        self.rfq.save()
        rate = ExchangeRate.objects.get(currency="USD").inr_per_unit
        order = self.award()
        po = OrderDocument.objects.get(order=order)
        inr = po.details["inr"]
        self.assertEqual(Decimal(inr["rate"]), rate)
        self.assertEqual(Decimal(inr["total"]), (Decimal(po.details["tax"]["total"]) * rate).quantize(Decimal("0.01")))
        self.assertEqual(Decimal(inr["subtotal"]), (Decimal(po.details["tax"]["subtotal"]) * rate).quantize(Decimal("0.01")))
        ExchangeRate.objects.filter(currency="USD").update(inr_per_unit="1.0")
        po.refresh_from_db()
        self.assertEqual(Decimal(po.details["inr"]["rate"]), rate)  # frozen at issue
        self.assertTrue(documents.render_pdf(po).startswith(b"%PDF"))

    def test_missing_rate_is_recorded_not_guessed_and_inr_documents_have_no_panel(self):
        self.rfq.quote_currency = "USD"
        self.rfq.save()
        ExchangeRate.objects.filter(currency="USD").delete()
        po = OrderDocument.objects.get(order=self.award())
        self.assertEqual(po.details["inr"], {"rate": None})
        self.assertTrue(documents.render_pdf(po).startswith(b"%PDF"))
        rfq2 = self.make_rfq(self.buyer, "Rupee job")
        quote2 = rfq2.quote.create(supplier=self.supplier, quote_price="10.00")
        self.client.post(reverse("quote-update-status", kwargs={"pk": quote2.pk, "status": "Approved"}))
        self.assertIsNone(OrderDocument.objects.get(order__requirement=rfq2).details["inr"])

    def test_export_pdf_renders(self):
        self.make_export(lut=True, currency="EUR")
        po = OrderDocument.objects.get(order=self.award())
        self.assertTrue(documents.render_pdf(po).startswith(b"%PDF"))

    def test_manufacturer_saves_lut_from_company_profile(self):
        self.login(self.supplier.user)
        self.assertContains(self.client.get(reverse("company-profile")), "Exports (LUT)")
        self.client.post(reverse("company-lut-update"), {"lut_reference": "ad270326000123x", "lut_valid_until": "2027-03-31"})
        company = Company.objects.get(pk=self.supplier.company_id)
        self.assertEqual((company.lut_reference, str(company.lut_valid_until)), ("AD270326000123X", "2027-03-31"))
        self.client.post(reverse("company-lut-update"), {"lut_reference": "bad ref!", "lut_valid_until": ""})
        self.assertEqual(Company.objects.get(pk=self.supplier.company_id).lut_reference, "AD270326000123X")
