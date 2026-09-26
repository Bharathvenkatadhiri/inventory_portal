import shutil
from decimal import Decimal
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import User
from accounts.models import ConsumerProfile, ManufacturerProfile, ManufacturingTech, MaterialCapability
from marketplace import services
from marketplace.models import Requirement, RequirementPart, Quote, Order, OrderEvent, MessageThread, Message, RFQDecline, RequirementAmendment, AmendmentResponse, SupplierReview

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
        self.assertEqual(order.status_events().count(), 2)


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
        self.assertEqual(len(self.order.qc_checklist), 5)
        self.order._seed_qc_checklist()  # calling again must not duplicate
        self.assertEqual(len(self.order.qc_checklist), 5)

    def test_start_production_computes_ship_by_date_from_lead_time(self):
        self.assertIsNotNone(self.order.ship_by_date)

    def test_supplier_can_advance_production_stage(self):
        self.client.login(username="producer@example.com", password="pass12345")
        response = self.client.post(reverse("order-production-advance", kwargs={"billno": self.order.billno}))
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.production_stage, "material_received")
        self.assertEqual(self.order.stage_events().count(), 1)

    def test_htmx_stage_advance_shows_message_immediately_not_after_logout(self):
        self.client.login(username="producer@example.com", password="pass12345")
        response = self.client.post(
            reverse("order-production-advance", kwargs={"billno": self.order.billno}), HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, 'id="flash-messages"')
        self.assertContains(response, "complete.")
        self.assertContains(response, 'hx-swap-oob="true"')
        # Consumed: the next full page no longer shows it.
        self.assertNotContains(self.client.get(reverse("home")), "complete.")

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


TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix="test-media-")


