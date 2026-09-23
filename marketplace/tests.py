from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import User
from accounts.models import ConsumerProfile, ManufacturerProfile, ManufacturingTech, MaterialCapability
from marketplace import services
from marketplace.models import Requirement, RequirementPart, Quote, Order

# See homepage/tests.py's LoginViewTests for why: templates using {% static %}
# need this override under `manage.py test`'s settings — the manifest
# storage used in prod requires a `collectstatic` run this dev environment
# hasn't done.
DASHBOARD_TEST_STORAGES = override_settings(
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
)


class MarketplaceFlowTests(TestCase):
    def setUp(self):
        self.consumer_user = User.objects.create_user(
            username="buyer", email="buyer@example.com", password="pass12345"
        )
        self.manufacturer_user = User.objects.create_user(
            username="maker", email="maker@example.com", password="pass12345", role="manufacturer"
        )
        self.consumer = ConsumerProfile.objects.create(
            user=self.consumer_user,
            Name="Buyer Co",
            type_of_business="electronics",
            city="Bengaluru",
            state="KA",
            country="India",
            phone="9999999991",
            email="buyer@example.com",
            EORI_number="EORI2",
            VAT_number="VAT2",
        )
        self.manufacturer = ManufacturerProfile.objects.create(
            user=self.manufacturer_user,
            phone="8888888881",
            address="123 Industrial Rd",
            city="Pune",
            state="MH",
            country="India",
            amount_of_employees="10-20",
            turnover_per_year="<1",
            certificates="",
            email="maker@example.com",
        )

    def test_requirement_quote_order_lifecycle(self):
        requirement = Requirement.objects.create(
            user=self.consumer_user,
            title="Bracket batch",
            rfq_desc="100 aluminium brackets",
            quote_currency="USD",
            request_reason="new_product",
        )
        quote = Quote.objects.create(
            requirement=requirement,
            supplier=self.manufacturer,
            quote_price="500.00",
        )
        order = Order.objects.create(
            requirement=requirement,
            quote=quote,
            supplier=self.manufacturer,
            customer=self.consumer,
        )
        self.assertEqual(order.status, "submitted")

        order.mark_quoted()
        order.select_quote()
        order.save()

        self.assertEqual(order.status, "quote_selected")
        self.assertEqual(order.status_history.count(), 2)


class MatchPercentServiceTests(TestCase):
    """compute_match_percent() must be a real score, not a placeholder."""

    def setUp(self):
        self.manufacturer_user = User.objects.create_user(
            username="matcher", email="matcher@example.com", password="pass12345", role="manufacturer"
        )
        self.manufacturer = ManufacturerProfile.objects.create(
            user=self.manufacturer_user, phone="8888888882", address="1 Rd", city="Pune",
            state="MH", country="India", amount_of_employees="10-20", turnover_per_year="<1",
            email="matcher@example.com",
        )
        self.consumer_user = User.objects.create_user(username="buyer2", email="buyer2@example.com", password="pass12345")
        self.requirement = Requirement.objects.create(
            user=self.consumer_user, title="Part", rfq_desc="", quote_currency="USD", request_reason="other",
        )
        RequirementPart.objects.create(requirement=self.requirement, part_name="P1", technology="Milling", Material="Aluminium", quantity=10)
        # ManufacturingTech/MaterialCapability are seeded by the
        # populate_choices management command in real environments, which
        # never runs against Django's fresh per-test-run database — create
        # the specific rows each test needs directly instead of assuming
        # they exist.
        self.milling, _ = ManufacturingTech.objects.get_or_create(technology_type="milling")
        self.turning, _ = ManufacturingTech.objects.get_or_create(technology_type="turning")
        self.aluminium, _ = MaterialCapability.objects.get_or_create(material_type="aluminium")

    def test_none_when_profile_has_no_capabilities_or_materials(self):
        self.assertIsNone(services.compute_match_percent(self.requirement, self.manufacturer))

    def test_full_match_scores_100(self):
        self.manufacturer.capabilities.add(self.milling)
        self.manufacturer.materials.add(self.aluminium)
        self.assertEqual(services.compute_match_percent(self.requirement, self.manufacturer), 100)

    def test_process_only_match_scores_process_weight(self):
        self.manufacturer.capabilities.add(self.milling)
        self.assertEqual(services.compute_match_percent(self.requirement, self.manufacturer), 60)

    def test_no_overlap_scores_zero_not_none(self):
        self.manufacturer.capabilities.add(self.turning)
        self.assertEqual(services.compute_match_percent(self.requirement, self.manufacturer), 0)


