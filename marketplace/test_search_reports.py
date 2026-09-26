from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import User
from accounts.models import Certification, ConsumerProfile, ManufacturerProfile, ManufacturingTech
from marketplace import reports
from marketplace.models import ExchangeRate, Order, Quote, Requirement, RequirementPart, RFQDecline, SupplierReview
from marketplace.templatetags.custom_filters import inr, inr_compact, search_highlight
from marketplace.tests import DASHBOARD_TEST_STORAGES


class Fixtures:
    def make_buyer(self, name):
        user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=user, Name=f"{name} Pvt Ltd", type_of_business="electronics", city="Pune", state="MH", country="India",
            phone=f"9{abs(hash(name)) % 10**9:09d}", email=f"{name}@example.com", EORI_number=f"E{name}", VAT_number=f"V{name}",
        )
        return user

    def make_supplier(self, name, **extra):
        user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", role="manufacturer")
        return ManufacturerProfile.objects.create(
            user=user, companyname=name.title(), phone=f"8{abs(hash(name)) % 10**9:09d}", address="1 Rd", city=extra.pop('city', 'Pune'),
            state="MH", country="India", amount_of_employees="10-20", turnover_per_year="<1", email=f"{name}@example.com", **extra,
        )

    def make_rfq(self, buyer, title, desc="", currency="INR", technology="Milling", material="Aluminium", quantity=10, part_name="Part"):
        rfq = Requirement.objects.create(
            user=buyer, title=title, rfq_desc=desc, quote_currency=currency, request_reason="other",
            end_date=timezone.now() + timedelta(days=5),
        )
        RequirementPart.objects.create(requirement=rfq, part_name=part_name, technology=technology, Material=material, quantity=quantity)
        return rfq

    def make_order(self, rfq, supplier, price="100.00", complete=True):
        quote = Quote.objects.create(requirement=rfq, supplier=supplier, quote_price=price, is_selected=True, status='Approved')
        quote.refresh_from_db()
        order = Order.objects.create(requirement=rfq, quote=quote, supplier=supplier, customer=ConsumerProfile.objects.get(user=rfq.user))
        steps = [order.mark_quoted, order.select_quote, order.start_production]
        if complete:
            steps += [order.request_payment, order.mark_paid, order.complete]
        for step in steps:
            step()
        order.save()
        return order

    def login(self, user):
        self.client.login(username=user.email, password="pass12345")


@DASHBOARD_TEST_STORAGES
class RfqSearchTests(Fixtures, TestCase):
    def setUp(self):
        self.buyer = self.make_buyer("searchbuyer")
        self.other = self.make_buyer("otherbuyer")
        self.housing = self.make_rfq(self.buyer, "Machined aluminium housing", "Anodized enclosure for a motor controller")
        self.bracket = self.make_rfq(self.buyer, "Steel bracket", "Needs a machined <b>flat</b> face", technology="Turning", material="Structural steel")
        self.make_rfq(self.other, "Machined aluminium cover")
        self.url = reverse("global_search_view")

    def results(self, **params):
        return list(self.client.get(self.url, params).context["object_list"])

    def test_stemmed_match_ranks_title_hits_first_and_stays_in_scope(self):
        self.login(self.buyer)
        results = self.results(search="machining")
        self.assertEqual(results, [self.housing, self.bracket])  # title match beats description match
        self.assertGreater(results[0].rank, results[1].rank)

    def test_partial_words_part_names_and_rfq_numbers_still_match(self):
        self.login(self.buyer)
        self.assertEqual(self.results(search="alumin"), [self.housing])
        RequirementPart.objects.create(requirement=self.bracket, part_name="Gearbox flange", technology="Milling", Material="Aluminium")
        self.assertEqual(self.results(search="flange"), [self.bracket])
        self.assertEqual(self.results(search=f"RFQ-{self.bracket.pk}"), [self.bracket])

    def test_filters_work_with_and_without_keywords(self):
        self.login(self.buyer)
        self.assertEqual(self.results(process="Turning"), [self.bracket])
        self.assertEqual(self.results(search="machined", material="Aluminium"), [self.housing])
        self.housing.status = "Completed"
        self.housing.save()
        self.assertEqual(self.results(status="completed"), [self.housing])
        self.assertEqual(self.results(status="open"), [self.bracket])
        self.assertEqual(self.results(date_from=(timezone.localdate() + timedelta(days=1)).isoformat()), [])

    def test_snippet_highlights_matches_and_never_renders_user_html(self):
        self.login(self.buyer)
        page = self.client.get(self.url, {"search": "flat"})
        self.assertContains(page, '<mark class="rounded bg-accent-100 px-0.5 text-navy-900">flat</mark>')
        self.assertNotContains(page, "<b>flat</b>")  # ts_headline drops tags; the filter escapes the rest
        self.assertEqual(str(search_highlight("<script>x")), '&lt;script&gt;<mark class="rounded bg-accent-100 px-0.5 text-navy-900">x</mark>')
        self.assertEqual(str(search_highlight("\x02x\x03 ·  · Parts: ")), '<mark class="rounded bg-accent-100 px-0.5 text-navy-900">x</mark>')


