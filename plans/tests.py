"""Plans (plans.catalog) and how they're applied (plans.access and friends):
role, then plan feature, then usage limit."""
import os
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import team
from accounts.models import ManufacturingTech, SubscriptionPlan, TeamMember
from marketplace import emails, services
from marketplace.models import Quote, QuoteTemplate, Requirement, RequirementPart
from marketplace.test_teams import PASSWORD, QUOTE_DATA, add_member, make_buyer, make_supplier
from plans import access, catalog, export, rfq_inbox, storage
from plans.models import RFQReceipt, StoredFile

MEDIA = tempfile.mkdtemp()
TEST_SETTINGS = override_settings(
    MEDIA_ROOT=MEDIA,
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    },
)


def on_plan(user, plan):
    SubscriptionPlan.objects.update_or_create(user_profile=user, defaults={'plan_type': plan, 'price': catalog.price(plan)})


@TEST_SETTINGS
class PlanTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.owner, self.buyer = make_buyer("pbuyer", "9500000001")
        self.sowner, self.supplier = make_supplier("pmaker", "9500000002")
        milling = ManufacturingTech.objects.create(technology_type='milling')
        self.supplier.capabilities.add(milling)
        self.supplier.accepting_rfqs = True
        self.supplier.save()

    def login(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.email, password=PASSWORD))

    def rfq(self, title="Bracket", created_ago=None):
        requirement = Requirement.objects.create(
            user=self.owner, title=title, rfq_desc="", quote_currency="INR", request_reason="other",
            end_date=timezone.now() + timedelta(days=10),
        )
        RequirementPart.objects.create(requirement=requirement, part_name=title, technology="Milling", Material="Aluminium", quantity=10)
        return requirement