@DASHBOARD_TEST_STORAGES
class QuoteFormSubmissionTests(TestCase):
    """Regression test for the fixed bug: QuoteCreateView used to bypass its
    own form entirely, hand-building a Quote from raw POST data with no
    validation on quote_price."""

    def setUp(self):
        self.manufacturer_user = User.objects.create_user(
            username="quoter", email="quoter@example.com", password="pass12345", role="manufacturer"
        )
        self.manufacturer = ManufacturerProfile.objects.create(
            user=self.manufacturer_user, phone="8888888883", address="1 Rd", city="Pune",
            state="MH", country="India", amount_of_employees="10-20", turnover_per_year="<1",
            email="quoter@example.com",
        )
        self.consumer_user = User.objects.create_user(username="buyer3", email="buyer3@example.com", password="pass12345")
        self.requirement = Requirement.objects.create(
            user=self.consumer_user, title="Housing", rfq_desc="", quote_currency="USD", request_reason="other",
        )
        RequirementPart.objects.create(requirement=self.requirement, part_name="Housing", technology="Milling", Material="Aluminium", quantity=250)
        self.client.login(username="quoter@example.com", password="pass12345")
        self.url = reverse("new-quote", kwargs={"pk": self.requirement.pk})

    def test_malformed_price_does_not_silently_save(self):
        response = self.client.post(self.url, {
            "quote_price": "not-a-number", "tooling_cost": "0", "lead_time_value": "10",
            "lead_time_unit": "days", "payment_terms": "net_30", "note": "bad",
        })
        self.assertEqual(response.status_code, 200)  # re-rendered with form errors, not saved
        self.assertFalse(Quote.objects.filter(requirement=self.requirement).exists())

    def test_valid_quote_is_created_with_view_assigned_supplier(self):
        response = self.client.post(self.url, {
            "quote_price": "1240", "tooling_cost": "12000", "lead_time_value": "18",
            "lead_time_unit": "days", "payment_terms": "net_30", "note": "ok", "action": "submit",
        })
        self.assertEqual(response.status_code, 302)
        quote = Quote.objects.get(requirement=self.requirement)
        self.assertEqual(quote.supplier_id, self.manufacturer.pk)
        breakdown = quote.get_breakdown()
        self.assertEqual(breakdown["quantity"], 250)
        self.assertGreater(breakdown["total"], 250 * 1240)  # tooling + GST included


@DASHBOARD_TEST_STORAGES
class ProductionStageTests(TestCase):
    def setUp(self):
        self.manufacturer_user = User.objects.create_user(
            username="producer", email="producer@example.com", password="pass12345", role="manufacturer"
        )
        self.manufacturer = ManufacturerProfile.objects.create(
            user=self.manufacturer_user, phone="8888888884", address="1 Rd", city="Pune",
            state="MH", country="India", amount_of_employees="10-20", turnover_per_year="<1",
            email="producer@example.com",
        )
        self.consumer_user = User.objects.create_user(username="buyer4", email="buyer4@example.com", password="pass12345")
        self.consumer = ConsumerProfile.objects.create(
            user=self.consumer_user, Name="Buyer4", type_of_business="electronics", city="Chennai",
            state="TN", country="India", phone="9999999992", email="buyer4@example.com",
            EORI_number="EORI4", VAT_number="VAT4",
        )
        self.requirement = Requirement.objects.create(
            user=self.consumer_user, title="Bracket", rfq_desc="", quote_currency="USD", request_reason="other",
        )
        self.quote = Quote.objects.create(
            requirement=self.requirement, supplier=self.manufacturer, quote_price="100.00", lead_time_value=10,
        )
        self.order = Order.objects.create(
            requirement=self.requirement, quote=self.quote, supplier=self.manufacturer, customer=self.consumer,
        )
        self.order.mark_quoted()
        self.order.select_quote()
        self.order.start_production()
        self.order.save()

    def test_start_production_seeds_qc_checklist_exactly_once(self):
        self.assertEqual(self.order.qc_items.count(), 5)
        self.order._seed_qc_checklist()  # calling again must not duplicate
        self.assertEqual(self.order.qc_items.count(), 5)

    def test_start_production_computes_ship_by_date_from_lead_time(self):
        self.assertIsNotNone(self.order.ship_by_date)

    def test_supplier_can_advance_production_stage(self):
        self.client.login(username="producer@example.com", password="pass12345")
        response = self.client.post(reverse("order-production-advance", kwargs={"billno": self.order.billno}))
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.production_stage, "material_received")
        self.assertEqual(self.order.production_stage_history.count(), 1)

    def test_buyer_cannot_advance_production_stage(self):
        self.client.login(username="buyer4@example.com", password="pass12345")
        self.client.post(reverse("order-production-advance", kwargs={"billno": self.order.billno}))
        self.order.refresh_from_db()
        self.assertEqual(self.order.production_stage, "order_confirmed")

    def test_order_detail_renders_for_both_parties(self):
        url = reverse("order-detail", kwargs={"billno": self.order.billno})
        self.client.login(username="producer@example.com", password="pass12345")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.login(username="buyer4@example.com", password="pass12345")
        self.assertEqual(self.client.get(url).status_code, 200)