@DASHBOARD_TEST_STORAGES
class SupplierSearchTests(Fixtures, TestCase):
    def setUp(self):
        self.buyer = self.make_buyer("dirbuyer")
        self.sheet = self.make_supplier("sheetco", about="Laser cutting and bending", typical_lead_time_days=20)
        self.sheet.capabilities.add(ManufacturingTech.objects.create(technology_type="sheet_metal"))
        self.cnc = self.make_supplier("cncworks", about="Five-axis machining", typical_lead_time_days=7, city="Chennai")
        Certification.objects.create(manufacturer=self.cnc, name="AS9100 aerospace")
        self.login(self.buyer)
        self.url = reverse("supplier-directory")

    def results(self, **params):
        return list(self.client.get(self.url, params).context["object_list"])

    def test_searches_capability_labels_certifications_and_about(self):
        self.assertEqual(self.results(q="sheet metal fabrication"), [self.sheet])
        self.assertEqual(self.results(q="aerospace"), [self.cnc])
        self.assertEqual(self.results(q="bending"), [self.sheet])
        self.assertEqual(self.results(q="Chennai"), [self.cnc])
        self.assertContains(self.client.get(self.url, {"q": "aerospace"}), "<mark")

    def test_search_combines_with_filters_and_sorts(self):
        self.assertEqual(self.results(q="machining", process="sheet_metal"), [])
        self.assertEqual(self.results(sort="lead_time"), [self.cnc, self.sheet])
        rfq = self.make_rfq(self.buyer, "Panel")
        SupplierReview.objects.create(order=self.make_order(rfq, self.sheet), rating=5)
        self.assertEqual(self.results(sort="rating"), [self.sheet, self.cnc])


@DASHBOARD_TEST_STORAGES
class BuyerSpendReportTests(Fixtures, TestCase):
    def setUp(self):
        self.buyer = self.make_buyer("spendbuyer")
        self.alpha = self.make_supplier("alphaparts")
        self.beta = self.make_supplier("betaworks")
        self.inr_order = self.make_order(self.make_rfq(self.buyer, "INR job", quantity=10), self.alpha, "100.00")
        self.usd_order = self.make_order(self.make_rfq(self.buyer, "USD job", currency="USD", technology="Turning", quantity=2), self.beta, "50.00")
        self.make_order(self.make_rfq(self.buyer, "Still running"), self.alpha, complete=False)
        self.usd_rate = ExchangeRate.objects.get(currency="USD").inr_per_unit

    def total(self, order):
        return order.quote.get_breakdown()["total"]

    def test_only_completed_orders_count_and_are_converted_to_inr(self):
        report = reports.buyer_spend(self.buyer, "12m")
        expected_usd = (self.total(self.usd_order) * self.usd_rate).quantize(Decimal("1"))
        expected_inr = self.total(self.inr_order).quantize(Decimal("1"))
        self.assertEqual(report["order_count"], 2)
        self.assertEqual(report["total_inr"], expected_inr + expected_usd)
        self.assertEqual(report["supplier_count"], 2)
        self.assertEqual({row["name"]: row["value"] for row in report["suppliers"]}, {"Alphaparts": expected_inr, "Betaworks": expected_usd})
        self.assertEqual({row["name"] for row in report["processes"]}, {"Milling", "Turning"})
        self.assertEqual(len(report["months"]), 12)
        self.assertEqual(report["months"][-1]["value"], expected_inr + expected_usd)
        self.assertEqual(report["unconverted"], [])

    def test_currency_without_a_rate_is_flagged_not_guessed(self):
        ExchangeRate.objects.filter(currency="USD").delete()
        report = reports.buyer_spend(self.buyer, "12m")
        self.assertEqual(report["order_count"], 1)
        self.assertEqual((report["unconverted"], report["unconverted_count"]), (["USD"], 1))
        self.assertEqual(len(report["csv_rows"]), 2)

    def test_funnel(self):
        rfq = self.make_rfq(self.buyer, "Expired, no quotes")
        rfq.end_date = timezone.now() - timedelta(days=1)
        rfq.save()
        Requirement.objects.filter(pk__in=[self.inr_order.requirement_id, self.usd_order.requirement_id]).update(status="Completed")
        funnel = reports.buyer_spend(self.buyer, "12m")["funnel"]
        self.assertEqual([step["count"] for step in funnel["steps"]], [4, 3, 3, 2])
        self.assertEqual(funnel["expired_without_quotes"], 1)

    def test_page_and_csv(self):
        self.login(self.buyer)
        page = self.client.get(reverse("reports"), {"period": "6m"})
        self.assertTemplateUsed(page, "reports/buyer_spend.html")
        self.assertContains(page, "Alphaparts")
        csv_response = self.client.get(reverse("reports"), {"period": "6m", "format": "csv"})
        self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
        body = csv_response.content.decode()
        self.assertIn("Total (INR)", body)
        self.assertIn(f"ORD-{self.usd_order.billno}", body)
        self.assertNotIn("Still running", body)