@DASHBOARD_TEST_STORAGES
@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class MessagingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.buyer = User.objects.create_user(username="msgbuyer", email="msgbuyer@example.com", password="pass12345")
        self.consumer = ConsumerProfile.objects.create(
            user=self.buyer, Name="Msg Buyer", type_of_business="electronics", city="Pune", state="MH",
            country="India", phone="9200000001", email="msgbuyer@example.com", EORI_number="EORIM1", VAT_number="VATM1",
        )
        self.maker = User.objects.create_user(username="msgmaker", email="msgmaker@example.com", password="pass12345", role="manufacturer")
        self.supplier = ManufacturerProfile.objects.create(
            user=self.maker, companyname="Msg Maker", phone="8200000001", address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="msgmaker@example.com",
        )
        self.outsider = User.objects.create_user(username="msgoutsider", email="msgoutsider@example.com", password="pass12345")
        self.requirement = Requirement.objects.create(
            user=self.buyer, title="Housing", rfq_desc="", quote_currency="INR", request_reason="other",
        )
        self.thread = MessageThread.objects.create(requirement=self.requirement, supplier=self.supplier)
        self.thread_url = reverse("message-thread", kwargs={"pk": self.thread.pk})

    def _send(self, **data):
        return self.client.post(self.thread_url, data)

    def test_message_with_attachment_is_stored_and_downloadable_by_participants(self):
        self.client.login(username="msgbuyer@example.com", password="pass12345")
        upload = SimpleUploadedFile("drawing_revB.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        self._send(body="See attached", attachment=upload)
        message = Message.objects.get(thread=self.thread)
        self.assertEqual(message.attachment_name, "drawing_revB.pdf")
        self.assertNotIn("drawing_revB", message.attachment.name)  # random storage name

        download_url = reverse("message-attachment", kwargs={"pk": message.pk})
        self.client.login(username="msgmaker@example.com", password="pass12345")
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("drawing_revB.pdf", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")

        self.client.login(username="msgoutsider@example.com", password="pass12345")
        self.assertEqual(self.client.get(download_url).status_code, 404)

    def test_disallowed_file_type_is_rejected(self):
        self.client.login(username="msgbuyer@example.com", password="pass12345")
        self._send(attachment=SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream"))
        self.assertFalse(Message.objects.exists())

    def test_empty_message_is_rejected(self):
        self.client.login(username="msgbuyer@example.com", password="pass12345")
        self._send(body="   ")
        self.assertFalse(Message.objects.exists())

    def test_sender_can_delete_own_message_and_file_is_removed(self):
        self.client.login(username="msgbuyer@example.com", password="pass12345")
        self._send(body="oops", attachment=SimpleUploadedFile("a.pdf", b"x", content_type="application/pdf"))
        message = Message.objects.get(thread=self.thread)
        storage, path = message.attachment.storage, message.attachment.name
        self.client.post(reverse("message-delete", kwargs={"pk": message.pk}))
        message.refresh_from_db()
        self.assertTrue(message.is_deleted)
        self.assertEqual(message.body, "")
        self.assertFalse(storage.exists(path))
        self.assertEqual(self.client.get(reverse("message-attachment", kwargs={"pk": message.pk})).status_code, 404)

    def test_other_participant_cannot_delete_message(self):
        message = Message.objects.create(thread=self.thread, sender=self.buyer, body="mine")
        self.client.login(username="msgmaker@example.com", password="pass12345")
        self.client.post(reverse("message-delete", kwargs={"pk": message.pk}))
        message.refresh_from_db()
        self.assertFalse(message.is_deleted)

    def test_thread_cannot_be_closed_before_rfq_completion(self):
        self.client.login(username="msgbuyer@example.com", password="pass12345")
        self.client.post(reverse("message-thread-close", kwargs={"pk": self.thread.pk}))
        self.thread.refresh_from_db()
        self.assertFalse(self.thread.is_closed)

    def test_finishing_the_order_closes_threads_and_blocks_new_messages_and_deletes(self):
        message = Message.objects.create(thread=self.thread, sender=self.buyer, body="before close")
        quote = Quote.objects.create(requirement=self.requirement, supplier=self.supplier, quote_price="10.00")
        self.client.login(username="msgbuyer@example.com", password="pass12345")
        self.client.post(reverse("quote-update-status", kwargs={"pk": quote.pk, "status": "Approved"}))
        order = Order.objects.get(requirement=self.requirement)

        # Completing the RFQ alone no longer closes the conversation: the
        # order still has payment and delivery ahead of it.
        self.client.login(username="msgmaker@example.com", password="pass12345")
        self.client.post(reverse("requirement-update-status", kwargs={"pk": self.requirement.pk, "status": "Production"}))
        self.client.post(reverse("requirement-update-status", kwargs={"pk": self.requirement.pk, "status": "Completed"}))
        self.thread.refresh_from_db()
        self.assertFalse(self.thread.is_closed)

        for status in ("payment_pending", "paid", "completed"):
            self.client.post(reverse("order-update-status", kwargs={"billno": order.billno, "status": status}))
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")
        self.thread.refresh_from_db()
        self.assertTrue(self.thread.is_closed)

        self.client.login(username="msgbuyer@example.com", password="pass12345")
        self._send(body="after close")
        self.assertFalse(Message.objects.filter(body="after close").exists())
        self.client.post(reverse("message-delete", kwargs={"pk": message.pk}))
        message.refresh_from_db()
        self.assertFalse(message.is_deleted)
        self.assertContains(self.client.get(self.thread_url), "read-only")

    def test_participant_can_close_thread_after_rfq_completion(self):
        self.requirement.status = "Completed"
        self.requirement.save()
        self.client.login(username="msgbuyer@example.com", password="pass12345")
        self.client.post(reverse("message-thread-close", kwargs={"pk": self.thread.pk}))
        self.thread.refresh_from_db()
        self.assertTrue(self.thread.is_closed)
        self.assertEqual(self.thread.closed_by, self.buyer)

    def test_outsider_cannot_view_or_post(self):
        self.client.login(username="msgoutsider@example.com", password="pass12345")
        self.assertEqual(self.client.get(self.thread_url).status_code, 404)
        self.assertEqual(self._send(body="hi").status_code, 404)


@DASHBOARD_TEST_STORAGES
class AskBuyerAndNotificationTests(TestCase):
    """A manufacturer's question to the buyer is a message in their RFQ
    thread (the separate Q&A table was merged into messaging)."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.buyer = User.objects.create_user(username="qbuyer", email="qbuyer@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=self.buyer, Name="Q Buyer", type_of_business="electronics", city="Pune", state="MH",
            country="India", phone="9300000001", email="qbuyer@example.com", EORI_number="EORIQ1", VAT_number="VATQ1",
        )
        self.maker = User.objects.create_user(username="qmaker", email="qmaker@example.com", password="pass12345", role="manufacturer")
        self.supplier = ManufacturerProfile.objects.create(
            user=self.maker, companyname="Q Maker", phone="8300000001", address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="qmaker@example.com",
        )
        self.requirement = Requirement.objects.create(
            user=self.buyer, title="Bracket", rfq_desc="", quote_currency="INR", request_reason="other",
            end_date=timezone.now() + timedelta(days=5),
        )
        self.rfq_url = reverse("requirement", kwargs={"pk": self.requirement.pk})

    def _ask(self, text="Is black anodize required?"):
        self.client.login(username="qmaker@example.com", password="pass12345")
        self.client.post(reverse("message-thread-ask-buyer", kwargs={"requirement_pk": self.requirement.pk}), {"body": text})
        return Message.objects.get(body=text)

    def test_question_reaches_buyer_and_reply_reaches_manufacturer(self):
        question = self._ask()
        thread = question.thread
        self.assertEqual((thread.requirement, thread.supplier), (self.requirement, self.supplier))

        self.client.login(username="qbuyer@example.com", password="pass12345")
        response = self.client.get(self.rfq_url)
        self.assertContains(response, "Conversations with suppliers")
        self.assertContains(response, "Is black anodize required?")
        self.client.post(reverse("message-thread", kwargs={"pk": thread.pk}), {"body": "Yes, black."})

        self.client.login(username="qmaker@example.com", password="pass12345")
        self.assertContains(self.client.get(self.rfq_url), "Yes, black.")
        reply = Message.objects.get(body="Yes, black.")
        self.assertTrue(any(e["key"] == f"message:{reply.pk}" and e["unread"] for e in services.notification_feed(self.maker)))

    def test_asking_again_reuses_the_same_thread(self):
        first = self._ask("First?")
        second = self._ask("Second?")
        self.assertEqual(first.thread_id, second.thread_id)
        self.assertEqual(MessageThread.objects.filter(requirement=self.requirement).count(), 1)

    def test_manufacturer_cannot_ask_on_an_rfq_they_cannot_see(self):
        self.requirement.end_date = timezone.now() - timedelta(days=1)  # expired, never quoted
        self.requirement.save()
        self.client.login(username="qmaker@example.com", password="pass12345")
        response = self.client.post(reverse("message-thread-ask-buyer", kwargs={"requirement_pk": self.requirement.pk}), {"body": "Hi"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(MessageThread.objects.exists())

    def test_new_supplier_message_is_an_unread_notification_with_bell_badge(self):
        question = self._ask()
        self.client.login(username="qbuyer@example.com", password="pass12345")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["topbar_unread_notifications"], 1)
        self.assertContains(response, "(1 unread)")
        self.assertContains(response, "sent you a message")  # dashboard "Action needed" panel
        keys = [e["key"] for e in response.context["topbar_notifications"]]
        self.assertIn(f"message:{question.pk}", keys)

    def test_mark_single_and_mark_all_as_read(self):
        first = self._ask("First?")
        second = self._ask("Second?")
        self.client.login(username="qbuyer@example.com", password="pass12345")

        self.client.post(reverse("notification-mark-read"), {"key": f"message:{first.pk}"})
        unread = {e["key"] for e in services.notification_feed(self.buyer) if e["unread"]}
        self.assertEqual(unread, {f"message:{second.pk}"})

        self.client.post(reverse("notification-mark-all-read"))
        self.assertFalse(any(e["unread"] for e in services.notification_feed(self.buyer)))

    def test_opening_a_notification_marks_it_read_and_redirects(self):
        question = self._ask()
        self.client.login(username="qbuyer@example.com", password="pass12345")
        response = self.client.get(reverse("notification-open"), {"key": f"message:{question.pk}", "next": self.rfq_url})
        self.assertRedirects(response, self.rfq_url)
        self.assertFalse(any(e["unread"] for e in services.notification_feed(self.buyer)))

    def test_open_refuses_external_redirect(self):
        self.client.login(username="qbuyer@example.com", password="pass12345")
        response = self.client.get(reverse("notification-open"), {"key": "x", "next": "https://evil.example.com/"})
        self.assertRedirects(response, reverse("notification-list"))

    def test_message_notification_is_read_once_thread_is_opened(self):
        thread = MessageThread.objects.create(requirement=self.requirement, supplier=self.supplier)
        Message.objects.create(thread=thread, sender=self.maker, body="Hello")
        self.assertTrue(any(e["kind"] == "message" and e["unread"] for e in services.notification_feed(self.buyer)))
        self.client.login(username="qbuyer@example.com", password="pass12345")
        self.client.get(reverse("message-thread", kwargs={"pk": thread.pk}))
        self.assertFalse(any(e["kind"] == "message" and e["unread"] for e in services.notification_feed(self.buyer)))


@DASHBOARD_TEST_STORAGES
class OwnershipAndAwardTests(TestCase):
    """Covers the fixes from the codebase review: ownership checks, POST-only
    status changes, orders created at award, scoped search, plan limits,
    per-currency totals, part counts and documents."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.buyer = User.objects.create_user(username="ownbuyer", email="ownbuyer@example.com", password="pass12345")
        self.consumer = ConsumerProfile.objects.create(
            user=self.buyer, Name="Own Buyer", type_of_business="electronics", city="Pune", state="MH",
            country="India", phone="9500000001", email="ownbuyer@example.com", EORI_number="EO1", VAT_number="VO1",
        )
        self.other_buyer = User.objects.create_user(username="otherbuyer", email="otherbuyer@example.com", password="pass12345")
        self.maker = User.objects.create_user(username="ownmaker", email="ownmaker@example.com", password="pass12345", role="manufacturer")
        self.supplier = ManufacturerProfile.objects.create(
            user=self.maker, companyname="Own Maker", phone="8500000001", address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="ownmaker@example.com",
        )
        self.rival = User.objects.create_user(username="rivalmaker", email="rivalmaker@example.com", password="pass12345", role="manufacturer")
        self.rival_supplier = ManufacturerProfile.objects.create(
            user=self.rival, companyname="Rival Maker", phone="8500000002", address="2 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="rivalmaker@example.com",
        )
        self.requirement = Requirement.objects.create(
            user=self.buyer, title="Motor housing", rfq_desc="Aluminium", quote_currency="INR",
            request_reason="other", end_date=timezone.now() + timedelta(days=5),
        )
        RequirementPart.objects.create(requirement=self.requirement, part_name="Housing", technology="Milling", Material="Aluminium", quantity=250)
        self.quote = Quote.objects.create(requirement=self.requirement, supplier=self.supplier, quote_price="100.00", lead_time_value=10)
        self.rival_quote = Quote.objects.create(requirement=self.requirement, supplier=self.rival_supplier, quote_price="120.00")

    def login(self, user):
        self.client.login(username=user.email, password="pass12345")

    def award(self):
        self.login(self.buyer)
        return self.client.post(reverse("quote-update-status", kwargs={"pk": self.quote.pk, "status": "Approved"}))

    # --- RFQ visibility and ownership ----------------------------------
    def test_other_buyers_cannot_view_edit_or_delete_an_rfq(self):
        self.login(self.other_buyer)
        for name in ("requirement", "edit-requirement", "delete-requirement"):
            self.assertEqual(self.client.get(reverse(name, kwargs={"pk": self.requirement.pk})).status_code, 404, name)
        self.client.post(reverse("delete-requirement", kwargs={"pk": self.requirement.pk}))
        self.requirement.refresh_from_db()
        self.assertFalse(self.requirement.is_deleted)

    def test_manufacturers_can_still_view_rfqs_to_quote(self):
        self.login(self.rival)
        self.assertEqual(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})).status_code, 200)

    def test_awarded_rfq_can_no_longer_be_edited(self):
        self.award()
        self.assertEqual(self.client.get(reverse("edit-requirement", kwargs={"pk": self.requirement.pk})).status_code, 403)

    def test_new_rfq_belongs_to_the_poster_and_counts_its_part_rows(self):
        self.login(self.buyer)
        self.client.post(reverse("new-requirement"), {
            "title": "Bracket", "rfq_desc": "d", "quote_currency": "INR", "request_reason": "other",
            "nda_required": "False", "user": self.other_buyer.pk,
            "requirement_parts-TOTAL_FORMS": "1", "requirement_parts-INITIAL_FORMS": "0",
            "requirement_parts-0-part_name": "Bracket", "requirement_parts-0-technology": "Milling",
            "requirement_parts-0-Material": "Aluminium", "requirement_parts-0-quantity": "40",
        })
        created = Requirement.objects.get(title="Bracket")
        self.assertEqual(created.user, self.buyer)
        self.assertEqual(created.parts, 1)
        self.assertEqual(created.total_parts_quantity(), 40)

    # --- Quotes -----------------------------------------------------------
    def test_suppliers_cannot_edit_or_delete_another_suppliers_quote(self):
        self.login(self.rival)
        self.assertEqual(self.client.get(reverse("edit-quote", kwargs={"pk": self.quote.pk})).status_code, 404)
        self.client.post(reverse("delete-quote", kwargs={"pk": self.quote.pk}))
        self.quote.refresh_from_db()
        self.assertFalse(self.quote.is_deleted)

    def test_selecting_a_quote_requires_post_by_the_rfq_owner(self):
        url = reverse("quote-update-status", kwargs={"pk": self.quote.pk, "status": "Approved"})
        self.login(self.buyer)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.login(self.other_buyer)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.quote.refresh_from_db()
        self.assertFalse(self.quote.is_selected)

    def test_awarding_creates_the_order_and_rejects_the_rest(self):
        self.award()
        self.quote.refresh_from_db()
        self.rival_quote.refresh_from_db()
        self.assertTrue(self.quote.is_selected)
        self.assertEqual(self.rival_quote.status, "Rejected")
        order = Order.objects.get(requirement=self.requirement)
        self.assertEqual(order.status, "quote_selected")
        self.assertEqual(order.supplier, self.supplier)
        self.assertEqual(order.customer, self.consumer)

    def test_decided_quote_cannot_be_edited(self):
        self.award()
        self.login(self.maker)
        self.assertEqual(self.client.get(reverse("edit-quote", kwargs={"pk": self.quote.pk})).status_code, 403)

    # --- Production / RFQ status ----------------------------------------
    def test_only_the_awarded_supplier_can_start_production_and_only_by_post(self):
        self.award()
        url = reverse("requirement-update-status", kwargs={"pk": self.requirement.pk, "status": "Production"})
        self.login(self.rival)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.login(self.maker)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.requirement.refresh_from_db()
        self.assertEqual(self.requirement.status, "Production")
        order = Order.objects.get(requirement=self.requirement)
        self.assertEqual(order.status, "in_production")
        self.assertEqual(len(order.qc_checklist), 5)
        self.assertIsNotNone(order.ship_by_date)

    def test_legacy_awarded_rfq_without_order_gets_one_when_production_starts(self):
        self.quote.is_selected = True
        self.quote.status = "Approved"
        self.quote.save()
        self.requirement.status = "Approved"
        self.requirement.save()
        self.login(self.maker)
        self.client.post(reverse("requirement-update-status", kwargs={"pk": self.requirement.pk, "status": "Production"}))
        self.assertEqual(Order.objects.get(requirement=self.requirement).status, "in_production")

    # --- Orders -----------------------------------------------------------
    def test_outsiders_cannot_view_or_change_an_order(self):
        self.award()
        order = Order.objects.get(requirement=self.requirement)
        self.login(self.rival)
        self.assertEqual(self.client.get(reverse("order-detail", kwargs={"billno": order.billno})).status_code, 404)
        self.assertEqual(self.client.post(reverse("order-update-status", kwargs={"billno": order.billno, "status": "cancelled"})).status_code, 404)
        order.refresh_from_db()
        self.assertEqual(order.status, "quote_selected")

    # --- Search -----------------------------------------------------------
    def test_search_only_returns_rfqs_the_user_may_see(self):
        self.login(self.other_buyer)
        response = self.client.get(reverse("global_search_view"), {"search": "Motor"})
        self.assertNotContains(response, "Motor housing")
        self.login(self.buyer)
        self.assertContains(self.client.get(reverse("global_search_view"), {"search": "Motor"}), "Motor housing")
        self.assertContains(self.client.get(reverse("global_search_view"), {"search": f"RFQ-{self.requirement.pk}"}), "Motor housing")

    def test_field_prefix_search_no_longer_errors(self):
        self.login(self.buyer)
        self.assertEqual(self.client.get(reverse("global_search_view"), {"search": "user someone"}).status_code, 200)

    # --- Plan limits --------------------------------------------------------
    def test_rfq_limit_is_enforced(self):
        from accounts.models import SubscriptionPlan
        SubscriptionPlan.objects.create(user_profile=self.buyer, plan_type="basic", price=0, rfq_limit="1")
        self.login(self.buyer)
        response = self.client.get(reverse("new-requirement"))
        self.assertRedirects(response, reverse("home") + "?upgrade=1", fetch_redirect_response=False)
        # POSTs are blocked too, not just the form page.
        self.assertEqual(self.client.post(reverse("new-requirement"), {}).status_code, 302)
        dashboard = self.client.get(reverse("home") + "?upgrade=1")
        self.assertContains(dashboard, "planModalOpen: true")
        self.assertContains(dashboard, "1 of 1 used this month")

    def test_unlimited_plan_is_not_limited(self):
        from accounts.models import SubscriptionPlan
        SubscriptionPlan.objects.create(user_profile=self.buyer, plan_type="enterprise", price=4999, rfq_limit="unlimited")
        self.login(self.buyer)
        self.assertEqual(self.client.get(reverse("new-requirement")).status_code, 200)

    # --- Currencies ---------------------------------------------------------
    def test_totals_are_kept_per_currency(self):
        from decimal import Decimal
        from marketplace.templatetags.custom_filters import money_totals
        totals = services.totals_by_currency([("INR", Decimal("100")), ("USD", Decimal("5")), ("INR", Decimal("50"))])
        self.assertEqual(totals, [{"currency": "INR", "amount": Decimal("150")}, {"currency": "USD", "amount": Decimal("5")}])
        self.assertEqual(money_totals(totals), "₹150 + $5")
        self.assertEqual(money_totals([]), "₹0")

    # --- Documents ----------------------------------------------------------
    def test_message_attachments_appear_in_documents_via_the_protected_link(self):
        thread = MessageThread.objects.create(requirement=self.requirement, supplier=self.supplier)
        message = Message.objects.create(
            thread=thread, sender=self.maker, body="", attachment=SimpleUploadedFile("spec.pdf", b"%PDF"),
            attachment_name="spec.pdf",
        )
        try:
            self.login(self.buyer)
            response = self.client.get(reverse("document-list"))
            self.assertContains(response, "spec.pdf")
            self.assertContains(response, reverse("message-attachment", kwargs={"pk": message.pk}))
        finally:
            message.attachment.delete(save=False)

    # --- Notification cache -------------------------------------------------
    def test_marking_read_clears_the_cached_bell_feed(self):
        thread = MessageThread.objects.create(requirement=self.requirement, supplier=self.supplier)
        Message.objects.create(thread=thread, sender=self.maker, body="Q?")
        feed = services.cached_notification_feed(self.buyer)
        question_key = next(e["key"] for e in feed if e["kind"] == "message")
        self.assertTrue(next(e for e in feed if e["key"] == question_key)["unread"])
        services.mark_notifications_read(self.buyer, [question_key])
        refreshed = services.cached_notification_feed(self.buyer)
        self.assertFalse(next(e for e in refreshed if e["key"] == question_key)["unread"])