@DASHBOARD_TEST_STORAGES
class RFQInboxTabTests(TestCase):
    def setUp(self):
        self.manufacturer_user = User.objects.create_user(
            username="inboxer", email="inboxer@example.com", password="pass12345", role="manufacturer"
        )
        self.manufacturer = ManufacturerProfile.objects.create(
            user=self.manufacturer_user, phone="8888888885", address="1 Rd", city="Pune",
            state="MH", country="India", amount_of_employees="10-20", turnover_per_year="<1",
            email="inboxer@example.com",
        )
        self.buyer = User.objects.create_user(username="buyer5", email="buyer5@example.com", password="pass12345")

        self.new_requirement = Requirement.objects.create(
            user=self.buyer, title="New RFQ", rfq_desc="", quote_currency="USD", request_reason="other",
            end_date=timezone.now() + timedelta(days=5),
        )
        self.quoted_requirement = Requirement.objects.create(
            user=self.buyer, title="Quoted RFQ", rfq_desc="", quote_currency="USD", request_reason="other",
        )
        Quote.objects.create(requirement=self.quoted_requirement, supplier=self.manufacturer, quote_price="10.00")
        self.won_requirement = Requirement.objects.create(
            user=self.buyer, title="Won RFQ", rfq_desc="", quote_currency="USD", request_reason="other",
        )
        Quote.objects.create(requirement=self.won_requirement, supplier=self.manufacturer, quote_price="10.00", is_selected=True)
        self.lost_requirement = Requirement.objects.create(
            user=self.buyer, title="Lost RFQ", rfq_desc="", quote_currency="USD", request_reason="other",
        )
        Quote.objects.create(requirement=self.lost_requirement, supplier=self.manufacturer, quote_price="10.00", status="Rejected")

        self.client.login(username="inboxer@example.com", password="pass12345")

    def test_new_tab_shows_only_unquoted_open_requirement(self):
        response = self.client.get(reverse("requirement-list") + "?tab=new")
        self.assertContains(response, "New RFQ")
        self.assertNotContains(response, "Quoted RFQ")

    def test_quoted_tab_shows_only_pending_quote(self):
        response = self.client.get(reverse("requirement-list") + "?tab=quoted")
        self.assertContains(response, "Quoted RFQ")
        self.assertNotContains(response, "Won RFQ")

    def test_won_tab_shows_only_selected_quote(self):
        response = self.client.get(reverse("requirement-list") + "?tab=won")
        self.assertContains(response, "Won RFQ")

    def test_lost_tab_shows_only_rejected_quote(self):
        response = self.client.get(reverse("requirement-list") + "?tab=lost")
        self.assertContains(response, "Lost RFQ")

    def test_declined_rfq_disappears_from_new_tab(self):
        self.client.post(reverse("requirement-decline", kwargs={"pk": self.new_requirement.pk}))
        response = self.client.get(reverse("requirement-list") + "?tab=new")
        self.assertNotContains(response, "New RFQ")