@TEST_SETTINGS
class CatalogTests(TestCase):
    def test_prices_and_limits_match_the_published_plans(self):
        self.assertEqual([catalog.price(p) for p in catalog.PLAN_ORDER], [0, 999, 2999])
        self.assertEqual([catalog.price(p, catalog.YEARLY) for p in catalog.PLAN_ORDER], [0, 9990, 29990])
        self.assertEqual([catalog.limit('buyer', p, catalog.USERS) for p in catalog.PLAN_ORDER], [2, 5, 15])
        self.assertEqual([catalog.limit('buyer', p, catalog.RFQS_PER_MONTH) for p in catalog.PLAN_ORDER], [5, 25, 100])
        self.assertEqual([catalog.limit('supplier', p, catalog.RFQS_RECEIVED_PER_MONTH) for p in catalog.PLAN_ORDER], [10, 50, 200])
        self.assertEqual([catalog.limit('supplier', p, catalog.QUOTES_PER_MONTH) for p in catalog.PLAN_ORDER], [10, 50, 200])
        self.assertEqual([catalog.limit('buyer', p, catalog.STORAGE_BYTES) // catalog.GB for p in catalog.PLAN_ORDER], [1, 5, 25])

    def test_pricing_tables_read_like_the_published_ones(self):
        buyer = dict(catalog.pricing_table('buyer'))
        self.assertEqual(buyer['Supplier directory'], ['Basic', 'Full', 'Full'])
        self.assertEqual(buyer['Approval workflows'], ['—', '—', '✓'])
        self.assertEqual(buyer['Audit logs'], ['Basic', '✓', 'Advanced'])
        self.assertEqual(buyer['Advanced quote comparison'], ['—', '—', '✓'])
        supplier = dict(catalog.pricing_table('supplier'))
        self.assertEqual(supplier['Supplier directory listing'], ['Basic', 'Full', 'Priority'])
        self.assertEqual(supplier['Supplier search visibility'], ['Basic', 'Enhanced', 'Priority'])
        self.assertEqual(supplier['Supplier matching'], ['—', '✓', 'Advanced'])
        self.assertEqual(supplier['File storage'], ['1 GB', '5 GB', '25 GB'])

    def test_pricing_page_shows_both_catalogues(self):
        page = self.client.get(reverse("pricing"))
        for text in ("Free", "Starter", "Business", "&#8377;999", "&#8377;29990", "For Manufacturers",
                     "Relevant RFQs received / month", "Approval workflows", "Performance insights"):
            self.assertContains(page, text)


class AccessCheckTests(PlanTestCase):
    def test_role_is_checked_before_plan_and_plan_before_limit(self):
        viewer = add_member(self.buyer, "pview", TeamMember.VIEWER)
        decision = access.check(viewer, 'rfq.create')
        self.assertFalse(decision)
        self.assertIn("Your role (Viewer)", decision.reason)
        decision = access.check(self.owner, 'export')  # Free has no export
        self.assertEqual((decision.allowed, decision.upgrade_to), (False, 'starter'))
        for i in range(5):
            self.rfq(f"Part {i}")
        decision = access.check(self.owner, 'rfq.create')
        self.assertFalse(decision)
        self.assertIn("all 5 RFQs", decision.reason)
        self.assertEqual(decision.upgrade_to, 'starter')
        on_plan(self.owner, 'starter')
        team.forget(self.owner)  # a new request would load the plan afresh
        self.assertTrue(access.check(self.owner, 'rfq.create'))
        self.assertTrue(access.check(self.owner, 'export'))

    def test_an_inactive_subscription_is_free(self):
        SubscriptionPlan.objects.create(user_profile=self.owner, plan_type='business', price=2999, is_active=False)
        self.assertEqual(access.plan_of(self.buyer), 'free')

    def test_usage_rows_on_the_dashboard(self):
        self.login(self.owner)
        page = self.client.get(reverse("home"))
        self.assertContains(page, "RFQs posted this month")
        self.assertContains(page, "File storage")


class SupplierRFQLimitTests(PlanTestCase):
    def test_inbox_receives_up_to_the_monthly_quota_best_match_first(self):
        for i in range(12):
            self.rfq(f"Part {i}")
        self.login(self.sowner)
        page = self.client.get(reverse("requirement-list"))
        self.assertEqual(RFQReceipt.objects.filter(supplier=self.supplier).count(), 10)  # Free: 10 a month
        self.assertContains(page, "2 more open RFQs are waiting")
        locked = Requirement.objects.exclude(receipts__supplier=self.supplier).first()
        response = self.client.get(reverse("requirement", kwargs={"pk": locked.pk}))
        self.assertEqual(response.status_code, 402)
        self.assertEqual(self.client.get(reverse("new-quote", kwargs={"pk": locked.pk})).status_code, 402)
        received = Requirement.objects.filter(receipts__supplier=self.supplier).first()
        self.assertEqual(self.client.get(reverse("requirement", kwargs={"pk": received.pk})).status_code, 200)

    def test_opening_a_link_receives_it_while_there_is_quota(self):
        requirement = self.rfq()
        self.login(self.sowner)
        self.assertEqual(self.client.get(reverse("requirement", kwargs={"pk": requirement.pk})).status_code, 200)
        self.assertTrue(rfq_inbox.is_received(self.supplier, requirement))

    def test_alerts_follow_the_plan(self):
        on_plan(self.sowner, 'starter')  # instant alerts
        requirement = self.rfq()
        mail.outbox.clear()
        emails.notify_new_rfq(requirement)
        self.assertEqual([m.to[0] for m in mail.outbox], ["pmaker@example.com"])
        self.assertTrue(rfq_inbox.is_received(self.supplier, requirement))

        on_plan(self.sowner, 'free')  # daily digest instead
        access.forget_plan(self.supplier)
        second = self.rfq("Flange")
        mail.outbox.clear()
        emails.notify_new_rfq(second)
        self.assertEqual(mail.outbox, [])
        call_command('send_rfq_digests', stdout=open(os.devnull, 'w'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Flange", mail.outbox[0].body)

    def test_business_alerts_respect_preferences(self):
        on_plan(self.sowner, 'business')
        self.login(self.sowner)
        self.client.post(reverse("rfq-alert-preferences"), {"materials": ["Steel"]})
        requirement = self.rfq()  # aluminium
        mail.outbox.clear()
        emails.notify_new_rfq(requirement)
        self.assertEqual(mail.outbox, [])

    def test_alert_preferences_need_business(self):
        self.login(self.sowner)
        self.assertEqual(self.client.get(reverse("rfq-alert-preferences")).status_code, 402)

    def test_quote_submissions_are_limited_and_overflow_is_kept_as_a_draft(self):
        for i in range(10):
            Quote.objects.create(requirement=self.rfq(f"Old {i}"), supplier=self.supplier, quote_price=Decimal('5'), submitted_at=timezone.now())
        requirement = self.rfq("One more")
        self.login(self.sowner)
        self.client.post(reverse("new-quote", kwargs={"pk": requirement.pk}), QUOTE_DATA)
        quote = Quote.objects.get(requirement=requirement)
        self.assertTrue(quote.is_draft)
        self.assertIsNone(quote.submitted_at)


class StorageTests(PlanTestCase):
    def test_uploads_are_recorded_and_the_limit_is_enforced(self):
        requirement = self.rfq()
        requirement.file = SimpleUploadedFile("drawing.pdf", b"%PDF-1.4 " + b"x" * 991)
        requirement.save()
        self.assertEqual(storage.used_bytes(self.buyer), 1000)
        requirement.file = None
        requirement.save()
        self.assertEqual(storage.used_bytes(self.buyer), 0)

        StoredFile.objects.create(
            buyer=self.buyer, content_type_id=1, object_id=999, field='file', name='big', size=catalog.GB - 10,
        )
        self.login(self.owner)
        response = self.client.post(
            reverse("message-thread-start", kwargs={"requirement_pk": requirement.pk, "supplier_pk": self.supplier.pk}),
            {"body": "hi", "attachment": SimpleUploadedFile("a.pdf", b"%PDF-1.4 " + b"x" * 100)},
            HTTP_REFERER=reverse("requirement-list"),
        )
        self.assertRedirects(response, reverse("requirement-list"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("requirement-list")), "file storage")


class FeatureGateTests(PlanTestCase):
    def test_buyer_reports_need_business(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("reports")).status_code, 402)
        on_plan(self.owner, 'business')
        self.assertEqual(self.client.get(reverse("reports")).status_code, 200)

    def test_supplier_reports_by_plan_and_role(self):
        ops = add_member(self.supplier, "pops", TeamMember.OPERATIONS)
        self.login(self.sowner)
        self.assertEqual(self.client.get(reverse("reports")).status_code, 402)  # Free
        on_plan(self.sowner, 'starter')
        page = self.client.get(reverse("reports"))
        self.assertContains(page, "Win rate")
        self.assertNotContains(page, "Average rating by month")
        self.login(ops)  # operations analytics need Business
        self.assertEqual(self.client.get(reverse("reports")).status_code, 402)
        on_plan(self.sowner, 'business')
        page = self.client.get(reverse("reports"))
        self.assertContains(page, "Average rating by month")
        self.assertNotContains(page, "Quotes by month")

    def test_documents_page_needs_full_invoice_management(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("document-list")).status_code, 402)
        on_plan(self.owner, 'starter')
        self.assertEqual(self.client.get(reverse("document-list")).status_code, 200)

    def test_csv_export_needs_starter_and_neutralises_formulas(self):
        self.rfq("=HYPERLINK(\"evil\")")
        self.login(self.owner)
        response = self.client.get(reverse("requirement-list") + "?export=csv")
        self.assertEqual(response.status_code, 302)
        on_plan(self.owner, 'starter')
        response = self.client.get(reverse("requirement-list") + "?export=csv")
        body = b"".join(response.streaming_content).decode('utf-8-sig')
        self.assertIn("'=HYPERLINK", body)
        self.assertEqual(export._safe("@cmd"), "'@cmd")

    def test_order_filters_need_starter(self):
        order, _ = services.award_quote(self.rfq(), Quote.objects.create(requirement=self.rfq("x"), supplier=self.supplier, quote_price=Decimal('1')))
        self.login(self.owner)
        self.assertNotContains(self.client.get(reverse("orders-list")), 'name="status"')
        on_plan(self.owner, 'starter')
        self.assertContains(self.client.get(reverse("orders-list")), 'name="status"')

    def test_directory_filters_and_supplier_visibility(self):
        other_user, other = make_supplier("pstar", "9500000003")
        other.capabilities.add(ManufacturingTech.objects.get(technology_type='milling'))
        on_plan(other_user, 'starter')
        self.login(self.owner)  # Free buyer: filters ignored
        page = self.client.get(reverse("supplier-directory") + "?process=milling")
        self.assertContains(page, "part of the Starter plan")
        self.assertContains(page, "pmaker Works")
        on_plan(self.owner, 'starter')  # filters work; Free suppliers aren't in filtered results
        page = self.client.get(reverse("supplier-directory") + "?process=milling")
        self.assertContains(page, "pstar Works")
        self.assertNotContains(page, "pmaker Works")
        on_plan(self.sowner, 'business')  # featured and first
        page = self.client.get(reverse("supplier-directory"))
        self.assertContains(page, "Featured manufacturers")
        self.assertContains(page, "Top supplier")

    def test_supplier_profile_tiers(self):
        self.supplier.machines.create(machine_type="SECRET-MACHINE")
        self.login(self.owner)
        url = reverse("supplier", kwargs={"pk": self.supplier.pk})
        self.assertNotContains(self.client.get(url), "SECRET-MACHINE")  # Basic profile
        on_plan(self.sowner, 'starter')
        self.assertContains(self.client.get(url), "SECRET-MACHINE")

    def test_contacts_details_need_starter(self):
        services.award_quote(self.rfq(), Quote.objects.create(requirement=self.rfq("y"), supplier=self.supplier, quote_price=Decimal('1')))
        self.login(self.sowner)
        page = self.client.get(reverse("contacts"))
        self.assertContains(page, "pbuyer Co")
        self.assertNotContains(page, "pbuyer@example.com")
        on_plan(self.sowner, 'starter')
        self.assertContains(self.client.get(reverse("contacts")), "pbuyer@example.com")

    def test_quote_templates_and_bulk_withdraw(self):
        on_plan(self.sowner, 'business')
        requirement = self.rfq()
        self.login(self.sowner)
        self.client.post(reverse("new-quote", kwargs={"pk": requirement.pk}), {**QUOTE_DATA, "action": "draft", "save_as_template": "Standard"})
        template = QuoteTemplate.objects.get(supplier=self.supplier, name="Standard")
        second = self.rfq("Second")
        page = self.client.get(reverse("new-quote", kwargs={"pk": second.pk}) + f"?template={template.pk}")
        self.assertContains(page, "Start from a template")
        draft = Quote.objects.get(requirement=requirement)
        self.client.post(reverse("quotes-bulk"), {"quote": [draft.pk]})
        draft.refresh_from_db()
        self.assertTrue(draft.is_deleted)

    def test_buyer_business_comparison_and_suggestions(self):
        requirement = self.rfq()
        for prefix, phone, price in (("pcmp1", "9500000011", 10), ("pcmp2", "9500000012", 12)):
            _, rival = make_supplier(prefix, phone)
            Quote.objects.create(requirement=requirement, supplier=rival, quote_price=Decimal(price), submitted_at=timezone.now())
        self.login(self.owner)
        url = reverse("requirement", kwargs={"pk": requirement.pk})
        self.assertNotContains(self.client.get(url), "Side-by-side comparison")
        on_plan(self.owner, 'business')
        page = self.client.get(url)
        self.assertContains(page, "Side-by-side comparison")
        self.assertContains(page, "Suggested manufacturers")
        self.assertContains(page, "pmaker Works")

    def test_storage_ledger_can_be_rebuilt(self):
        requirement = self.rfq()
        Requirement.objects.filter(pk=requirement.pk).update(file='requirement_files/x.pdf')
        os.makedirs(os.path.join(MEDIA, 'requirement_files'), exist_ok=True)
        with open(os.path.join(MEDIA, 'requirement_files', 'x.pdf'), 'wb') as handle:
            handle.write(b"x" * 42)
        call_command('rebuild_storage_ledger', stdout=open(os.devnull, 'w'))
        self.assertEqual(storage.used_bytes(self.buyer), 42)
