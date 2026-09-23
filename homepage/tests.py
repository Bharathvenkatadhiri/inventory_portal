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
