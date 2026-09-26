from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import User
from accounts.models import ManufacturerProfile
from marketplace.models import Requirement, RequirementPart, Quote, Order

DASHBOARD_TEST_STORAGES = override_settings(
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
)


@override_settings(
    # landing.html uses {% static %}; the manifest storage used in prod
    # requires a `collectstatic` run this dev environment hasn't done
    # (unrelated pre-existing gap). Plain StaticFilesStorage needs no
    # manifest, so tests aren't coupled to that.
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
)
class LoginViewTests(TestCase):
    """
    A failed login used to re-render the form in place (200), leaving the
    browser on a POST response — refreshing that page pops a browser
    "Confirm Form Resubmission" prompt. Login should follow Post/Redirect/Get
    instead so a refresh after a failed attempt is just a normal GET.
    """

    def setUp(self):
        User.objects.create_user(username="realuser", email="real@example.com", password="correct-horse-battery")
        self.url = reverse("login")

    def test_invalid_login_redirects_instead_of_rerendering(self):
        response = self.client.post(self.url, data={"username": "real@example.com", "password": "wrong-password"})
        self.assertRedirects(response, self.url)

    def test_invalid_login_surfaces_error_via_messages(self):
        response = self.client.post(
            self.url,
            data={"username": "real@example.com", "password": "wrong-password"},
            follow=True,
        )
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Incorrect email or password" in m for m in messages))

    def test_refresh_after_failed_login_is_a_plain_get(self):
        self.client.post(self.url, data={"username": "real@example.com", "password": "wrong-password"})
        # Simulates the browser refresh: a plain GET, not a resubmitted POST.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_valid_login_still_works(self):
        response = self.client.post(self.url, data={"username": "real@example.com", "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


@DASHBOARD_TEST_STORAGES
class ManufacturerDashboardStatsTests(TestCase):
    """The dashboard's 'New RFQs matched' count used to come from a looser
    filter than the RFQ inbox's own 'New' tab (no expiry check, no
    already-quoted exclusion) — the two screens could disagree on how many
    RFQs were 'open'. Both now share marketplace.services.open_requirements_for."""

    def setUp(self):
        from django.utils import timezone
        from datetime import timedelta

        self.manufacturer_user = User.objects.create_user(
            username="dashstats", email="dashstats@example.com", password="pass12345", role="manufacturer",
        )
        self.manufacturer = ManufacturerProfile.objects.create(
            user=self.manufacturer_user, phone="7000000010", address="1 Rd", city="Pune",
            state="MH", country="India", amount_of_employees="10-20", turnover_per_year="<1",
            email="dashstats@example.com",
        )
        self.buyer = User.objects.create_user(username="dashbuyer", email="dashbuyer@example.com", password="pass12345")

        self.open_requirement = Requirement.objects.create(
            user=self.buyer, title="Open RFQ", rfq_desc="", quote_currency="USD", request_reason="other",
            end_date=timezone.now() + timedelta(days=5),
        )
        RequirementPart.objects.create(requirement=self.open_requirement, part_name="P", technology="Milling", Material="Aluminium", quantity=5)

        self.expired_requirement = Requirement.objects.create(
            user=self.buyer, title="Expired RFQ", rfq_desc="", quote_currency="USD", request_reason="other",
            end_date=timezone.now() - timedelta(days=1),
        )
        RequirementPart.objects.create(requirement=self.expired_requirement, part_name="P", technology="Milling", Material="Aluminium", quantity=5)

        self.client.login(username="dashstats@example.com", password="pass12345")

    def test_dashboard_renders(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open RFQ")

    def test_expired_rfq_excluded_from_new_count(self):
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "Expired RFQ")


@DASHBOARD_TEST_STORAGES
class PortalFeedbackTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.buyer = User.objects.create_user(username="fbbuyer", email="fbbuyer@example.com", password="pass12345", first_name="Priya", last_name="Sharma")
        self.maker = User.objects.create_user(username="fbmaker", email="fbmaker@example.com", password="pass12345", role="manufacturer")
        ManufacturerProfile.objects.create(
            user=self.maker, companyname="Feedback Works", phone="8800000001", address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="fbmaker@example.com",
        )
        self.staff = User.objects.create_user(username="fbstaff", email="fbstaff@example.com", password="pass12345", is_staff=True)

    def submit(self, user, **data):
        self.client.login(username=user.email, password="pass12345")
        return self.client.post(reverse("portal-feedback"), data)

    def test_user_submits_then_updates_their_feedback(self):
        from homepage.models import PortalFeedback
        self.submit(self.buyer, rating="4", useful_features=["rfq_posting", "messaging"], improvement_areas=["mobile"],
                    improvement_note="Needs a mobile app", comment="Saved us weeks of emails.", allow_public="on")
        feedback = PortalFeedback.objects.get(user=self.buyer)
        self.assertEqual((feedback.rating, feedback.role), (4, "consumer"))
        self.assertEqual(feedback.useful_features, ["rfq_posting", "messaging"])
        self.assertEqual(feedback.improvement_areas, ["mobile"])
        self.assertTrue(feedback.allow_public)
        self.submit(self.buyer, rating="5", comment="Saved us weeks of emails.")
        self.assertEqual(PortalFeedback.objects.count(), 1)
        self.assertEqual(PortalFeedback.objects.get(user=self.buyer).rating, 5)

    def test_rating_is_required_and_bounded(self):
        from homepage.models import PortalFeedback
        self.submit(self.buyer, comment="No stars")
        self.submit(self.buyer, rating="6")
        self.assertFalse(PortalFeedback.objects.exists())

    def test_public_pages_show_overall_rating_and_only_permitted_best_reviews(self):
        self.submit(self.buyer, rating="5", comment="Quotes in two days, brilliant.", allow_public="on")
        self.submit(self.maker, rating="3", comment="Decent but slow notifications.", allow_public="on",
                    improvement_note="Private note never public")
        self.client.logout()
        for name in ("home", "about", "how-it-works", "pricing"):
            page = self.client.get(reverse(name))
            self.assertContains(page, "4.0", msg_prefix=name)
            self.assertContains(page, "Quotes in two days, brilliant.", msg_prefix=name)
            self.assertContains(page, "Priya S.", msg_prefix=name)
            self.assertNotContains(page, "Decent but slow", msg_prefix=name)  # 3 stars isn't a "best" review
            self.assertNotContains(page, "Private note", msg_prefix=name)

    def test_private_and_hidden_reviews_stay_off_the_site(self):
        from homepage.models import PortalFeedback
        self.submit(self.buyer, rating="5", comment="Keep this private please.")
        self.submit(self.maker, rating="5", comment="Great platform overall.", allow_public="on")
        self.client.logout()
        page = self.client.get(reverse("home"))
        self.assertNotContains(page, "Keep this private")
        self.assertContains(page, "Great platform overall.")
        maker_feedback = PortalFeedback.objects.get(user=self.maker)
        self.client.login(username=self.staff.email, password="pass12345")
        self.client.post(reverse("portal-feedback-moderate", kwargs={"pk": maker_feedback.pk, "action": "hide"}))
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("home")), "Great platform overall.")

    def test_featured_reviews_come_first_and_editing_the_comment_unfeatures_it(self):
        from homepage.feedback import public_summary
        from homepage.models import PortalFeedback
        self.submit(self.buyer, rating="5", comment="Buyer review.", allow_public="on")
        self.submit(self.maker, rating="4", comment="Maker review.", allow_public="on")
        self.assertEqual(public_summary()["reviews"][0]["comment"], "Buyer review.")
        maker_feedback = PortalFeedback.objects.get(user=self.maker)
        self.client.login(username=self.staff.email, password="pass12345")
        self.client.post(reverse("portal-feedback-moderate", kwargs={"pk": maker_feedback.pk, "action": "feature"}))
        self.assertEqual(public_summary()["reviews"][0]["comment"], "Maker review.")
        self.submit(self.maker, rating="4", comment="Edited maker review.", allow_public="on")
        self.assertFalse(PortalFeedback.objects.get(user=self.maker).is_featured)

    def test_staff_summary_counts_useful_and_improvement_areas(self):
        self.submit(self.buyer, rating="5", useful_features=["quote_comparison"], improvement_areas=["mobile"])
        self.submit(self.maker, rating="3", useful_features=["quote_comparison"], improvement_areas=["mobile", "notifications"],
                    improvement_note="Email digests please")
        self.client.login(username=self.staff.email, password="pass12345")
        page = self.client.get(reverse("portal-feedback-summary"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Email digests please")
        features = {row["label"]: row for row in page.context["summary"]["features"]}
        self.assertEqual(features["Comparing quotes"]["useful"], 2)
        self.assertEqual(features["Mobile experience"]["improve"], 2)
        self.assertEqual(page.context["summary"]["features"][0]["label"], "Mobile experience")

    def test_summary_and_moderation_are_staff_only(self):
        self.client.login(username=self.buyer.email, password="pass12345")
        self.assertEqual(self.client.get(reverse("portal-feedback-summary")).status_code, 403)
        self.assertEqual(self.client.post(reverse("portal-feedback-moderate", kwargs={"pk": 1, "action": "hide"})).status_code, 403)

    def test_public_section_is_hidden_without_ratings(self):
        self.assertNotContains(self.client.get(reverse("home")), "Trusted by buyers and manufacturers")