@DASHBOARD_TEST_STORAGES
class QCChecklistTests(TestCase):
    def setUp(self):
        self.maker = User.objects.create_user(username="qcmaker", email="qcmaker@example.com", password="pass12345", role="manufacturer", first_name="Rahul", last_name="D")
        supplier = ManufacturerProfile.objects.create(
            user=self.maker, phone="8600000001", address="1 Rd", city="Pune", state="MH", country="India",
            amount_of_employees="10-20", turnover_per_year="<1", email="qcmaker@example.com",
        )
        self.buyer = User.objects.create_user(username="qcbuyer", email="qcbuyer@example.com", password="pass12345")
        consumer = ConsumerProfile.objects.create(
            user=self.buyer, Name="QC Buyer", type_of_business="electronics", city="Pune", state="MH", country="India",
            phone="9600000001", email="qcbuyer@example.com", EORI_number="EQC", VAT_number="VQC",
        )
        requirement = Requirement.objects.create(user=self.buyer, title="Shaft", rfq_desc="", quote_currency="INR", request_reason="other")
        quote = Quote.objects.create(requirement=requirement, supplier=supplier, quote_price="10.00", lead_time_value=5)
        self.order = Order.objects.create(requirement=requirement, quote=quote, supplier=supplier, customer=consumer)
        self.order.mark_quoted()
        self.order.select_quote()
        self.order.start_production()
        self.order.save()
        self.url = reverse("order-qc-toggle", kwargs={"billno": self.order.billno, "index": 0})

    def test_supplier_ticks_an_item_and_who_did_it_is_recorded(self):
        self.client.login(username="qcmaker@example.com", password="pass12345")
        self.client.post(self.url)
        self.order.refresh_from_db()
        item = self.order.qc_checklist[0]
        self.assertTrue(item["checked"])
        self.assertEqual(item["checked_by"], self.maker.pk)
        self.assertEqual(item["checked_by_name"], "Rahul D")
        self.client.post(self.url)
        self.order.refresh_from_db()
        self.assertFalse(self.order.qc_checklist[0]["checked"])

    def test_buyer_cannot_tick_items_and_bad_index_is_404(self):
        self.client.login(username="qcbuyer@example.com", password="pass12345")
        self.client.post(self.url)
        self.order.refresh_from_db()
        self.assertFalse(self.order.qc_checklist[0]["checked"])
        self.client.login(username="qcmaker@example.com", password="pass12345")
        bad = reverse("order-qc-toggle", kwargs={"billno": self.order.billno, "index": 9})
        self.assertEqual(self.client.post(bad).status_code, 404)


