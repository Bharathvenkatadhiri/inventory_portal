from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from accounts.models import (
    ConsumerProfile, ManufacturerProfile, Company, Machine, Certification,
    ManufacturingTech, MaterialCapability, ManufacturerPhoto,
)
from gst.models import GSTVerification
from gst.views import SESSION_KEY as GST_SESSION_KEY

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

ACTIVE_GSTIN = "33AAAAA0000A1Z5"


def verify_gstin_for_session(client, gstin=ACTIVE_GSTIN, **fields):
    """What gst.views.verify_gstin leaves behind for the sign-up step: a
    successful GSTVerification remembered in the session."""
    values = dict(
        gstin=gstin, provider="mock", status=GSTVerification.Status.SUCCESS,
        legal_name="Verified Legal Name Private Limited", trade_name="Verified Trade Name", gst_status="Active",
        business_constitution="Private Limited Company", taxpayer_type="Regular", state="Tamil Nadu",
        state_code=gstin[:2], pincode="600001", verified_at=timezone.now(),
        principal_address={"address": "1 Verified Street, Chennai - 600001", "city": "Chennai"},
    )
    values.update(fields)
    verification = GSTVerification.objects.create(**values)
    session = client.session
    session[GST_SESSION_KEY] = verification.pk
    session.save()
    return verification


class ProfileModelTests(TestCase):
    def test_consumer_profile_creation(self):
        user = User.objects.create_user(username="c1", email="c1@example.com", password="pass12345")
        profile = ConsumerProfile.objects.create(
            user=user,
            Name="Acme Buyer",
            type_of_business="electronics",
            city="Bengaluru",
            state="KA",
            country="India",
            phone="9999999999",
            email="c1@example.com",
            EORI_number="EORI1",
            VAT_number="VAT1",
        )
        self.assertEqual(str(profile), f"#{profile.id} - Acme Buyer")

    def test_manufacturer_profile_creation(self):
        user = User.objects.create_user(
            username="m1", email="m1@example.com", password="pass12345", role="manufacturer"
        )
        profile = ManufacturerProfile.objects.create(
            user=user,
            phone="8888888888",
            address="123 Industrial Rd",
            city="Pune",
            state="MH",
            country="India",
            amount_of_employees="10-20",
            turnover_per_year="<1",
            certificates="",
            email="m1@example.com",
        )
        self.assertIn("m1", str(profile.user))


class UserRegistrationRoleTests(TestCase):
    """
    The Supplier/Buyer choice sets `role` — and must never touch `is_staff`
    (it used to, making every supplier a Django staff user).
    """

    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.client = Client()
        self.url = reverse("register")

    def _payload(self, username, email, account_type):
        return {
            "username": username,
            "first_name": "Test",
            "last_name": "User",
            "password1": "a-strong-passw0rd",
            "password2": "a-strong-passw0rd",
            "email": email,
            "account_type": account_type,
        }

    def _verify(self, email):
        for line in mail.outbox[-1].body.splitlines():
            line = line.strip()
            if line.isdigit() and len(line) == 6:
                return self.client.post(reverse("verify-email"), {"code": line})
        raise AssertionError(f"No 6-digit code found in email body: {mail.outbox[-1].body!r}")

    def test_supplier_signup_gets_manufacturer_role(self):
        response = self.client.post(self.url, data=self._payload("supplier1", "supplier1@example.com", "supplier"))
        self.assertEqual(response.status_code, 302)
        # No User row until the email is verified.
        self.assertFalse(User.objects.filter(email="supplier1@example.com").exists())
        self._verify("supplier1@example.com")
        user = User.objects.get(email="supplier1@example.com")
        self.assertEqual(user.role, "manufacturer")
        self.assertFalse(user.is_staff)

    def test_buyer_signup_gets_consumer_role(self):
        response = self.client.post(self.url, data=self._payload("buyer1", "buyer1@example.com", "buyer"))
        self.assertEqual(response.status_code, 302)
        self._verify("buyer1@example.com")
        user = User.objects.get(email="buyer1@example.com")
        self.assertEqual(user.role, "consumer")


