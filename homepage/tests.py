from django.contrib.messages import get_messages
from django.core.cache import cache
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
        cache.clear()  # failed-login counts live in the cache
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

    def test_unregistered_email_links_to_register(self):
        response = self.client.post(
            self.url,
            data={"username": "nobody@example.com", "password": "whatever"},
            follow=True,
        )
        self.assertContains(response, "The email ID you entered is not registered.")
        self.assertContains(response, 'href="%s"' % reverse("register"))
        self.assertNotContains(response, "Incorrect email or password")

    def test_refresh_after_failed_login_is_a_plain_get(self):
        self.client.post(self.url, data={"username": "real@example.com", "password": "wrong-password"})
        # Simulates the browser refresh: a plain GET, not a resubmitted POST.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_valid_login_still_works(self):
        response = self.client.post(self.url, data={"username": "real@example.com", "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


@override_settings(
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    },
    LOGIN_FAILURE_LIMIT_PER_EMAIL=3,
    LOGIN_FAILURE_LIMIT_PER_IP=5,
)
class LoginThrottleTests(TestCase):
    """Login used to allow unlimited password guesses, and the "not
    registered" message let anyone script a check of which emails have
    accounts. Failures are now counted per email and per IP."""

    def setUp(self):
        cache.clear()
        User.objects.create_user(username="victim", email="victim@example.com", password="correct-horse-battery")
        self.url = reverse("login")

    def _post(self, email, password="wrong-password", **extra):
        return self.client.post(self.url, data={"username": email, "password": password}, follow=True, **extra)

    def test_email_locks_after_limit_even_with_the_right_password(self):
        for _ in range(3):
            self._post("victim@example.com")
        response = self._post("victim@example.com", password="correct-horse-battery")
        self.assertContains(response, "Too many failed login attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_email_lock_applies_from_another_ip(self):
        for _ in range(3):
            self._post("victim@example.com", REMOTE_ADDR="10.0.0.1")
        response = self._post("victim@example.com", password="correct-horse-battery", REMOTE_ADDR="10.0.0.2")
        self.assertContains(response, "Too many failed login attempts")

    def test_ip_locks_after_spraying_many_emails(self):
        for i in range(5):
            self._post(f"guess{i}@example.com", REMOTE_ADDR="10.0.0.9")
        response = self._post("victim@example.com", password="correct-horse-battery", REMOTE_ADDR="10.0.0.9")
        self.assertContains(response, "Too many failed login attempts")
        # A different client is unaffected.
        self._post("victim@example.com", password="correct-horse-battery", REMOTE_ADDR="10.0.0.10")
        self.assertIn("_auth_user_id", self.client.session)

    def test_locked_out_request_does_not_reveal_registration(self):
        for i in range(5):
            self._post(f"guess{i}@example.com")
        response = self._post("nobody@example.com")
        self.assertNotContains(response, "not registered")

    @override_settings(LOGIN_UNREGISTERED_HINT_LIMIT=2)
    def test_not_registered_hint_stops_after_a_few_failures_from_one_ip(self):
        self.assertContains(self._post("first@example.com"), "not registered")
        self.assertContains(self._post("second@example.com"), "not registered")
        response = self._post("third@example.com")
        self.assertNotContains(response, "not registered")
        self.assertContains(response, "Incorrect email or password")
        # Another client still gets the helpful message.
        self.assertContains(self._post("fourth@example.com", REMOTE_ADDR="10.0.0.20"), "not registered")

    def test_successful_login_resets_the_email_count(self):
        for _ in range(2):
            self._post("victim@example.com")
        self._post("victim@example.com", password="correct-horse-battery")
        self.client.logout()
        for _ in range(2):
            self._post("victim@example.com")
        response = self._post("victim@example.com", password="correct-horse-battery")
        self.assertNotContains(response, "Too many failed login attempts")
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


@DASHBOARD_TEST_STORAGES
class LogoutFeedbackPromptTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.buyer = User.objects.create_user(username="lobuyer", email="lobuyer@example.com", password="pass12345", first_name="Asha")
        self.client.login(username=self.buyer.email, password="pass12345")

    def test_prompt_shows_until_the_user_has_rated(self):
        from homepage.models import PortalFeedback
        page = self.client.get(reverse("profile"))
        self.assertContains(page, "Before you go")
        PortalFeedback.objects.create(user=self.buyer, role="consumer", rating=4)
        self.assertNotContains(self.client.get(reverse("profile")), "Before you go")

    def test_staff_are_not_prompted(self):
        staff = User.objects.create_user(username="lostaff", email="lostaff@example.com", password="pass12345", is_staff=True)
        self.client.login(username=staff.email, password="pass12345")
        self.assertNotContains(self.client.get(reverse("profile")), "Before you go")

    def test_submit_from_prompt_saves_and_logs_out(self):
        from homepage.models import PortalFeedback
        response = self.client.post(reverse("portal-feedback"), {"rating": "5", "comment": "Smooth", "logout": "1"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(PortalFeedback.objects.get(user=self.buyer).rating, 5)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_skip_still_logs_out(self):
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_star_buttons_have_no_fixed_grey_class(self):
        # A fixed text-gray-300 outranks the Alpine-added text-amber-400 in the built CSS.
        page = self.client.get(reverse("portal-feedback"))
        self.assertContains(page, """' : 'text-gray-300'" class="transition-colors">""")
        self.assertNotContains(page, """' : 'text-gray-300'" class="text-gray-300">""")


@DASHBOARD_TEST_STORAGES
class PortalRatingBadgeTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        from homepage.models import PortalFeedback
        cache.clear()
        user = User.objects.create_user(username="badgeuser", email="badgeuser@example.com", password="pass12345", first_name="Ravi")
        PortalFeedback.objects.create(user=user, role="consumer", rating=5, comment="Quotes came in fast.", allow_public=True)

    def test_badge_at_the_top_of_pages_with_a_reviews_section(self):
        for name in ("home", "about", "how-it-works", "pricing"):
            page = self.client.get(reverse(name)).content.decode()
            self.assertIn('href="#reviews"', page, name)
            self.assertIn('id="reviews"', page, name)
            self.assertLess(page.index('href="#reviews"'), page.index('id="reviews"'), name)
        self.assertNotIn('href="#reviews"', self.client.get(reverse("privacy-policy")).content.decode())

    def test_half_star_for_a_4_5_average(self):
        from homepage.models import PortalFeedback
        from marketplace.templatetags.custom_filters import star_fills
        self.assertEqual(star_fills(4.5), [100, 100, 100, 100, 50])
        self.assertEqual(star_fills(3.46), [100, 100, 100, 50, 0])
        self.assertEqual(star_fills(None), [0, 0, 0, 0, 0])
        other = User.objects.create_user(username="badgeuser2", email="badgeuser2@example.com", password="pass12345")
        PortalFeedback.objects.create(user=other, role="consumer", rating=4)
        page = self.client.get(reverse("home")).content.decode()
        self.assertIn("4.5 out of 5 stars", page)
        self.assertIn('style="width: 50%"', page)

    def test_reviews_on_contact_and_register_pages(self):
        for name in ("contact", "register"):
            page = self.client.get(reverse(name)).content.decode()
            self.assertIn('href="#reviews"', page, name)
            self.assertIn("Quotes came in fast.", page, name)
        # The detailed sign-up forms need the half-registered account from step one.
        for name, role in (("register-customer", "consumer"), ("register-supplier", "manufacturer")):
            pending = User.objects.create_user(
                username=f"pending-{role}", email=f"pending-{role}@example.com", password="pass12345", role=role,
                email_verified=True,  # this test is about the review widget, not email verification
            )
            session = self.client.session
            session["session_user_id"] = pending.pk
            session.save()
            self.assertContains(self.client.get(reverse(name)), "Quotes came in fast.", msg_prefix=name)


@override_settings(
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    },
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    SECURE_HSTS_SECONDS=3600,
)
class HttpsHardeningTests(TestCase):
    """Production settings: plain HTTP is redirected, but a request the TLS
    proxy marks as HTTPS is served (not redirected again, which would loop)."""

    def test_plain_http_is_redirected_to_https(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response["Location"].startswith("https://"))

    def test_https_via_proxy_is_served_with_hsts_and_no_framing(self):
        response = self.client.get(reverse("login"), HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Strict-Transport-Security"], "max-age=3600")
        self.assertEqual(response["X-Frame-Options"], "DENY")


@DASHBOARD_TEST_STORAGES
class AuthSurfaceHardeningTests(TestCase):
    """Login paths that used to sidestep core/login_throttle, the
    password-reset rate limit, and session binding."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="authsurf", email="authsurf@example.com", password="correct-horse-battery")

    def test_admin_login_goes_through_the_throttled_login_page(self):
        response = self.client.get("/admin/login/?next=/admin/")
        self.assertRedirects(response, reverse("login") + "?next=%2Fadmin%2F", fetch_redirect_response=False)

    def test_admin_login_drops_an_off_site_next(self):
        response = self.client.get("/admin/login/?next=https://evil.example/")
        self.assertRedirects(response, reverse("login") + "?next=%2Fadmin%2F", fetch_redirect_response=False)

    def test_failed_login_keeps_a_safe_next(self):
        response = self.client.post(reverse("login"), {"username": "authsurf@example.com", "password": "nope", "next": "/admin/"})
        self.assertRedirects(response, reverse("login") + "?next=%2Fadmin%2F", fetch_redirect_response=False)

    def test_password_reset_emails_are_rate_limited_without_revealing_it(self):
        from django.conf import settings
        from django.core import mail
        for _ in range(settings.PASSWORD_RESET_LIMIT_PER_EMAIL + 3):
            response = self.client.post(reverse("password_reset"), {"email": "authsurf@example.com"})
            self.assertRedirects(response, reverse("password_reset_done"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), settings.PASSWORD_RESET_LIMIT_PER_EMAIL)

    def test_unknown_email_gets_the_same_page_and_sends_nothing(self):
        # Django's PasswordResetForm.save() only emails a match it finds;
        # the view redirects to password_reset_done either way, so an
        # unknown address can't be told apart from a real one by the
        # response — only by the (nonexistent) email that follows.
        from django.core import mail
        response = self.client.post(reverse("password_reset"), {"email": "nobody-here@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)
        response = self.client.post(reverse("password_reset"), {"email": "authsurf@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("authsurf@example.com", mail.outbox[0].to)

    def test_session_is_logged_out_when_replayed_from_another_browser(self):
        self.client.login(username="authsurf@example.com", password="correct-horse-battery")
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)
        response = self.client.get(reverse("profile"), HTTP_USER_AGENT="Stolen-Cookie-Browser/1.0")
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_reauth_rejects_an_off_site_next(self):
        self.client.login(username="authsurf@example.com", password="correct-horse-battery")
        response = self.client.post(reverse("reauth"), {"password": "correct-horse-battery", "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)


@DASHBOARD_TEST_STORAGES
class PasswordChangeAndAccountMenuTests(TestCase):
    """Changing your own password from the account menu: current and new
    password, then a code emailed to you; only a correct code changes it,
    and then you're signed out everywhere and log in again. Also the menu:
    the user's role under their name rather than the company."""
    NEW_PASSWORD = "N3w-strong-passw0rd!"

    def setUp(self):
        from django.core import mail
        from marketplace.test_teams import PASSWORD, make_buyer
        cache.clear()
        mail.outbox.clear()
        self.old_password = PASSWORD
        self.owner, self.profile = make_buyer("pwchange", "9600000001")
        self.client.login(username=self.owner.email, password=PASSWORD)

    def request_change(self, old, new=None):
        new = new or self.NEW_PASSWORD
        return self.client.post(reverse("password_change"), {"old_password": old, "new_password1": new, "new_password2": new})

    def last_code(self):
        from django.core import mail
        message = mail.outbox[-1]
        self.assertIn("change your password", message.subject)
        return next(l.strip() for l in message.body.splitlines() if l.strip().isdigit() and len(l.strip()) == 6)

    def wrong_code(self, code):
        return "000000" if code != "000000" else "111111"

    def verify(self, code):
        return self.client.post(reverse("password_change_verify"), {"code": code})

    def password_is(self, user, raw):
        user.refresh_from_db()
        return user.check_password(raw)

    def test_menu_shows_role_and_change_password(self):
        page = self.client.get(reverse("home"))
        self.assertContains(page, reverse("password_change"))
        self.assertContains(page, '<span class="block text-xs text-gray-500">Owner</span>', html=True)
        self.assertContains(self.client.get(reverse("password_change")), "Current password")

    def test_menu_shows_a_team_members_role(self):
        from marketplace.test_teams import PASSWORD, add_member
        member = add_member(self.profile, "pwmember")
        self.client.login(username=member.email, password=PASSWORD)
        self.assertContains(self.client.get(reverse("home")), '<span class="block text-xs text-gray-500">Procurement</span>', html=True)

    def test_right_passwords_send_a_code_and_change_nothing_yet(self):
        response = self.request_change(self.old_password)
        self.assertRedirects(response, reverse("password_change_verify"), fetch_redirect_response=False)
        self.last_code()
        self.assertTrue(self.password_is(self.owner, self.old_password))
        self.assertContains(self.client.get(reverse("password_change_verify")), "Verify and change password")

    def test_correct_code_changes_the_password_and_logs_out(self):
        from django.core import mail
        self.request_change(self.old_password)
        response = self.verify(self.last_code())
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertTrue(self.password_is(self.owner, self.NEW_PASSWORD))
        self.assertIn("password was changed", mail.outbox[-1].subject)
        # Signed out here: dashboard pages send you to log in again.
        self.assertIn(reverse("login"), self.client.get(reverse("profile"))["Location"])
        self.assertTrue(self.client.login(username=self.owner.email, password=self.NEW_PASSWORD))

    def test_change_signs_out_other_sessions_too(self):
        from django.test import Client
        other = Client()
        other.login(username=self.owner.email, password=self.old_password)
        self.request_change(self.old_password)
        self.verify(self.last_code())
        self.assertIn(reverse("login"), other.get(reverse("profile"))["Location"])

    def test_wrong_code_changes_nothing(self):
        self.request_change(self.old_password)
        wrong = self.wrong_code(self.last_code())
        self.assertRedirects(self.verify(wrong), reverse("password_change_verify"), fetch_redirect_response=False)
        self.assertTrue(self.password_is(self.owner, self.old_password))
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)  # still signed in

    def test_too_many_wrong_codes_lock_the_code(self):
        from accounts.otp import MAX_ATTEMPTS
        self.request_change(self.old_password)
        code = self.last_code()
        for _ in range(MAX_ATTEMPTS):
            self.verify(self.wrong_code(code))
        self.verify(code)
        self.assertTrue(self.password_is(self.owner, self.old_password))

    def test_expired_code_changes_nothing(self):
        from datetime import timedelta
        from unittest import mock
        from django.utils import timezone
        self.request_change(self.old_password)
        code = self.last_code()
        later = timezone.now() + timedelta(minutes=11)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.verify(code)
        self.assertTrue(self.password_is(self.owner, self.old_password))

    def test_resend_replaces_the_code(self):
        self.request_change(self.old_password)
        first = self.last_code()
        self.client.post(reverse("password_change_verify"), {"action": "resend"})
        second = self.last_code()
        if first != second:
            self.verify(first)
            self.assertTrue(self.password_is(self.owner, self.old_password))
        self.verify(second)
        self.assertTrue(self.password_is(self.owner, self.NEW_PASSWORD))

    def test_verify_page_needs_a_pending_change(self):
        self.assertRedirects(self.client.get(reverse("password_change_verify")), reverse("password_change"), fetch_redirect_response=False)
        self.verify("123456")
        self.assertTrue(self.password_is(self.owner, self.old_password))

    def test_wrong_current_password_sends_no_code(self):
        from django.core import mail
        response = self.request_change("not-my-password")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your old password was entered incorrectly")
        self.assertEqual(mail.outbox, [])

    def test_weak_new_password_is_refused(self):
        from django.core import mail
        self.assertEqual(self.request_change(self.old_password, "password").status_code, 200)
        self.assertEqual(mail.outbox, [])

    @override_settings(LOGIN_FAILURE_LIMIT_PER_EMAIL=2)
    def test_wrong_current_passwords_count_toward_the_lockout(self):
        from django.core import mail
        self.request_change("wrong-1")
        self.request_change("wrong-2")
        response = self.request_change(self.old_password)
        self.assertRedirects(response, reverse("password_change"), fetch_redirect_response=False)
        self.assertEqual(mail.outbox, [])

    def test_a_viewer_may_change_their_own_password(self):
        from accounts.models import TeamMember
        from marketplace.test_teams import PASSWORD, add_member
        viewer = add_member(self.profile, "pwviewer", TeamMember.VIEWER)
        self.client.login(username=viewer.email, password=PASSWORD)
        self.request_change(PASSWORD)
        self.verify(self.last_code())
        self.assertTrue(self.password_is(viewer, self.NEW_PASSWORD))