class MergeRetiredTablesMigrationTests(TransactionTestCase):
    """Runs migration 0010 against rows in the old tables and checks nothing is lost."""
    before = [('marketplace', '0009_add_order_event_and_qc_checklist')]
    after = [('marketplace', '0010_move_history_qc_and_questions')]

    def setUp(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old = executor.loader.project_state(self.before).apps

        OldUser = old.get_model('core', 'User')
        buyer = OldUser.objects.create(username="mig_buyer", email="mig_buyer@example.com", role="consumer")
        maker = OldUser.objects.create(username="mig_maker", email="mig_maker@example.com", role="manufacturer")
        consumer = old.get_model('accounts', 'ConsumerProfile').objects.create(
            user=buyer, Name="B", type_of_business="electronics", city="P", state="MH", country="India",
            phone="9700000001", email="mig_buyer@example.com", EORI_number="EM", VAT_number="VM",
        )
        supplier = old.get_model('accounts', 'ManufacturerProfile').objects.create(
            user=maker, phone="8700000001", address="1", city="P", state="MH", country="India",
            amount_of_employees="10-20", turnover_per_year="<1", email="mig_maker@example.com",
        )
        requirement = old.get_model('marketplace', 'Requirement').objects.create(
            user=buyer, title="R", rfq_desc="", quote_currency="INR", request_reason="other",
        )
        quote = old.get_model('marketplace', 'Quote').objects.create(requirement=requirement, supplier=supplier, quote_price="1")
        order = old.get_model('marketplace', 'Order').objects.create(
            requirement=requirement, quote=quote, supplier=supplier, customer=consumer, status="in_production",
        )
        self.old_time = timezone.now() - timedelta(days=30)
        StatusHistory = old.get_model('marketplace', 'OrderStatusHistory')
        status_row = StatusHistory.objects.create(order=order, from_status="quoted", to_status="quote_selected")
        StatusHistory.objects.filter(pk=status_row.pk).update(changed_at=self.old_time)
        stage_row = old.get_model('marketplace', 'ProductionStageHistory').objects.create(
            order=order, from_stage="order_confirmed", to_stage="material_received",
        )
        QC = old.get_model('marketplace', 'QCChecklistItem')
        QC.objects.create(order=order, label="Packaging inspected")
        QC.objects.create(order=order, label="First article inspection passed", is_checked=True, checked_at=self.old_time, checked_by=maker)
        question = old.get_model('marketplace', 'RequirementQuestion').objects.create(
            requirement=requirement, supplier=supplier, asked_by=maker, question="Black or clear?",
            answer="Black.", answered_at=self.old_time,
        )
        old.get_model('marketplace', 'RequirementQuestion').objects.filter(pk=question.pk).update(
            created_at=self.old_time - timedelta(days=1),
        )
        Read = old.get_model('marketplace', 'NotificationRead')
        Read.objects.create(user=buyer, key=f"order-status:{status_row.pk}")
        Read.objects.create(user=buyer, key=f"stage:{stage_row.pk}")
        Read.objects.create(user=buyer, key=f"question:{question.pk}")
        self.order_pk = order.pk

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.after)
        self.apps = executor.loader.project_state(self.after).apps

    def tearDown(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_data_is_moved_with_timestamps_and_read_state(self):
        OrderEventNew = self.apps.get_model('marketplace', 'OrderEvent')
        events = list(OrderEventNew.objects.filter(order_id=self.order_pk).order_by('kind'))
        self.assertEqual(
            [(e.kind, e.from_value, e.to_value) for e in events],
            [('stage', 'order_confirmed', 'material_received'), ('status', 'quoted', 'quote_selected')],
        )
        self.assertEqual(next(e for e in events if e.kind == 'status').changed_at, self.old_time)

        qc = self.apps.get_model('marketplace', 'Order').objects.get(pk=self.order_pk).qc_checklist
        self.assertEqual([i['label'] for i in qc], ["First article inspection passed", "Packaging inspected"])
        self.assertTrue(qc[0]['checked'])

        MessageNew = self.apps.get_model('marketplace', 'Message')
        self.assertEqual(list(MessageNew.objects.order_by('created_at').values_list('body', flat=True)), ["Black or clear?", "Black."])

        keys = set(self.apps.get_model('marketplace', 'NotificationRead').objects.values_list('key', flat=True))
        self.assertEqual(len(keys), 3)
        self.assertTrue(all(k.startswith(('order-event:', 'message:')) for k in keys))


@DASHBOARD_TEST_STORAGES
class LiveUpdateTests(TestCase):
    """htmx pollers: 204 when nothing changed, fresh markup when it has,
    286 (stop polling) once logged out, 404 for people who can't see it."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.buyer = User.objects.create_user(username="livebuyer", email="livebuyer@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=self.buyer, Name="Live Buyer", type_of_business="electronics", city="Pune", state="MH",
            country="India", phone="9800000001", email="livebuyer@example.com", EORI_number="EL", VAT_number="VL",
        )
        self.maker = User.objects.create_user(username="livemaker", email="livemaker@example.com", password="pass12345", role="manufacturer")
        self.supplier = ManufacturerProfile.objects.create(
            user=self.maker, companyname="Live Maker", phone="8800000001", address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="livemaker@example.com",
        )
        self.outsider = User.objects.create_user(username="liveout", email="liveout@example.com", password="pass12345")
        self.requirement = Requirement.objects.create(
            user=self.buyer, title="Live RFQ", rfq_desc="", quote_currency="INR", request_reason="other",
            end_date=timezone.now() + timedelta(days=5),
        )
        self.thread = MessageThread.objects.create(requirement=self.requirement, supplier=self.supplier)

    def login(self, user):
        self.client.login(username=user.email, password="pass12345")

    def htmx_get(self, url, **params):
        return self.client.get(url, params, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="http://testserver/")

    # --- topbar -----------------------------------------------------------
    def test_topbar_poll_is_204_until_something_changes(self):
        self.login(self.buyer)
        sig = self.client.get(reverse("home")).context["live_sig"]
        self.assertEqual(self.htmx_get(reverse("live-topbar"), sig=sig).status_code, 204)

        Quote.objects.create(requirement=self.requirement, supplier=self.supplier, quote_price="10.00")
        from django.core.cache import cache
        cache.clear()  # the bell feed is cached for a few seconds
        response = self.htmx_get(reverse("live-topbar"), sig=sig)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="notification-bell"')
        self.assertContains(response, "New quote on RFQ-")
        self.assertContains(response, 'hx-swap-oob="innerHTML:.live-badge-quotes"')
        self.assertNotContains(response, "live-badge-new-rfqs")  # manufacturer-only badge
        self.assertNotContains(response, f"sig={sig}")  # new poller carries the new signature

    def test_dashboard_pages_include_the_poller(self):
        self.login(self.buyer)
        self.assertContains(self.client.get(reverse("home")), 'id="live-topbar-poller"')

    def test_logged_out_pollers_are_told_to_stop(self):
        self.assertEqual(self.htmx_get(reverse("live-topbar")).status_code, 286)
        self.assertEqual(self.htmx_get(reverse("live-thread", kwargs={"pk": self.thread.pk})).status_code, 286)
        self.assertEqual(self.htmx_get(reverse("live-quotes", kwargs={"pk": self.requirement.pk})).status_code, 286)

    # --- message thread -----------------------------------------------------
    def test_thread_poll_delivers_new_messages_and_marks_them_read(self):
        self.login(self.buyer)
        page = self.client.get(reverse("message-thread", kwargs={"pk": self.thread.pk}))
        sig = page.context["message_sig"]
        url = reverse("live-thread", kwargs={"pk": self.thread.pk})
        self.assertEqual(self.htmx_get(url, sig=sig, closed="0").status_code, 204)

        Message.objects.create(thread=self.thread, sender=self.maker, body="Batch 2 is on the VMC now")
        response = self.htmx_get(url, sig=sig, closed="0")
        self.assertContains(response, "Batch 2 is on the VMC now")
        self.assertContains(response, 'id="thread-message-list"')
        self.assertNotContains(response, "<form method=\"post\" action=\"%s\"" % reverse("message-thread", kwargs={"pk": self.thread.pk}))
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.unread_count_for(self.buyer), 0)

    def test_thread_poll_reloads_the_page_when_the_conversation_is_closed(self):
        self.login(self.buyer)
        self.thread.close()
        response = self.htmx_get(reverse("live-thread", kwargs={"pk": self.thread.pk}), sig="x", closed="0")
        self.assertEqual(response["HX-Refresh"], "true")

    def test_thread_poll_is_404_for_outsiders(self):
        self.login(self.outsider)
        self.assertEqual(self.htmx_get(reverse("live-thread", kwargs={"pk": self.thread.pk}), sig="x").status_code, 404)

    # --- quotes ---------------------------------------------------------------
    def test_quotes_poll_shows_new_quotes(self):
        self.login(self.buyer)
        page = self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk}))
        self.assertContains(page, reverse("live-quotes", kwargs={"pk": self.requirement.pk}))
        sig = page.context["quotes_sig"]
        url = reverse("live-quotes", kwargs={"pk": self.requirement.pk})
        self.assertEqual(self.htmx_get(url, sig=sig).status_code, 204)

        Quote.objects.create(requirement=self.requirement, supplier=self.supplier, quote_price="99.00")
        response = self.htmx_get(url, sig=sig)
        self.assertContains(response, "Live Maker")
        self.assertContains(response, 'id="quotes-section"')

    def test_quotes_poll_stops_once_the_rfq_is_awarded(self):
        quote = Quote.objects.create(requirement=self.requirement, supplier=self.supplier, quote_price="99.00")
        self.login(self.buyer)
        response = self.client.post(
            reverse("quote-update-status", kwargs={"pk": quote.pk, "status": "Approved"}), HTTP_HX_REQUEST="true",
        )
        self.assertNotContains(response, reverse("live-quotes", kwargs={"pk": self.requirement.pk}))

    def test_quotes_poll_is_404_for_other_buyers(self):
        self.login(self.outsider)
        self.assertEqual(self.htmx_get(reverse("live-quotes", kwargs={"pk": self.requirement.pk}), sig="x").status_code, 404)


@DASHBOARD_TEST_STORAGES
class QuoteRejectAndRevisionTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.buyer = User.objects.create_user(username="revbuyer", email="revbuyer@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=self.buyer, Name="Rev Buyer", type_of_business="electronics", city="Pune", state="MH",
            country="India", phone="9900000001", email="revbuyer@example.com", EORI_number="ER", VAT_number="VR",
        )
        self.maker = User.objects.create_user(username="revmaker", email="revmaker@example.com", password="pass12345", role="manufacturer")
        self.supplier = ManufacturerProfile.objects.create(
            user=self.maker, companyname="Rev Maker", phone="8900000001", address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="revmaker@example.com",
        )
        self.other_buyer = User.objects.create_user(username="revother", email="revother@example.com", password="pass12345")
        self.requirement = Requirement.objects.create(
            user=self.buyer, title="Gear", rfq_desc="", quote_currency="INR", request_reason="other",
            end_date=timezone.now() + timedelta(days=5),
        )
        RequirementPart.objects.create(requirement=self.requirement, part_name="Gear", technology="Milling", Material="Aluminium", quantity=10)
        self.quote = Quote.objects.create(requirement=self.requirement, supplier=self.supplier, quote_price="100.00", lead_time_value=20)
        self.revise_url = reverse("quote-request-revision", kwargs={"pk": self.quote.pk})

    def login(self, user):
        self.client.login(username=user.email, password="pass12345")

    def test_rfq_page_shows_select_revise_and_reject_to_the_buyer(self):
        self.login(self.buyer)
        response = self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk}))
        self.assertContains(response, "Select this quote")
        self.assertContains(response, "Request revision")
        self.assertContains(response, reverse("quote-update-status", kwargs={"pk": self.quote.pk, "status": "Rejected"}))

    def test_reject_records_the_decision_and_notifies_the_supplier(self):
        self.login(self.buyer)
        self.client.post(reverse("quote-update-status", kwargs={"pk": self.quote.pk, "status": "Rejected"}))
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, "Rejected")
        self.assertIsNotNone(self.quote.decided_at)
        event = next(e for e in services.notification_feed(self.maker) if e["key"] == f"quote-decision:{self.quote.pk}")
        self.assertIn("not selected", event["title"])
        self.assertTrue(event["unread"])

    def test_losing_quotes_are_notified_when_another_is_awarded(self):
        rival_user = User.objects.create_user(username="revrival", email="revrival@example.com", password="pass12345", role="manufacturer")
        rival = ManufacturerProfile.objects.create(
            user=rival_user, companyname="Rival", phone="8900000002", address="2 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="revrival@example.com",
        )
        rival_quote = Quote.objects.create(requirement=self.requirement, supplier=rival, quote_price="90.00")
        self.login(self.buyer)
        self.client.post(reverse("quote-update-status", kwargs={"pk": self.quote.pk, "status": "Approved"}))
        winner = next(e for e in services.notification_feed(self.maker) if e["key"] == f"quote-decision:{self.quote.pk}")
        loser = next(e for e in services.notification_feed(rival_user) if e["key"] == f"quote-decision:{rival_quote.pk}")
        self.assertIn("awarded", winner["title"])
        self.assertIn("not selected", loser["title"])

    def test_revision_request_flow(self):
        self.login(self.buyer)
        self.client.post(self.revise_url, {"note": "Can you do 15 days?"})
        self.quote.refresh_from_db()
        self.assertTrue(self.quote.awaiting_revision)
        self.assertEqual(self.quote.revision_note, "Can you do 15 days?")
        # Waiting on the supplier, so it's not on the buyer's "to review" pile.
        self.assertFalse(services.buyer_pending_quotes(self.buyer).filter(pk=self.quote.pk).exists())

        feed = services.notification_feed(self.maker)
        event = next(e for e in feed if e["key"].startswith(f"quote-revision:{self.quote.pk}:"))
        self.assertEqual(event["detail"], "Can you do 15 days?")
        self.assertEqual(event["url"], reverse("edit-quote", kwargs={"pk": self.quote.pk}))

        self.login(self.maker)
        self.assertContains(self.client.get(reverse("edit-quote", kwargs={"pk": self.quote.pk})), "Can you do 15 days?")
        self.assertContains(self.client.get(reverse("quote-list")), "Revise quote")
        self.client.post(reverse("edit-quote", kwargs={"pk": self.quote.pk}), {
            "quote_price": "95", "tooling_cost": "0", "lead_time_value": "15", "lead_time_unit": "days",
            "payment_terms": "net_30", "note": "Revised", "action": "submit",
        })
        self.quote.refresh_from_db()
        self.assertFalse(self.quote.awaiting_revision)
        self.assertIsNotNone(self.quote.revised_at)
        self.assertEqual(self.quote.lead_time_value, 15)
        self.assertTrue(services.buyer_pending_quotes(self.buyer).filter(pk=self.quote.pk).exists())
        self.assertTrue(any(e["key"].startswith(f"quote-revised:{self.quote.pk}:") for e in services.notification_feed(self.buyer)))

    def test_saving_a_draft_does_not_answer_the_revision_request(self):
        self.quote.revision_requested_at = timezone.now()
        self.quote.revision_note = "Lower price"
        self.quote.save()
        self.login(self.maker)
        self.client.post(reverse("edit-quote", kwargs={"pk": self.quote.pk}), {
            "quote_price": "95", "tooling_cost": "0", "lead_time_value": "20", "lead_time_unit": "days",
            "payment_terms": "net_30", "note": "", "action": "draft",
        })
        self.quote.refresh_from_db()
        self.assertTrue(self.quote.awaiting_revision)

    def test_revision_request_guards(self):
        self.login(self.other_buyer)
        self.assertEqual(self.client.post(self.revise_url, {"note": "x"}).status_code, 404)
        self.login(self.buyer)
        self.assertEqual(self.client.get(self.revise_url).status_code, 405)
        self.client.post(self.revise_url, {"note": "   "})
        self.quote.refresh_from_db()
        self.assertFalse(self.quote.awaiting_revision)  # a note is required

        self.client.post(reverse("quote-update-status", kwargs={"pk": self.quote.pk, "status": "Rejected"}))
        self.client.post(self.revise_url, {"note": "Too late"})
        self.quote.refresh_from_db()
        self.assertFalse(self.quote.awaiting_revision)  # decided quotes can't be revised

    def test_quote_awaiting_revision_can_still_be_awarded(self):
        self.login(self.buyer)
        self.client.post(self.revise_url, {"note": "Better price?"})
        self.client.post(reverse("quote-update-status", kwargs={"pk": self.quote.pk, "status": "Approved"}))
        self.quote.refresh_from_db()
        self.assertTrue(self.quote.is_selected)
        self.assertFalse(self.quote.awaiting_revision)


@DASHBOARD_TEST_STORAGES
class AwardClosesRfqTests(TestCase):
    """At award: losing quoters are rejected and told; suppliers who never
    quoted lose the RFQ from their inbox and are told why; nobody can quote
    any more. Suppliers can turn down RFQs and revision requests without
    the buyer being notified."""

    def _maker(self, name, phone):
        user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", role="manufacturer")
        profile = ManufacturerProfile.objects.create(
            user=user, companyname=name.title(), phone=phone, address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email=f"{name}@example.com",
        )
        return user, profile

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.buyer = User.objects.create_user(username="awbuyer", email="awbuyer@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=self.buyer, Name="Aw Buyer", type_of_business="electronics", city="Pune", state="MH",
            country="India", phone="9910000001", email="awbuyer@example.com", EORI_number="EA", VAT_number="VA",
        )
        self.winner_user, self.winner = self._maker("awwinner", "8910000001")
        self.loser_user, self.loser = self._maker("awloser", "8910000002")
        self.silent_user, self.silent = self._maker("awsilent", "8910000003")
        self.requirement = Requirement.objects.create(
            user=self.buyer, title="Housing", rfq_desc="", quote_currency="INR", request_reason="other",
            end_date=timezone.now() + timedelta(days=5),
        )
        self.win_quote = Quote.objects.create(requirement=self.requirement, supplier=self.winner, quote_price="100")
        self.lose_quote = Quote.objects.create(requirement=self.requirement, supplier=self.loser, quote_price="120")

    def login(self, user):
        self.client.login(username=user.email, password="pass12345")

    def award(self):
        self.login(self.buyer)
        self.client.post(reverse("quote-update-status", kwargs={"pk": self.win_quote.pk, "status": "Approved"}))

    def test_suppliers_who_never_quoted_lose_the_rfq_and_are_told_why(self):
        inbox = lambda: list(self.client.get(reverse("requirement-list") + "?tab=new").context["object_list"])
        self.login(self.silent_user)
        self.assertIn(self.requirement, inbox())
        self.award()
        self.login(self.silent_user)
        self.assertNotIn(self.requirement, inbox())
        event = next(e for e in services.notification_feed(self.silent_user) if e["key"] == f"rfq-closed:{self.requirement.pk}")
        self.assertIn("awarded to another manufacturer", event["title"])
        self.assertTrue(event["unread"])
        page = self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk}))
        self.assertContains(page, "awarded to another manufacturer")
        self.assertContains(page, "closed to new quotes")

    def test_quoters_are_not_told_twice_and_see_their_outcome(self):
        self.award()
        loser_keys = [e["key"] for e in services.notification_feed(self.loser_user)]
        self.assertIn(f"quote-decision:{self.lose_quote.pk}", loser_keys)
        self.assertNotIn(f"rfq-closed:{self.requirement.pk}", loser_keys)
        self.assertNotIn(f"rfq-closed:{self.requirement.pk}", [e["key"] for e in services.notification_feed(self.winner_user)])
        self.lose_quote.refresh_from_db()
        self.assertEqual(self.lose_quote.status, "Rejected")
        self.login(self.loser_user)
        self.assertContains(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})), "Not selected")
        self.assertContains(self.client.get(reverse("requirement-list") + "?tab=lost"), "Housing")
        self.login(self.winner_user)
        self.assertContains(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})), "Awarded to you")

    def test_no_new_quotes_after_award(self):
        self.award()
        self.login(self.silent_user)
        response = self.client.get(reverse("new-quote", kwargs={"pk": self.requirement.pk}))
        self.assertRedirects(response, reverse("requirement", kwargs={"pk": self.requirement.pk}), fetch_redirect_response=False)
        self.client.post(reverse("new-quote", kwargs={"pk": self.requirement.pk}), {"quote_price": "10", "tooling_cost": "0", "lead_time_unit": "days"})
        self.assertFalse(Quote.objects.filter(supplier=self.silent).exists())

    def test_one_quote_per_supplier(self):
        self.login(self.loser_user)
        response = self.client.get(reverse("new-quote", kwargs={"pk": self.requirement.pk}))
        self.assertEqual(response.status_code, 302)

    def test_declined_rfqs_get_no_closed_notification(self):
        self.login(self.silent_user)
        self.client.post(reverse("requirement-decline", kwargs={"pk": self.requirement.pk}))
        self.award()
        self.assertNotIn(f"rfq-closed:{self.requirement.pk}", [e["key"] for e in services.notification_feed(self.silent_user)])

    def test_supplier_can_decline_an_rfq_from_its_page_without_notifying_the_buyer(self):
        self.login(self.silent_user)
        self.assertContains(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})), "Not interested")
        before = {e["key"] for e in services.notification_feed(self.buyer)}
        self.client.post(reverse("requirement-decline", kwargs={"pk": self.requirement.pk}))
        self.assertTrue(RFQDecline.objects.filter(requirement=self.requirement, supplier=self.silent).exists())
        self.assertEqual({e["key"] for e in services.notification_feed(self.buyer)}, before)

    def test_supplier_can_decline_a_revision_request_without_notifying_the_buyer(self):
        self.login(self.buyer)
        self.client.post(reverse("quote-request-revision", kwargs={"pk": self.lose_quote.pk}), {"note": "Cheaper?"})
        before = {e["key"] for e in services.notification_feed(self.buyer)}

        self.login(self.loser_user)
        self.client.post(reverse("quote-decline-revision", kwargs={"pk": self.lose_quote.pk}))
        self.lose_quote.refresh_from_db()
        self.assertFalse(self.lose_quote.awaiting_revision)
        self.assertTrue(self.lose_quote.revision_was_declined)
        self.assertEqual(self.lose_quote.quote_price, 120)
        self.assertEqual({e["key"] for e in services.notification_feed(self.buyer)}, before)
        # Back on the buyer's review pile, labelled.
        self.assertTrue(services.buyer_pending_quotes(self.buyer).filter(pk=self.lose_quote.pk).exists())
        self.login(self.buyer)
        self.assertContains(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})), "Original quote stands")

    def test_only_the_quoting_supplier_can_decline_its_revision_request(self):
        self.login(self.buyer)
        self.client.post(reverse("quote-request-revision", kwargs={"pk": self.lose_quote.pk}), {"note": "Cheaper?"})
        self.login(self.winner_user)
        self.assertEqual(self.client.post(reverse("quote-decline-revision", kwargs={"pk": self.lose_quote.pk})).status_code, 404)
        self.lose_quote.refresh_from_db()
        self.assertTrue(self.lose_quote.awaiting_revision)


@DASHBOARD_TEST_STORAGES
class RfqLifecycleChangeTests(TestCase):
    """Private supplier declines, overdue RFQs with no quotes, and buyer
    edits after quoting going through supplier confirmation."""

    def _maker(self, name, phone):
        user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", role="manufacturer")
        profile = ManufacturerProfile.objects.create(
            user=user, companyname=name.title(), phone=phone, address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email=f"{name}@example.com",
        )
        return user, profile

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.buyer = User.objects.create_user(username="chbuyer", email="chbuyer@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=self.buyer, Name="Ch Buyer", type_of_business="electronics", city="Pune", state="MH",
            country="India", phone="9920000001", email="chbuyer@example.com", EORI_number="EC", VAT_number="VC",
        )
        self.other_buyer = User.objects.create_user(username="chother", email="chother@example.com", password="pass12345")
        self.maker_user, self.maker = self._maker("chmaker", "8920000001")
        self.rival_user, self.rival = self._maker("chrival", "8920000002")
        self.requirement = Requirement.objects.create(
            user=self.buyer, title="Motor housing", rfq_desc="Aluminium", quote_currency="INR",
            request_reason="new_product", end_date=timezone.now() + timedelta(days=5), parts=1,
        )
        self.part = RequirementPart.objects.create(requirement=self.requirement, part_name="Housing", technology="Milling", Material="Aluminium", quantity=250)

    def login(self, user):
        self.client.login(username=user.email, password="pass12345")

    def edit_payload(self, **changes):
        data = {
            "title": self.requirement.title, "rfq_desc": self.requirement.rfq_desc, "quote_currency": "INR",
            "request_reason": "new_product", "nda_required": "False", "industry": "",
            "end_date": self.requirement.end_date.date().isoformat(),
            "requirement_parts-TOTAL_FORMS": "2", "requirement_parts-INITIAL_FORMS": "1",
            "requirement_parts-0-id": str(self.part.pk), "requirement_parts-0-part_name": "Housing",
            "requirement_parts-0-Part_desc": "", "requirement_parts-0-technology": "Milling",
            "requirement_parts-0-Material": "Aluminium", "requirement_parts-0-quantity": "250",
            # The blank "add a part" row the page always renders (quantity defaults to 1).
            "requirement_parts-1-quantity": "1",
        }
        data.update(changes)
        return data

    def edit(self, **changes):
        self.login(self.buyer)
        return self.client.post(reverse("edit-requirement", kwargs={"pk": self.requirement.pk}), self.edit_payload(**changes))

    def quote(self, supplier, price="100.00"):
        return Quote.objects.create(requirement=self.requirement, supplier=supplier, quote_price=price, lead_time_value=20, payment_terms="net_30")

    # --- 1. Declining before quoting stays private ------------------------------
    def test_a_supplier_declining_is_invisible_to_the_buyer(self):
        before = {e["key"] for e in services.notification_feed(self.buyer)}
        self.login(self.maker_user)
        self.client.post(reverse("requirement-decline", kwargs={"pk": self.requirement.pk}))
        self.assertEqual({e["key"] for e in services.notification_feed(self.buyer)}, before)
        self.login(self.buyer)
        self.assertNotContains(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})), "Chmaker")

    # --- 2. Overdue with no quotes ---------------------------------------------------
    def test_overdue_rfq_without_quotes_alerts_the_buyer(self):
        self.requirement.end_date = timezone.now() - timedelta(days=1)
        self.requirement.save()
        self.assertTrue(any(e["key"].startswith(f"rfq-expired:{self.requirement.pk}:") for e in services.notification_feed(self.buyer)))
        self.login(self.buyer)
        self.assertContains(self.client.get(reverse("home")), "No quotes by the due date")
        page = self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk}))
        self.assertContains(page, "Reopen with new date")
        self.assertContains(page, reverse("delete-requirement", kwargs={"pk": self.requirement.pk}))

    def test_overdue_rfq_with_quotes_is_not_flagged(self):
        self.quote(self.maker)
        self.requirement.end_date = timezone.now() - timedelta(days=1)
        self.requirement.save()
        self.assertFalse(any(e["key"].startswith("rfq-expired:") for e in services.notification_feed(self.buyer)))

    def test_moving_the_due_date_reopens_the_rfq_to_suppliers(self):
        self.requirement.end_date = timezone.now() - timedelta(days=1)
        self.requirement.save()
        self.assertFalse(services.open_requirements_for(self.maker).filter(pk=self.requirement.pk).exists())
        new_date = (timezone.now() + timedelta(days=10)).date()
        self.login(self.buyer)
        self.client.post(reverse("requirement-extend", kwargs={"pk": self.requirement.pk}), {"end_date": new_date.isoformat()})
        self.requirement.refresh_from_db()
        self.assertEqual(timezone.localtime(self.requirement.end_date).date(), new_date)
        self.assertTrue(services.open_requirements_for(self.maker).filter(pk=self.requirement.pk).exists())
        self.assertFalse(any(e["key"].startswith("rfq-expired:") for e in services.notification_feed(self.buyer)))

    def test_due_date_must_be_in_the_future_and_only_the_owner_can_move_it(self):
        url = reverse("requirement-extend", kwargs={"pk": self.requirement.pk})
        original = self.requirement.end_date
        self.login(self.buyer)
        self.client.post(url, {"end_date": (timezone.now() - timedelta(days=2)).date().isoformat()})
        self.requirement.refresh_from_db()
        self.assertEqual(self.requirement.end_date, original)
        self.login(self.other_buyer)
        self.assertEqual(self.client.post(url, {"end_date": "2099-01-01"}).status_code, 404)

    # --- 3. Editing after suppliers have quoted -------------------------------------
    def test_edit_without_quotes_applies_directly(self):
        self.edit(title="Motor housing v2")
        self.requirement.refresh_from_db()
        self.assertEqual(self.requirement.title, "Motor housing v2")
        self.assertFalse(RequirementAmendment.objects.exists())

    def test_edit_with_quotes_becomes_a_change_request_and_leaves_the_rfq_alone(self):
        quote = self.quote(self.maker)
        new_due = (timezone.now() + timedelta(days=9)).date()
        self.edit(**{"requirement_parts-0-quantity": "400", "quote_currency": "USD", "end_date": new_due.isoformat()})
        self.requirement.refresh_from_db()
        self.part.refresh_from_db()
        self.assertEqual(self.part.quantity, 250)
        self.assertEqual(self.requirement.quote_currency, "INR")
        # The due date doesn't affect pricing, so it applies immediately.
        self.assertEqual(timezone.localtime(self.requirement.end_date).date(), new_due)

        amendment = RequirementAmendment.objects.get()
        self.assertEqual(amendment.changes["parts"][str(self.part.pk)]["quantity"], {"old": 250, "new": 400})
        self.assertEqual(amendment.changes["requirement"]["quote_currency"], {"old": "INR", "new": "USD"})
        response = AmendmentResponse.objects.get()
        self.assertEqual(response.quote, quote)
        self.assertTrue(any(e["key"] == f"amendment:{response.pk}" for e in services.notification_feed(self.maker_user)))
        self.login(self.maker_user)
        page = self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk}))
        self.assertContains(page, "The buyer changed this RFQ")
        self.assertContains(page, "Accept &amp; send new pricing")

    def test_adding_parts_after_quotes_is_refused(self):
        self.quote(self.maker)
        response = self.edit(**{
            "requirement_parts-1-part_name": "Lid", "requirement_parts-1-technology": "Milling",
            "requirement_parts-1-Material": "Aluminium", "requirement_parts-1-quantity": "10",
        })
        self.assertContains(response, "adding parts")
        self.assertFalse(RequirementAmendment.objects.exists())
        self.assertEqual(self.requirement.requirement_parts.count(), 1)

    def test_supplier_rejects_and_the_buyer_is_told_why(self):
        self.quote(self.maker)
        self.edit(**{"requirement_parts-0-quantity": "400"})
        response = AmendmentResponse.objects.get()
        self.login(self.maker_user)
        self.client.post(reverse("amendment-respond", kwargs={"pk": response.pk}), {"action": "reject", "supplier_note": "Capacity is full"})
        response.refresh_from_db()
        self.assertEqual(response.status, AmendmentResponse.REJECTED)
        event = next(e for e in services.notification_feed(self.buyer) if e["key"] == f"amendment-response:{response.pk}")
        self.assertIn("rejected your changes", event["title"])
        self.assertEqual(event["detail"], "Capacity is full")
        self.part.refresh_from_db()
        self.assertEqual(self.part.quantity, 250)

    def test_accepting_new_pricing_applies_the_changes_and_awards_that_supplier(self):
        quote = self.quote(self.maker)
        rival_quote = self.quote(self.rival, price="110")
        self.edit(**{"requirement_parts-0-quantity": "400"})
        response = AmendmentResponse.objects.get(quote=quote)
        rival_response = AmendmentResponse.objects.get(quote=rival_quote)

        self.login(self.maker_user)
        self.client.post(reverse("amendment-respond", kwargs={"pk": response.pk}), {
            "action": "accept", "quote_price": "92.50", "tooling_cost": "500", "lead_time_value": "25",
            "lead_time_unit": "days", "payment_terms": "net_30", "supplier_note": "Volume discount",
        })
        response.refresh_from_db()
        self.assertEqual(response.status, AmendmentResponse.ACCEPTED)
        self.assertTrue(any(e["key"] == f"amendment-response:{response.pk}" for e in services.notification_feed(self.buyer)))
        # Nothing has changed yet.
        quote.refresh_from_db()
        self.part.refresh_from_db()
        self.assertEqual(quote.quote_price, 100)
        self.assertEqual(self.part.quantity, 250)

        self.login(self.buyer)
        page = self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk}))
        self.assertContains(page, "Accept &amp; award to this supplier")
        self.client.post(reverse("amendment-decide", kwargs={"pk": response.pk}), {"decision": "accept"})
        quote.refresh_from_db()
        self.part.refresh_from_db()
        self.requirement.refresh_from_db()
        self.assertEqual(self.part.quantity, 400)
        self.assertEqual(quote.quote_price, Decimal("92.50"))
        self.assertEqual(quote.lead_time_value, 25)
        self.assertEqual(RequirementAmendment.objects.get().status, RequirementAmendment.APPLIED)
        event = next(e for e in services.notification_feed(self.maker_user) if e["key"] == f"amendment-decision:{response.pk}")
        self.assertIn("awarded you the RFQ", event["title"])

        # Accepting the new pricing is choosing this supplier.
        self.assertTrue(quote.is_selected)
        self.assertEqual(self.requirement.status, "Approved")
        order = Order.objects.get(requirement=self.requirement)
        self.assertEqual((order.quote, order.status), (quote, "quote_selected"))
        self.assertEqual(order.quote.get_breakdown()["quantity"], 400)
        rival_quote.refresh_from_db()
        rival_response.refresh_from_db()
        self.assertEqual(rival_quote.status, "Rejected")
        self.assertEqual(rival_response.status, AmendmentResponse.CLOSED)
        rival_event = next(e for e in services.notification_feed(self.rival_user) if e["key"] == f"quote-decision:{rival_quote.pk}")
        self.assertIn("not selected", rival_event["title"])
        self.login(self.rival_user)
        self.assertNotIn(self.requirement, list(self.client.get(reverse("requirement-list") + "?tab=new").context["object_list"]))
        self.assertContains(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})), "Not selected")
        self.login(self.buyer)
        # History is visible to both sides.
        self.assertContains(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})), "Change history")
        self.login(self.maker_user)
        self.assertContains(self.client.get(reverse("requirement", kwargs={"pk": self.requirement.pk})), "Buyer accepted the new pricing")

    def test_buyer_can_keep_the_original_details(self):
        quote = self.quote(self.maker)
        self.edit(**{"requirement_parts-0-quantity": "400"})
        response = AmendmentResponse.objects.get()
        self.login(self.maker_user)
        self.client.post(reverse("amendment-respond", kwargs={"pk": response.pk}), {
            "action": "accept", "quote_price": "80", "tooling_cost": "0", "lead_time_unit": "days",
        })
        self.login(self.buyer)
        self.client.post(reverse("amendment-decide", kwargs={"pk": response.pk}), {"decision": "keep"})
        quote.refresh_from_db()
        self.part.refresh_from_db()
        self.assertEqual(quote.quote_price, 100)
        self.assertEqual(self.part.quantity, 250)
        response.refresh_from_db()
        self.assertEqual(response.status, AmendmentResponse.BUYER_DECLINED)
        self.assertTrue(any(e["key"] == f"amendment-decision:{response.pk}" for e in services.notification_feed(self.maker_user)))

    def test_no_new_edits_while_a_change_request_is_open_and_withdraw_closes_it(self):
        self.quote(self.maker)
        self.edit(**{"requirement_parts-0-quantity": "400"})
        self.login(self.buyer)
        response = self.client.get(reverse("edit-requirement", kwargs={"pk": self.requirement.pk}))
        self.assertEqual(response.status_code, 302)
        amendment = RequirementAmendment.objects.get()
        self.client.post(reverse("amendment-withdraw", kwargs={"pk": amendment.pk}))
        amendment.refresh_from_db()
        self.assertEqual(amendment.status, RequirementAmendment.WITHDRAWN)
        self.assertEqual(AmendmentResponse.objects.get().status, AmendmentResponse.CLOSED)
        self.assertEqual(self.client.get(reverse("edit-requirement", kwargs={"pk": self.requirement.pk})).status_code, 200)

    def test_awarding_closes_an_open_change_request(self):
        quote = self.quote(self.maker)
        self.edit(**{"requirement_parts-0-quantity": "400"})
        self.login(self.buyer)
        self.client.post(reverse("quote-update-status", kwargs={"pk": quote.pk, "status": "Approved"}))
        self.assertEqual(RequirementAmendment.objects.get().status, RequirementAmendment.CLOSED)
        self.assertEqual(AmendmentResponse.objects.get().status, AmendmentResponse.CLOSED)

    def test_only_the_quoting_supplier_and_the_rfq_owner_can_act(self):
        self.quote(self.maker)
        self.quote(self.rival, price="110")
        self.edit(**{"requirement_parts-0-quantity": "400"})
        maker_response = AmendmentResponse.objects.get(quote__supplier=self.maker)
        self.login(self.rival_user)
        self.assertEqual(self.client.post(reverse("amendment-respond", kwargs={"pk": maker_response.pk}), {"action": "reject"}).status_code, 404)
        self.login(self.maker_user)
        self.client.post(reverse("amendment-respond", kwargs={"pk": maker_response.pk}), {
            "action": "accept", "quote_price": "80", "tooling_cost": "0", "lead_time_unit": "days",
        })
        self.login(self.other_buyer)
        self.assertEqual(self.client.post(reverse("amendment-decide", kwargs={"pk": maker_response.pk}), {"decision": "accept"}).status_code, 404)


@DASHBOARD_TEST_STORAGES
class SupplierReviewTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(username="revbuyer", email="revbuyer@example.com", password="pass12345")
        self.consumer = ConsumerProfile.objects.create(
            user=self.buyer, Name="Rev Buyer", type_of_business="electronics", city="Pune", state="MH", country="India",
            phone="9700000001", email="revbuyer@example.com", EORI_number="ERV", VAT_number="VRV",
        )
        self.maker = User.objects.create_user(username="revmaker", email="revmaker@example.com", password="pass12345", role="manufacturer")
        self.supplier = ManufacturerProfile.objects.create(
            user=self.maker, companyname="Rev Maker", phone="8700000001", address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="revmaker@example.com",
        )
        self.order = self.make_order("Bracket")
        self.url = reverse("order-review", kwargs={"billno": self.order.billno})

    def make_order(self, title, complete=True):
        requirement = Requirement.objects.create(user=self.buyer, title=title, rfq_desc="", quote_currency="INR", request_reason="other")
        quote = Quote.objects.create(requirement=requirement, supplier=self.supplier, quote_price="10.00", lead_time_value=5)
        order = Order.objects.create(requirement=requirement, quote=quote, supplier=self.supplier, customer=self.consumer)
        steps = [order.mark_quoted, order.select_quote, order.start_production]
        if complete:
            steps += [order.request_payment, order.mark_paid, order.complete]
        for step in steps:
            step()
        order.save()
        return order

    def login(self, user):
        self.client.login(username=user.email, password="pass12345")

    def test_buyer_rates_a_completed_order_once(self):
        self.login(self.buyer)
        page = self.client.get(reverse("order-detail", kwargs={"billno": self.order.billno}))
        self.assertContains(page, "Submit rating")
        self.client.post(self.url, {"rating": "4", "comment": "On time, good finish"})
        review = SupplierReview.objects.get(order=self.order)
        self.assertEqual((review.rating, review.comment), (4, "On time, good finish"))
        self.client.post(self.url, {"rating": "1"})
        self.assertEqual(SupplierReview.objects.get(order=self.order).rating, 4)
        page = self.client.get(reverse("order-detail", kwargs={"billno": self.order.billno}))
        self.assertNotContains(page, "Submit rating")
        self.assertContains(page, "On time, good finish")

    def test_only_the_buyer_can_rate_and_only_after_completion(self):
        self.login(self.maker)
        self.assertEqual(self.client.post(self.url, {"rating": "5"}).status_code, 404)
        open_order = self.make_order("Open one", complete=False)
        self.login(self.buyer)
        self.client.post(reverse("order-review", kwargs={"billno": open_order.billno}), {"rating": "5"})
        self.client.post(self.url, {"rating": "6"})
        self.assertFalse(SupplierReview.objects.exists())

    def test_rating_is_an_action_item_until_given(self):
        titles = [item["title"] for item in services.buyer_action_items(self.buyer)]
        self.assertTrue(any(t.startswith("Rate Rev Maker") for t in titles))
        SupplierReview.objects.create(order=self.order, rating=5)
        titles = [item["title"] for item in services.buyer_action_items(self.buyer)]
        self.assertFalse(any(t.startswith("Rate Rev Maker") for t in titles))

    def test_average_and_count_show_in_directory_and_on_quote_cards(self):
        SupplierReview.objects.create(order=self.order, rating=4)
        SupplierReview.objects.create(order=self.make_order("Second"), rating=5)
        self.login(self.buyer)
        self.assertContains(self.client.get(reverse("supplier-directory")), "(2 reviews)")
        supplier = services.with_ratings(ManufacturerProfile.objects.filter(pk=self.supplier.pk)).get()
        self.assertEqual((supplier.rating_avg, supplier.rating_count), (4.5, 2))

        requirement = Requirement.objects.create(
            user=self.buyer, title="New part", rfq_desc="", quote_currency="INR", request_reason="other",
            end_date=timezone.now() + timedelta(days=5),
        )
        Quote.objects.create(requirement=requirement, supplier=self.supplier, quote_price="12.00")
        page = self.client.get(reverse("requirement", kwargs={"pk": requirement.pk}))
        self.assertContains(page, "4.5")
        self.assertContains(page, "(2 reviews)")

    def test_unrated_supplier_shows_no_reviews(self):
        supplier = services.with_ratings(ManufacturerProfile.objects.filter(pk=self.supplier.pk)).get()
        self.assertEqual((supplier.rating_avg, supplier.rating_count), (None, 0))
        self.login(self.buyer)
        self.assertContains(self.client.get(reverse("supplier-directory")), "No reviews yet")

    def test_profile_pages_show_ratings_without_comments(self):
        SupplierReview.objects.create(order=self.order, rating=5, comment="Secret comment")
        SupplierReview.objects.create(order=self.make_order("Second"), rating=3)
        breakdown = services.rating_breakdown(self.supplier)
        self.assertEqual((breakdown["avg"], breakdown["count"]), (4.0, 2))
        self.assertEqual([row["count"] for row in breakdown["stars"]], [1, 0, 1, 0, 0])

        self.login(self.buyer)
        page = self.client.get(reverse("supplier", kwargs={"pk": self.supplier.pk}))
        self.assertContains(page, "2 ratings from completed orders")
        self.assertNotContains(page, "Secret comment")
        self.login(self.maker)
        page = self.client.get(reverse("company-profile"))
        self.assertContains(page, "2 ratings from completed orders")
        self.assertNotContains(page, "Secret comment")