@override_settings(
    # register_first.html/register_supplier.html use {% static %}; see the
    # identical note on CreateSupplierSecurityTests above.
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
)
class RegisterViewTests(TestCase):
    """
    accounts.views.register() previously fell through to a silent re-render
    (no error, no redirect) when the submitted email already belonged to a
    ConsumerProfile/ManufacturerProfile — from the user's side, clicking
    Register just appeared to do nothing.
    """

    def setUp(self):
        cache.clear()  # the "already exists" hint shares login's per-IP throttle counter
        self.client = Client()
        self.url = reverse("register")

    def _payload(self, username, email, account_type):
        return {
            "username": username,
            "first_name": "Test",
            "last_name": "User",
            "password1": "a-strong-passw0rd",
            "password2": "a-strong-passw0rd",
            "email": email,
            "account_type": account_type,
        }

    def test_email_already_used_by_manufacturer_profile_shows_error(self):
        owner = User.objects.create_user(username="owner1", email="owner1@example.com", password="pass12345")
        ManufacturerProfile.objects.create(
            user=owner, phone="6666666666", address="x", city="Chennai", state="TN", country="India",
            amount_of_employees="10-20", turnover_per_year="<1", email="taken@example.com",
        )
        response = self.client.post(self.url, data=self._payload("newsupplier", "taken@example.com", "supplier"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        # No duplicate/orphan User should be created for the rejected attempt.
        self.assertFalse(User.objects.filter(username="newsupplier").exists())

    def test_email_already_used_by_consumer_profile_shows_error(self):
        owner = User.objects.create_user(username="owner2", email="owner2@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=owner, Name="Acme", type_of_business="electronics", city="Pune", state="MH", country="India",
            phone="5555555555", email="takenbuyer@example.com", EORI_number="E1", VAT_number="V1",
        )
        response = self.client.post(self.url, data=self._payload("newbuyer", "takenbuyer@example.com", "buyer"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertFalse(User.objects.filter(username="newbuyer").exists())

    def test_existing_user_without_profile_resumes_registration(self):
        # Simulates step 1 having completed in an earlier visit and its
        # email already verified, but the supplier/customer form (step 2)
        # never being submitted.
        User.objects.create_user(
            username="abandoned", email="abandoned@example.com", password="a-strong-passw0rd", role="manufacturer",
            email_verified=True,
        )
        response = self.client.post(self.url, data=self._payload("abandoned", "abandoned@example.com", "supplier"))
        self.assertRedirects(response, reverse("register-supplier"))
        self.assertEqual(self.client.session["session_email"], "abandoned@example.com")

    def test_resuming_an_unverified_account_is_sent_back_to_verify_its_email(self):
        # Step one was submitted earlier but the OTP was never confirmed, so
        # there's a PendingRegistration but no User row yet.
        from django.contrib.auth.hashers import make_password
        from accounts.models import PendingRegistration
        PendingRegistration.objects.create(
            email="halfdone@example.com", username="halfdone", first_name="Test", last_name="User",
            password=make_password("a-strong-passw0rd"), role="manufacturer",
        )
        response = self.client.post(self.url, data=self._payload("halfdone", "halfdone@example.com", "supplier"))
        self.assertRedirects(response, reverse("verify-email"))
        self.assertFalse(User.objects.filter(email="halfdone@example.com").exists())

    def test_resuming_someone_elses_registration_needs_their_password(self):
        User.objects.create_user(username="victim", email="victim@example.com", password="their-own-passw0rd", role="manufacturer")
        response = self.client.post(self.url, data=self._payload("victim", "victim@example.com", "supplier"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("session_user_id", self.client.session)

    def _duplicate_owner(self, email):
        owner = User.objects.create_user(username=f"owner-{email}", email=f"owner-{email}", password="pass12345")
        ConsumerProfile.objects.create(
            user=owner, Name="Existing Co", type_of_business="electronics", city="Pune", state="MH", country="India",
            phone=f"9{abs(hash(email)) % 10**9:09d}", email=email, EORI_number=f"E{email}", VAT_number=f"V{email}",
        )

    def test_already_exists_hint_is_capped_per_ip_then_falls_back_to_a_neutral_message(self):
        # Previously unlimited: this form alone let anyone script a check
        # of which emails have accounts, bypassing the login form's own
        # 3-hint cap. Now it shares that same per-IP budget.
        self._duplicate_owner("takenA@example.com")
        limit = settings.LOGIN_UNREGISTERED_HINT_LIMIT
        for _ in range(limit):
            response = self.client.post(self.url, data=self._payload("probeA", "takenA@example.com", "buyer"))
            self.assertContains(response, "already exists")
        response = self.client.post(self.url, data=self._payload("probeA", "takenA@example.com", "buyer"))
        self.assertNotContains(response, "already exists")
        self.assertContains(response, "sign in instead")

    def test_the_cap_is_shared_across_different_emails_checked_from_one_ip(self):
        # An IP-wide budget, not per-email: exhausting it on one probe
        # hides the hint for every other email too, so scripting through a
        # list still only gets a handful of confirmed hits.
        self._duplicate_owner("takenB1@example.com")
        self._duplicate_owner("takenB2@example.com")
        limit = settings.LOGIN_UNREGISTERED_HINT_LIMIT
        for _ in range(limit):
            self.client.post(self.url, data=self._payload("probeB", "takenB1@example.com", "buyer"))
        response = self.client.post(self.url, data=self._payload("probeB", "takenB2@example.com", "buyer"))
        self.assertNotContains(response, "already exists")

    def test_the_cap_is_shared_with_logins_own_failure_counter(self):
        # registration_hint_allowed spends from the exact cache key/window
        # a failed login uses, so probing through one form eats the
        # other's budget too.
        from core import login_throttle
        self._duplicate_owner("takenC@example.com")

        class FakeRequest:
            META = {"REMOTE_ADDR": "127.0.0.1"}
        for _ in range(settings.LOGIN_UNREGISTERED_HINT_LIMIT):
            login_throttle.record_failure(FakeRequest(), "someone-else@example.com")
        response = self.client.post(self.url, data=self._payload("probeC", "takenC@example.com", "buyer"))
        self.assertNotContains(response, "already exists")

    def test_a_different_ip_still_gets_its_own_hint(self):
        self._duplicate_owner("takenD@example.com")
        limit = settings.LOGIN_UNREGISTERED_HINT_LIMIT
        for _ in range(limit):
            self.client.post(self.url, data=self._payload("probeD", "takenD@example.com", "buyer"), REMOTE_ADDR="10.0.0.5")
        response = self.client.post(self.url, data=self._payload("probeD", "takenD@example.com", "buyer"), REMOTE_ADDR="10.0.0.6")
        self.assertContains(response, "already exists")

    def test_a_brand_new_email_never_spends_the_budget(self):
        limit = settings.LOGIN_UNREGISTERED_HINT_LIMIT
        for i in range(limit + 2):
            response = self.client.post(self.url, data=self._payload(f"newuser{i}", f"newuser{i}@example.com", "buyer"))
            self.assertEqual(response.status_code, 302)  # registration proceeds every time


@override_settings(
    # register_first.html and verify_email.html use {% static %}; see the
    # identical note on CreateSupplierSecurityTests above.
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
)
class EmailVerificationTests(TestCase):
    """Registration now pauses at an OTP step between account creation and
    the profile step (accounts.otp, accounts.views.verify_email)."""

    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.url = reverse("register")

    def _payload(self, username, email, account_type="buyer"):
        return {
            "username": username, "first_name": "Test", "last_name": "User",
            "password1": "a-strong-passw0rd", "password2": "a-strong-passw0rd",
            "email": email, "account_type": account_type,
        }

    def _register(self, username="otpuser", email="otpuser@example.com", account_type="buyer"):
        return self.client.post(self.url, data=self._payload(username, email, account_type))

    def _sent_code(self):
        # The code isn't stored in the clear; pull it back out of the email
        # actually sent, the same way a real user would read it.
        for line in mail.outbox[-1].body.splitlines():
            line = line.strip()
            if line.isdigit() and len(line) == 6:
                return line
        raise AssertionError(f"No 6-digit code found in email body: {mail.outbox[-1].body!r}")

    def test_registering_sends_a_code_and_redirects_to_verify_without_creating_a_user(self):
        response = self._register()
        self.assertRedirects(response, reverse("verify-email"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("otpuser@example.com", mail.outbox[0].to)
        # Nothing lands in the users table until the code is confirmed.
        self.assertFalse(User.objects.filter(email="otpuser@example.com").exists())
        from accounts.models import PendingRegistration
        self.assertTrue(PendingRegistration.objects.filter(email="otpuser@example.com").exists())

    def test_cannot_reach_the_profile_step_before_verifying(self):
        self._register()
        response = self.client.get(reverse("register-customer"))
        self.assertRedirects(response, reverse("verify-email"))

    def test_correct_code_verifies_creates_the_user_and_moves_on_to_the_profile_step(self):
        self._register()
        code = self._sent_code()
        response = self.client.post(reverse("verify-email"), {"code": code})
        self.assertRedirects(response, reverse("register-customer"))
        user = User.objects.get(email="otpuser@example.com")
        self.assertTrue(user.email_verified)
        # The pending signup is consumed, not left behind.
        from accounts.models import PendingRegistration
        self.assertFalse(PendingRegistration.objects.filter(email="otpuser@example.com").exists())
        # And the profile step is reachable now.
        self.assertEqual(self.client.get(reverse("register-customer")).status_code, 200)

    def test_wrong_code_is_rejected_and_does_not_create_a_user(self):
        self._register()
        self._sent_code()
        response = self.client.post(reverse("verify-email"), {"code": "000000"})
        self.assertContains(response, "code isn")
        self.assertFalse(User.objects.filter(email="otpuser@example.com").exists())

    def test_too_many_wrong_attempts_locks_the_code(self):
        from accounts.otp import MAX_ATTEMPTS
        self._register()
        code = self._sent_code()
        for _ in range(MAX_ATTEMPTS):
            self.client.post(reverse("verify-email"), {"code": "000000"})
        response = self.client.post(reverse("verify-email"), {"code": code})  # even the real code, now locked
        self.assertContains(response, "Too many incorrect attempts")
        self.assertFalse(User.objects.filter(email="otpuser@example.com").exists())

    def test_resend_issues_a_new_code_and_is_rate_limited(self):
        from accounts.otp import RESEND_LIMIT
        self._register()  # counts as the first of RESEND_LIMIT sends in the window
        first_code = self._sent_code()
        for _ in range(RESEND_LIMIT - 1):
            self.client.post(reverse("verify-email"), {"action": "resend"})
        self.assertEqual(len(mail.outbox), RESEND_LIMIT)
        response = self.client.post(reverse("verify-email"), {"action": "resend"}, follow=True)
        self.assertContains(response, "Too many codes requested")
        self.assertEqual(len(mail.outbox), RESEND_LIMIT)  # the throttled attempt sent nothing
        # The very first code no longer verifies once a new one replaces it.
        response = self.client.post(reverse("verify-email"), {"code": first_code})
        self.assertContains(response, "code isn")

    def test_verify_email_without_a_pending_registration_sends_back_to_register(self):
        self.assertRedirects(self.client.get(reverse("verify-email")), reverse("register"))

    def test_revisiting_verify_email_after_completing_it_sends_back_to_register(self):
        # Once verified, the pending signup is gone and session no longer
        # points at it — register() is what resumes an already-verified,
        # profile-incomplete account from here.
        self._register()
        self.client.post(reverse("verify-email"), {"code": self._sent_code()})
        self.assertRedirects(self.client.get(reverse("verify-email")), reverse("register"))


@override_settings(
    # register_supplier.html re-renders on validation failure and uses
    # {% static %}; the manifest storage used in prod requires a
    # `collectstatic` run this dev environment hasn't done (unrelated
    # pre-existing gap — a bootstrap asset's source map is missing). Plain
    # StaticFilesStorage needs no manifest, so tests aren't coupled to that.
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
)
class CreateSupplierSecurityTests(TestCase):
    """
    The registration endpoint must never trust the client for gst_verified /
    legal_name / address — only a Company this session verified server-side
    (via verify-gstin) can be attached to a new ManufacturerProfile.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="unverified", email="unverified@example.com", password="pass12345", role="manufacturer",
            email_verified=True,  # this class tests the GSTIN check, not email verification
        )
        session = self.client.session
        session["session_user_id"] = self.user.id
        session["session_username"] = self.user.username
        session["session_first_name"] = "Test"
        session["session_last_name"] = "Supplier"
        session["session_email"] = self.user.email
        session.save()
        self.url = reverse("register-supplier")

    def _base_payload(self):
        return {
            "user": self.user.id,
            "email": self.user.email,
            "phone": "9000000000",
            "amount_of_employees": "10-20",
            "turnover_per_year": "<1",
            "certificates": "",
        }

    def test_registration_without_prior_verification_is_rejected(self):
        response = self.client.post(self.url, data=self._base_payload())
        self.assertEqual(response.status_code, 200)  # re-renders the form with an error
        self.assertFalse(ManufacturerProfile.objects.exists())

    def test_posted_gst_identity_is_ignored(self):
        payload = self._base_payload()
        payload.update({
            "gstin": ACTIVE_GSTIN,
            "gst_verified": "true",
            "status": "SUCCESS",
            "legal_name": "Fake Legal Name Pvt Ltd",
            "principal_address": "Fake address the client made up",
        })
        response = self.client.post(self.url, data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ManufacturerProfile.objects.exists())
        self.assertFalse(Company.objects.exists())

    def test_registration_builds_the_company_from_the_session_verification(self):
        verification = verify_gstin_for_session(self.client)
        payload = self._base_payload()
        # Even if a malicious client sends a different legal name/address,
        # the backend must take them from the verification, not the POST.
        payload.update({"legal_name": "Something Else Entirely", "principal_address": "Elsewhere"})
        self.client.post(self.url, data=payload)

        profile = ManufacturerProfile.objects.get(user=self.user)
        company = profile.company
        self.assertEqual(company.gstin, ACTIVE_GSTIN)
        self.assertEqual(company.legal_name, "Verified Legal Name Private Limited")
        self.assertTrue(company.gst_verified)
        self.assertEqual(company.principal_address, "1 Verified Street, Chennai - 600001")
        self.assertEqual(company.name, "Verified Trade Name")  # display name defaults to the trade name
        self.assertEqual(profile.companyname, "Verified Trade Name")
        self.assertEqual(profile.address, "1 Verified Street, Chennai - 600001")
        self.assertEqual((profile.city, profile.state), ("Chennai", "Tamil Nadu"))
        verification.refresh_from_db()
        self.assertEqual(verification.company, company)
        # The verification is consumed so it can't be replayed.
        self.assertNotIn(GST_SESSION_KEY, self.client.session)

    def test_page_reshows_this_sessions_verification(self):
        self.assertContains(self.client.get(self.url), 'id="registration-submit-btn" class="btn-primary flex-1" disabled')
        verify_gstin_for_session(self.client)
        page = self.client.get(self.url)
        self.assertContains(page, 'id="gst-verification-initial"')
        self.assertContains(page, "Verified Legal Name Private Limited")
        self.assertNotContains(page, 'id="registration-submit-btn" class="btn-primary flex-1" disabled')

    def test_display_name_is_the_users_but_the_legal_name_is_gsts(self):
        verify_gstin_for_session(self.client)
        self.client.post(self.url, data={**self._base_payload(), "company_display_name": "  Lakshmi   Precision "})
        profile = ManufacturerProfile.objects.get(user=self.user)
        self.assertEqual(profile.companyname, "Lakshmi Precision")
        self.assertEqual(profile.company.name, "Lakshmi Precision")
        self.assertEqual(profile.company.legal_name, "Verified Legal Name Private Limited")

    def test_stale_or_unsuccessful_verifications_are_not_accepted(self):
        stale = verify_gstin_for_session(self.client)
        GSTVerification.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(hours=2))
        self.assertContains(self.client.post(self.url, data=self._base_payload()), "verify your company")
        verify_gstin_for_session(self.client, status=GSTVerification.Status.INACTIVE, gst_status="Cancelled")
        self.assertContains(self.client.post(self.url, data=self._base_payload()), "verify your company")
        self.assertFalse(ManufacturerProfile.objects.exists())


import itertools
_phone_counter = itertools.count(1)


def _make_manufacturer(username, email, **overrides):
    user = User.objects.create_user(username=username, email=email, password="pass12345", role="manufacturer")
    defaults = dict(
        user=user, companyname=f"{username} Co", phone=f"700000{next(_phone_counter):04d}",
        address="1 Rd", city="Pune", state="MH", country="India",
        amount_of_employees="10-20", turnover_per_year="<1", email=email,
    )
    defaults.update(overrides)
    return user, ManufacturerProfile.objects.create(**defaults)


class ProfileStrengthTests(TestCase):
    def setUp(self):
        self.user, self.profile = _make_manufacturer("strength", "strength@example.com")

    def test_empty_profile_scores_zero(self):
        percent, checklist = self.profile.profile_strength()
        self.assertEqual(percent, 0)
        self.assertTrue(all(not item["done"] for item in checklist))

    def test_score_increases_as_fields_are_filled(self):
        self.profile.about = "We make precision parts."
        self.profile.contact_name = "Jane"
        self.profile.contact_phone = "9999999999"
        self.profile.save()
        percent, _ = self.profile.profile_strength()
        self.assertGreater(percent, 0)

    def test_checklist_weights_sum_to_100(self):
        # ManufacturingTech/MaterialCapability are seeded by a management
        # command that never runs against the test database — create the
        # rows directly rather than assuming they exist.
        milling, _ = ManufacturingTech.objects.get_or_create(technology_type="milling")
        aluminium, _ = MaterialCapability.objects.get_or_create(material_type="aluminium")
        # Fill every field the checklist checks for and confirm the total is 100.
        self.profile.capabilities.add(milling)
        Machine.objects.create(manufacturer=self.profile, machine_type="Lathe")
        self.profile.materials.add(aluminium)
        Certification.objects.create(manufacturer=self.profile, name="ISO 9001")
        ManufacturerPhoto.objects.create(manufacturer=self.profile, image=SimpleUploadedFile("shop.jpg", b"fake-image-bytes"))
        self.profile.about = "About us"
        self.profile.contact_name = "Jane"
        self.profile.contact_phone = "9999999999"
        self.profile.minimum_order_qty = 10
        self.profile.typical_lead_time_days = 14
        self.profile.save()
        percent, _ = self.profile.profile_strength()
        self.assertEqual(percent, 100)


@DASHBOARD_TEST_STORAGES
class SupplierUpdateOwnershipTests(TestCase):
    """Regression test for the fixed bug: SupplierUpdateView had no
    ownership check at all — any authenticated user could edit any
    manufacturer's profile by guessing its pk."""

    def setUp(self):
        self.user_a, self.profile_a = _make_manufacturer("owner_a", "owner_a@example.com")
        self.user_b, self.profile_b = _make_manufacturer("owner_b", "owner_b@example.com")
        self.staff_user = User.objects.create_user(
            username="staffy", email="staffy@example.com", password="pass12345", is_staff=True,
        )

    def test_manufacturer_cannot_edit_another_manufacturers_profile(self):
        self.client.login(username="owner_a@example.com", password="pass12345")
        url = reverse("edit-supplier", kwargs={"pk": self.profile_b.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_manufacturer_can_edit_own_profile(self):
        self.client.login(username="owner_a@example.com", password="pass12345")
        url = reverse("edit-supplier", kwargs={"pk": self.profile_a.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_staff_can_still_edit_any_profile(self):
        self.client.login(username="staffy@example.com", password="pass12345")
        url = reverse("edit-supplier", kwargs={"pk": self.profile_b.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


@DASHBOARD_TEST_STORAGES
class CompanyProfileSubActionTests(TestCase):
    def setUp(self):
        self.user_a, self.profile_a = _make_manufacturer("comp_a", "comp_a@example.com")
        self.user_b, self.profile_b = _make_manufacturer("comp_b", "comp_b@example.com")
        self.machine_a = Machine.objects.create(manufacturer=self.profile_a, machine_type="Lathe")
        self.client.login(username="comp_a@example.com", password="pass12345")

    def test_company_profile_page_renders(self):
        response = self.client.get(reverse("company-profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lathe")

    def test_about_update_saves_own_profile(self):
        self.client.post(reverse("company-about-update"), {"about": "New about text"})
        self.profile_a.refresh_from_db()
        self.assertEqual(self.profile_a.about, "New about text")

    def test_cannot_delete_another_manufacturers_machine(self):
        self.client.logout()
        self.client.login(username="comp_b@example.com", password="pass12345")
        response = self.client.post(reverse("company-machine-remove", kwargs={"pk": self.machine_a.pk}))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Machine.objects.filter(pk=self.machine_a.pk).exists())

    def test_view_profile_details_routes_manufacturer_to_company_profile(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Company profile")


def _buyer(username, phone):
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass12345")
    profile = ConsumerProfile.objects.create(
        user=user, Name=f"{username} Co", type_of_business="electronics", city="Pune", state="MH", country="India",
        phone=phone, email=f"{username}@example.com", EORI_number=f"E-{username}", VAT_number=f"V-{username}",
    )
    return user, profile


@DASHBOARD_TEST_STORAGES
class AdminScreenPermissionTests(TestCase):
    """Customer/supplier/subscription admin screens used to need only a login."""

    def setUp(self):
        self.buyer, self.profile = _buyer("adminperm_buyer", "9400000001")
        self.other, self.other_profile = _buyer("adminperm_other", "9400000002")
        self.staff = User.objects.create_user(username="adminperm_staff", email="adminperm_staff@example.com", password="pass12345", is_staff=True)

    def test_non_staff_cannot_open_admin_screens_or_deactivate_customers(self):
        self.client.login(username="adminperm_buyer@example.com", password="pass12345")
        for name in ("customers-list", "suppliers-list", "subscription-list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        response = self.client.post(reverse("delete-customer", kwargs={"pk": self.other_profile.pk}))
        self.assertEqual(response.status_code, 403)
        self.other_profile.refresh_from_db()
        self.assertFalse(self.other_profile.is_deleted)

    def test_staff_can_open_admin_screens(self):
        self.client.login(username="adminperm_staff@example.com", password="pass12345")
        for name in ("customers-list", "suppliers-list", "subscription-list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_buyer_can_edit_only_their_own_company(self):
        self.client.login(username="adminperm_buyer@example.com", password="pass12345")
        self.assertEqual(self.client.get(reverse("edit-customer", kwargs={"pk": self.profile.pk})).status_code, 200)
        self.assertEqual(self.client.get(reverse("edit-customer", kwargs={"pk": self.other_profile.pk})).status_code, 403)


@DASHBOARD_TEST_STORAGES
class StaffActionSecurityTests(TestCase):
    """Staff changes to other accounts are audited and need a recently
    entered password; deactivating a supplier now also blocks their login;
    a deactivated supplier's profile is no longer reachable by id."""

    def setUp(self):
        self.buyer, self.profile = _buyer("staffsec_buyer", "9410000001")
        self.maker = User.objects.create_user(username="staffsec_maker", email="staffsec_maker@example.com", password="pass12345", role="manufacturer")
        self.supplier = ManufacturerProfile.objects.create(
            user=self.maker, companyname="Staffsec Maker", phone="8410000001", address="1 Rd", city="Pune", state="MH",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="staffsec_maker@example.com",
        )
        self.staff = User.objects.create_user(username="staffsec_staff", email="staffsec_staff@example.com", password="pass12345", is_staff=True)
        self.client.login(username="staffsec_staff@example.com", password="pass12345")

    def _expire_recent_auth(self):
        from core.session_security import AUTH_AT
        session = self.client.session
        session[AUTH_AT] = 0  # signed in long ago
        session.save()

    def test_deactivating_a_supplier_blocks_their_login_and_is_audited(self):
        from core.models import AuditLogEntry
        self.client.post(reverse("delete-supplier", kwargs={"pk": self.supplier.pk}))
        self.maker.refresh_from_db()
        self.assertFalse(self.maker.is_active)
        entry = AuditLogEntry.objects.get(action="supplier.deactivate")
        self.assertEqual((entry.actor, entry.target_id), (self.staff, str(self.supplier.pk)))
        self.assertFalse(Client().login(username="staffsec_maker@example.com", password="pass12345"))

    def test_customer_deactivation_and_subscription_edits_are_audited(self):
        from core.models import AuditLogEntry
        self.client.post(reverse("delete-customer", kwargs={"pk": self.profile.pk}))
        self.assertTrue(AuditLogEntry.objects.filter(action="customer.deactivate", target_id=str(self.profile.pk)).exists())

    def test_staff_actions_need_a_recent_password(self):
        self._expire_recent_auth()
        response = self.client.post(reverse("delete-supplier", kwargs={"pk": self.supplier.pk}))
        self.assertTrue(response["Location"].startswith(reverse("reauth")))
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_deleted)
        # Confirming the password lets the same action through.
        self.client.post(reverse("reauth"), {"password": "pass12345", "next": reverse("suppliers-list")})
        self.client.post(reverse("delete-supplier", kwargs={"pk": self.supplier.pk}))
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_deleted)

    def test_wrong_reauth_password_is_refused_and_counted(self):
        self._expire_recent_auth()
        self.client.post(reverse("reauth"), {"password": "wrong", "next": "/"})
        self.client.post(reverse("delete-supplier", kwargs={"pk": self.supplier.pk}))
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_deleted)

    def test_audit_log_is_read_only_in_the_admin(self):
        from core.admin import AuditLogEntryAdmin
        from core.models import AuditLogEntry
        from django.contrib import admin
        model_admin = AuditLogEntryAdmin(AuditLogEntry, admin.site)
        request = type("R", (), {"user": self.staff})()
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))

    def test_deactivated_supplier_profile_is_hidden_except_from_staff(self):
        self.supplier.is_deleted = True
        self.supplier.save()
        url = reverse("supplier", kwargs={"pk": self.supplier.pk})
        self.assertEqual(self.client.get(url).status_code, 200)  # staff
        self.client.login(username="staffsec_buyer@example.com", password="pass12345")
        self.assertEqual(self.client.get(url).status_code, 404)


@DASHBOARD_TEST_STORAGES
class BuyerRegistrationBindingTests(TestCase):
    """The public buyer-profile step used to expose a `user` dropdown listing
    every account without a profile, and trusted whichever one was posted."""

    def setUp(self):
        self.new_user = User.objects.create_user(username="fresh", email="fresh@example.com", password="pass12345", email_verified=True)
        self.victim = User.objects.create_user(username="victim2", email="victim2@example.com", password="pass12345")
        session = self.client.session
        session["session_user_id"] = self.new_user.id
        session.save()
        verify_gstin_for_session(self.client, legal_name="Fresh Legal Pvt Ltd", trade_name="Fresh Co")

    def _payload(self, **extra):
        data = {
            "type_of_business": "electronics", "phone": "9400000009",
            "email": "fresh@example.com", "EORI_number": "EF", "VAT_number": "VF",
        }
        data.update(extra)
        return data

    def test_page_does_not_list_other_users(self):
        response = self.client.get(reverse("register-customer"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "victim2")

    def test_profile_is_attached_to_the_session_user_even_if_another_is_posted(self):
        self.client.post(reverse("register-customer"), self._payload(user=self.victim.pk))
        profile = ConsumerProfile.objects.get(email="fresh@example.com")
        self.assertEqual(profile.user, self.new_user)
        self.assertFalse(ConsumerProfile.objects.filter(user=self.victim).exists())
        self.assertNotIn("session_user_id", self.client.session)

    def test_without_a_pending_account_the_step_redirects_to_register(self):
        self.client.session.flush()
        client = Client()
        self.assertRedirects(client.get(reverse("register-customer")), reverse("register"), fetch_redirect_response=False)


@DASHBOARD_TEST_STORAGES
class BuyerCompanyVerificationTests(TestCase):
    """Buyers must verify their company's GSTIN before the profile step, the
    same way suppliers do (accounts.views.CreateCustomer.form_valid)."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="gstbuyer", email="gstbuyer@example.com", password="pass12345", role="consumer", email_verified=True,
        )
        session = self.client.session
        session["session_user_id"] = self.user.id
        session.save()
        self.url = reverse("register-customer")

    def _payload(self, **extra):
        data = {
            "type_of_business": "electronics", "phone": "9400000021",
            "email": "gstbuyer@example.com", "EORI_number": "EB1", "VAT_number": "VB1",
        }
        data.update(extra)
        return data

    def _verify(self, gstin=ACTIVE_GSTIN):
        return verify_gstin_for_session(
            self.client, gstin, legal_name="Buyer Legal Pvt Ltd", trade_name="Buyer Trade", state="Karnataka",
            principal_address={"address": "7 Buyer Street", "city": "Bengaluru"},
        )

    def test_registration_without_verification_is_rejected(self):
        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "verify your company")
        self.assertFalse(ConsumerProfile.objects.exists())

    def test_name_and_address_come_from_the_verification_not_the_post(self):
        self._verify()
        self.client.post(self.url, self._payload(Name="Made Up Name", Address="Made up address"))
        profile = ConsumerProfile.objects.get(user=self.user)
        self.assertEqual(profile.company.legal_name, "Buyer Legal Pvt Ltd")
        self.assertEqual(profile.Name, "Buyer Trade")
        self.assertEqual(profile.Address, "7 Buyer Street")
        self.assertEqual(profile.city, "Bengaluru")
        self.assertEqual(profile.state, "Karnataka")
        self.assertNotIn(GST_SESSION_KEY, self.client.session)

    def test_a_supplier_company_signing_up_as_a_buyer_reuses_its_company(self):
        supplier_user = User.objects.create_user(username="sellerco", email="sellerco@example.com", password="pass12345", role="manufacturer")
        company = Company.objects.create(name="Acme", legal_name="Old Legal Name", gstin=ACTIVE_GSTIN, gst_verified=True)
        ManufacturerProfile.objects.create(
            user=supplier_user, company=company, phone="9400000023", address="x", city="Chennai", state="TN",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="sellerco@example.com",
        )
        self._verify()
        self.client.post(self.url, self._payload())
        profile = ConsumerProfile.objects.get(user=self.user)
        self.assertEqual(profile.company, company)
        company.refresh_from_db()
        self.assertEqual(company.legal_name, "Buyer Legal Pvt Ltd")  # refreshed from the new verification
        self.assertEqual(company.name, "Acme")  # the existing display name stays

    def test_gstin_already_used_by_another_buyer_is_rejected(self):
        self._verify()
        company = Company.objects.create(legal_name="Buyer Legal Pvt Ltd", gstin=ACTIVE_GSTIN, gst_verified=True)
        other = User.objects.create_user(username="firstbuyer", email="firstbuyer@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=other, company=company, Name="First", type_of_business="electronics", city="X", state="Y",
            country="India", phone="9400000022", email="firstbuyer@example.com", EORI_number="E0", VAT_number="V0",
        )
        response = self.client.post(self.url, self._payload())
        self.assertContains(response, "already registered")
        self.assertFalse(ConsumerProfile.objects.filter(user=self.user).exists())

@DASHBOARD_TEST_STORAGES
class SubscriptionUpgradeTests(TestCase):
    def setUp(self):
        from accounts.models import SubscriptionPlan
        self.SubscriptionPlan = SubscriptionPlan
        self.buyer, _ = _buyer("planner", "9400000011")
        self.plan = SubscriptionPlan.objects.create(user_profile=self.buyer, plan_type="starter", price=999)
        self.client.login(username="planner@example.com", password="pass12345")

    def test_paid_upgrade_is_pending_until_staff_apply_it(self):
        self.client.post(reverse("subscription-upgrade"), {"plan_type": "business", "billing_cycle": "yearly"})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.plan_type, "starter")
        self.assertEqual((self.plan.pending_plan_type, self.plan.pending_billing_cycle), ("business", "yearly"))
        self.assertContains(self.client.get(reverse("profile") + "?tab=billing"), "Awaiting payment")

    def test_switching_to_yearly_billing_waits_for_payment_too(self):
        self.client.post(reverse("subscription-upgrade"), {"plan_type": "starter", "billing_cycle": "yearly"})
        self.plan.refresh_from_db()
        self.assertEqual((self.plan.billing_cycle, self.plan.pending_plan_type, self.plan.pending_billing_cycle), ("monthly", "starter", "yearly"))

    def test_moving_to_a_cheaper_plan_applies_immediately(self):
        self.client.post(reverse("subscription-upgrade"), {"plan_type": "free"})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.plan_type, "free")
        self.assertEqual(self.plan.price, 0)

    def test_staff_setting_the_plan_clears_the_pending_request(self):
        self.client.post(reverse("subscription-upgrade"), {"plan_type": "business"})
        User.objects.create_user(username="planstaff", email="planstaff@example.com", password="pass12345", is_staff=True)
        self.client.login(username="planstaff@example.com", password="pass12345")
        self.client.post(reverse("edit-subscription", kwargs={"pk": self.plan.pk}), {"plan_type": "business", "billing_cycle": "yearly", "is_active": "on"})
        self.plan.refresh_from_db()
        self.assertEqual((self.plan.plan_type, self.plan.billing_cycle, self.plan.price), ("business", "yearly", 29990))
        self.assertEqual(self.plan.pending_plan_type, "")

    def test_next_cannot_redirect_off_site(self):
        response = self.client.post(reverse("subscription-upgrade"), {"plan_type": "free", "next": "https://evil.example.com/"})
        self.assertEqual(response["Location"], reverse("profile") + "?tab=billing")


@override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
})
class SupplierRegistrationBindingTests(TestCase):
    """SupplierDetailsForm carries a hidden `user` field; the view must
    ignore it and use the account from the session."""

    def test_posted_user_is_ignored(self):
        cache.clear()
        me = User.objects.create_user(username="mfgme", email="mfgme@example.com", password="pass12345", role="manufacturer", email_verified=True)
        someone = User.objects.create_user(username="mfgother", email="mfgother@example.com", password="pass12345", role="manufacturer")
        session = self.client.session
        session["session_user_id"] = me.id
        session.save()
        verify_gstin_for_session(self.client)
        self.client.post(reverse("register-supplier"), {
            "user": someone.id, "email": "mfgme-co@example.com", "phone": "7000000001",
            "amount_of_employees": "10-20", "turnover_per_year": "<1",
        })
        self.assertTrue(ManufacturerProfile.objects.filter(user=me).exists())
        self.assertFalse(ManufacturerProfile.objects.filter(user=someone).exists())


@DASHBOARD_TEST_STORAGES
class SettingsContactTabTests(TestCase):
    """Contact us lives in its own Settings tab for buyers and suppliers,
    not as a card under every other tab."""

    def test_supplier_contact_tab_prefills_their_details(self):
        _make_manufacturer("contact_mfg", "contact_mfg@example.com")
        self.client.login(username="contact_mfg@example.com", password="pass12345")
        response = self.client.get(reverse("profile") + "?tab=contact")
        self.assertContains(response, "Send us a message")
        self.assertContains(response, "contact_mfg Co")
        self.assertContains(response, "role: 'Manufacturer looking for RFQs'")

    def test_buyer_contact_tab_prefills_their_details(self):
        _buyer("contact_buyer", "9400000031")
        self.client.login(username="contact_buyer@example.com", password="pass12345")
        response = self.client.get(reverse("profile") + "?tab=contact")
        self.assertContains(response, "Send us a message")
        self.assertContains(response, "contact_buyer Co")
        self.assertContains(response, "role: 'Buyer looking for parts'")

    def test_profile_tab_no_longer_shows_the_contact_card(self):
        _buyer("contact_buyer2", "9400000032")
        self.client.login(username="contact_buyer2@example.com", password="pass12345")
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "?tab=contact")
        self.assertNotContains(response, "Still can't find what you're looking for?")
        self.assertNotContains(response, "Send us a message")


@DASHBOARD_TEST_STORAGES
class SupplierProfileLayoutTests(TestCase):
    """View profile from Find manufacturers used to render in the public
    site layout, so it looked like the buyer had been sent out of the portal."""

    def setUp(self):
        _, self.supplier = _make_manufacturer("layout_mfg", "layout_mfg@example.com")

    def test_buyer_sees_profile_inside_the_dashboard(self):
        _buyer("layout_buyer", "9400000041")
        self.client.login(username="layout_buyer@example.com", password="pass12345")
        response = self.client.get(reverse("supplier", kwargs={"pk": self.supplier.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard_base.html")
        self.assertContains(response, "layout_mfg Co")
        self.assertContains(response, reverse("supplier-directory"))

    def test_staff_keep_the_admin_layout(self):
        User.objects.create_user(username="layout_staff", email="layout_staff@example.com", password="pass12345", is_staff=True)
        self.client.login(username="layout_staff@example.com", password="pass12345")
        response = self.client.get(reverse("supplier", kwargs={"pk": self.supplier.pk}))
        self.assertTemplateUsed(response, "base.html")
        self.assertTemplateNotUsed(response, "dashboard_base.html")


@DASHBOARD_TEST_STORAGES
class ProfileCompanyTabTests(TestCase):
    """Settings > Company used to only have a supplier branch, so buyers
    were told their "manufacturer profile isn't set up yet"."""

    def _company(self, gstin):
        return Company.objects.create(
            legal_name="TEJAS ELECTRONICS PRIVATE LIMITED", trade_name="Tejas Electronics", gstin=gstin,
            gst_status="Active", gst_verified=True, gst_verified_at=timezone.now(), pincode="501510",
            principal_address="Plot 9, Hardware Park", state="Telangana", city="Hyderabad",
            business_constitution="Private Limited Company",
        )

    def test_buyer_sees_their_verified_company(self):
        company = self._company("36AAHCT9753L1Z4")
        user = User.objects.create_user(username="tabbuyer", email="tabbuyer@example.com", password="pass12345", role="consumer")
        ConsumerProfile.objects.create(
            user=user, company=company, Name="Tejas Electronics", type_of_business="electronics",
            Address="Plot 9, Hardware Park", city="Hyderabad", state="Telangana", country="India",
            phone="9400000031", email="tabbuyer@example.com", EORI_number="ET", VAT_number="VT",
        )
        self.client.login(username="tabbuyer@example.com", password="pass12345")
        response = self.client.get(reverse("profile") + "?tab=company")
        self.assertNotContains(response, "isn't set up yet")
        for text in ("TEJAS ELECTRONICS PRIVATE LIMITED", "36AAHCT9753L1Z4", "Verified", "501510", "Hyderabad", "Private Limited"):
            self.assertContains(response, text)

    def test_supplier_sees_gstin_and_legal_name(self):
        company = self._company("33AABCS1234K1Z7")
        user = User.objects.create_user(username="tabmaker", email="tabmaker@example.com", password="pass12345", role="manufacturer")
        ManufacturerProfile.objects.create(
            user=user, company=company, companyname="Tejas Electronics", phone="9400000032", address="Plot 9",
            city="Hyderabad", state="Telangana", country="India", amount_of_employees="10-20",
            turnover_per_year="<1", email="tabmaker@example.com",
        )
        self.client.login(username="tabmaker@example.com", password="pass12345")
        response = self.client.get(reverse("profile") + "?tab=company")
        for text in ("TEJAS ELECTRONICS PRIVATE LIMITED", "33AABCS1234K1Z7", "Verified", "501510"):
            self.assertContains(response, text)

    def test_buyer_without_a_profile_is_not_called_a_manufacturer(self):
        User.objects.create_user(username="tabnoprof", email="tabnoprof@example.com", password="pass12345", role="consumer")
        self.client.login(username="tabnoprof@example.com", password="pass12345")
        response = self.client.get(reverse("profile") + "?tab=company")
        self.assertContains(response, "buyer profile isn't set up yet")
        self.assertNotContains(response, "manufacturer profile")


@DASHBOARD_TEST_STORAGES
class ProfileStepEmailPrefillTests(TestCase):
    """After the OTP step, the profile step's email field starts with the
    address that was just verified, for both roles."""

    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def _register_and_verify(self, email, account_type):
        self.client.post(reverse("register"), {
            "username": email.split("@")[0], "first_name": "Test", "last_name": "User",
            "password1": "a-strong-passw0rd", "password2": "a-strong-passw0rd",
            "email": email, "account_type": account_type,
        })
        code = next(l.strip() for l in mail.outbox[-1].body.splitlines() if l.strip().isdigit() and len(l.strip()) == 6)
        return self.client.post(reverse("verify-email"), {"code": code}, follow=True)

    def test_buyer_profile_step_prefills_the_verified_email(self):
        response = self._register_and_verify("prefillbuyer@example.com", "buyer")
        self.assertContains(response, 'value="prefillbuyer@example.com"')

    def test_supplier_profile_step_prefills_the_verified_email(self):
        response = self._register_and_verify("prefillmaker@example.com", "supplier")
        self.assertContains(response, 'value="prefillmaker@example.com"')