@DASHBOARD_TEST_STORAGES
class SupplierWinRateReportTests(Fixtures, TestCase):
    def setUp(self):
        self.buyer = self.make_buyer("winbuyer")
        self.me = self.make_supplier("mefab")
        self.rival = self.make_supplier("rivalfab")
        # Won (and completed)
        self.make_order(self.make_rfq(self.buyer, "Won job"), self.me, "100.00")
        # Lost to the rival, 10% above their price
        lost_rfq = self.make_rfq(self.buyer, "Lost job", technology="Turning")
        Quote.objects.create(requirement=lost_rfq, supplier=self.me, quote_price="110.00", status="Rejected")
        Quote.objects.create(requirement=lost_rfq, supplier=self.rival, quote_price="100.00", is_selected=True, status="Approved")
        # Still open, a draft, and a declined RFQ
        Quote.objects.create(requirement=self.make_rfq(self.buyer, "Open job"), supplier=self.me, quote_price="90.00")
        Quote.objects.create(requirement=self.make_rfq(self.buyer, "Draft job"), supplier=self.me, quote_price="90.00", is_draft=True)
        RFQDecline.objects.create(requirement=self.make_rfq(self.buyer, "Declined job"), supplier=self.me)

    def test_win_rate_pipeline_and_price_gap(self):
        report = reports.supplier_win_rate(self.me, "12m")
        self.assertEqual((report["submitted"], report["won"], report["lost"], report["pending"]), (3, 1, 1, 1))
        self.assertEqual(report["win_rate"], 50)
        self.assertEqual(report["declined"], 1)
        self.assertEqual(report["price_gaps"]["count"], 1)
        self.assertAlmostEqual(report["price_gaps"]["median"], 10.0)
        self.assertEqual([b["count"] for b in report["price_gaps"]["buckets"]], [0, 0, 1, 0])
        processes = {row["name"]: row for row in report["processes"]}
        self.assertEqual((processes["Milling"]["won"], processes["Turning"]["lost"]), (1, 1))
        self.assertEqual(report["revenue_orders"], 1)

    def test_page_never_names_the_competitor(self):
        self.login(self.me.user)
        page = self.client.get(reverse("reports"))
        self.assertTemplateUsed(page, "reports/supplier_win_rate.html")
        self.assertContains(page, "50%")
        self.assertNotContains(page, "Rivalfab")
        csv_body = self.client.get(reverse("reports"), {"format": "csv"}).content.decode()
        self.assertIn("+10.0%", csv_body)
        self.assertNotIn("Rivalfab", csv_body)


class InrFormattingTests(TestCase):
    def test_indian_grouping_and_compact(self):
        self.assertEqual(inr(Decimal("1234567.4")), "₹12,34,567")
        self.assertEqual(inr(950), "₹950")
        self.assertEqual(inr(None), "—")
        self.assertEqual(inr_compact(1250000), "₹12.5L")
        self.assertEqual(inr_compact(30000000), "₹3Cr")
