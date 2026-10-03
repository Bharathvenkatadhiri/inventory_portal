import json
from datetime import date

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import Company, ConsumerProfile, ManufacturerProfile
from core.models import User
from gst.checks import gst_provider_check
from gst.exceptions import GSTInvalidResponse, GSTProviderTimeout
from gst.forms import GSTINForm
from gst.models import GSTVerification
from gst.services.providers import get_gst_provider
from gst.services.providers.base import GSTProvider, from_gstn_record
from gst.services.providers.mock import SAMPLE_COMPANIES, MockGSTProvider
from gst.services.providers.setu import SetuGSTProvider
from gst.services.verification import GSTVerificationService
from gst.views import SESSION_KEY

# The mock resolves GSTINs by their last character: '0' cancelled, '1'
# suspended, else active; a "00" state code isn't found.
ACTIVE_GSTIN = "33AAAAA0000A1Z5"
CANCELLED_GSTIN = "33AAAAA0000A1Z0"
UNKNOWN_GSTIN = "00AAAAA0000A1Z5"


class StubProvider(GSTProvider):
    name = 'stub'

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def verify(self, gstin):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class GSTINFormTests(TestCase):
    def clean(self, value):
        form = GSTINForm({'gstin': value})
        return form.cleaned_data['gstin'] if form.is_valid() else None

    def test_valid_gstin_is_normalized(self):
        self.assertEqual(self.clean("  33aaaaa0000a1z5 "), ACTIVE_GSTIN)
        self.assertEqual(self.clean("33AAA AA0000A1Z5"), ACTIVE_GSTIN)

    def test_malformed_gstins_are_rejected(self):
        for value in ("", "33AAAAA0000A1Z", "33AAAAA0000A1Y5", "not-a-gstin"):
            self.assertIsNone(self.clean(value), value)


class ProviderTests(TestCase):
    def test_every_sample_company_is_an_active_gstin_with_an_address(self):
        provider = MockGSTProvider()
        for gstin, (legal_name, *_rest) in SAMPLE_COMPANIES.items():
            self.assertTrue(GSTINForm({'gstin': gstin}).is_valid(), gstin)
            result = provider.verify(gstin)
            self.assertEqual(result['gst_status'], 'Active', gstin)
            self.assertEqual(result['legal_name'], legal_name)
            self.assertEqual(result['state_code'], gstin[:2])
            self.assertTrue(result['principal_address']['city'] and result['pincode'] and result['state'], gstin)

    def test_gstn_records_are_normalized(self):
        record = {
            'lgnm': 'ABC MANUFACTURING PRIVATE LIMITED', 'tradeNam': 'ABC Manufacturing', 'sts': 'ACTIVE',
            'rgdt': '01/07/2017', 'cxdt': '', 'dty': 'Regular', 'ctb': 'Private Limited Company',
            'pradr': {'addr': {'bno': '14', 'st': 'Mount Road', 'loc': 'Guindy', 'dst': 'Chennai',
                               'stcd': 'Tamil Nadu', 'pncd': '600032'}},
        }
        result = from_gstn_record('33ABCDE1234F1Z5', record, provider_reference='REQ-1')
        self.assertEqual(result['legal_name'], 'ABC MANUFACTURING PRIVATE LIMITED')
        self.assertEqual(result['gst_status'], 'Active')
        self.assertEqual(result['registration_date'], date(2017, 7, 1))
        self.assertIsNone(result['cancellation_date'])
        self.assertEqual(result['principal_address']['address'], '14, Mount Road, Guindy, Chennai - 600032')
        self.assertEqual((result['state'], result['state_code'], result['pincode']), ('Tamil Nadu', '33', '600032'))
        self.assertEqual(result['raw_response'], record)

    def test_a_gstn_record_without_a_legal_name_is_an_invalid_response(self):
        with self.assertRaises(GSTInvalidResponse):
            from_gstn_record('33ABCDE1234F1Z5', {'sts': 'Active'})

    def test_provider_comes_from_settings(self):
        self.assertIsInstance(get_gst_provider(), MockGSTProvider)
        with override_settings(GST_PROVIDER='setu'):
            self.assertIsInstance(get_gst_provider(), SetuGSTProvider)
            self.assertEqual(gst_provider_check(None)[0].id, 'gst.W001')
        with override_settings(GST_PROVIDER='nope'):
            with self.assertRaises(ImproperlyConfigured):
                get_gst_provider()
            self.assertEqual(gst_provider_check(None)[0].id, 'gst.E001')
        self.assertEqual(gst_provider_check(None), [])


class GSTVerificationServiceTests(TestCase):
    def test_active_gstin_is_a_success(self):
        verification = GSTVerificationService().verify(" 33aaaaa0000a1z5")
        self.assertEqual(verification.status, GSTVerification.Status.SUCCESS)
        self.assertEqual(verification.gstin, ACTIVE_GSTIN)
        self.assertEqual(verification.provider, 'mock')
        self.assertEqual(verification.gst_status, 'Active')
        self.assertIsNotNone(verification.verified_at)
        self.assertIsNone(verification.company)

    def test_unknown_gstin_is_invalid(self):
        verification = GSTVerificationService().verify(UNKNOWN_GSTIN)
        self.assertEqual(verification.status, GSTVerification.Status.INVALID)
        self.assertEqual(verification.error_code, 'GSTIN_NOT_FOUND')
        self.assertIsNone(verification.verified_at)

    def test_cancelled_gstin_is_inactive_but_keeps_what_gstn_said(self):
        verification = GSTVerificationService().verify(CANCELLED_GSTIN)
        self.assertEqual(verification.status, GSTVerification.Status.INACTIVE)
        self.assertEqual(verification.gst_status, 'Cancelled')
        self.assertEqual(verification.error_code, 'GSTIN_INACTIVE')
        self.assertTrue(verification.legal_name)

    def test_provider_failures_are_recorded_without_leaking_to_the_user(self):
        cases = [
            (GSTProviderTimeout("Setu HTTP 504 x-client-id abc"), 'GST_PROVIDER_TIMEOUT'),
            (RuntimeError("boom"), 'GST_PROVIDER_ERROR'),
        ]
        for error, code in cases:
            verification = GSTVerificationService(StubProvider(error=error)).verify(ACTIVE_GSTIN)
            self.assertEqual(verification.status, GSTVerification.Status.FAILED)
            self.assertEqual(verification.error_code, code)
            self.assertNotIn('x-client-id', json.dumps(verification.as_payload()))

    def test_a_result_missing_the_legal_name_is_a_failure(self):
        verification = GSTVerificationService(StubProvider(result={'success': True, 'gst_status': 'Active'})).verify(ACTIVE_GSTIN)
        self.assertEqual(verification.status, GSTVerification.Status.FAILED)
        self.assertEqual(verification.error_code, 'GST_PROVIDER_INVALID_RESPONSE')

    def test_unimplemented_provider_fails_gracefully(self):
        verification = GSTVerificationService(SetuGSTProvider()).verify(ACTIVE_GSTIN)
        self.assertEqual(verification.status, GSTVerification.Status.FAILED)
        self.assertEqual(verification.error_code, 'GST_PROVIDER_UNAVAILABLE')

    def test_reverifying_a_company_updates_its_current_state(self):
        company = Company.objects.create(legal_name="Old Name", gstin=CANCELLED_GSTIN, gst_verified=True, gst_status="Active")
        verification = GSTVerificationService().verify(CANCELLED_GSTIN, company=company)
        company.refresh_from_db()
        self.assertEqual(company.gst_status, 'Cancelled')
        self.assertFalse(company.gst_verified)
        self.assertEqual(company.gst_cancellation_date, date(2025, 3, 31))
        self.assertEqual(company.legal_name, "Old Name")  # an inactive result doesn't rewrite the identity
        self.assertEqual(list(company.gst_verifications.all()), [verification])

    def test_successful_verification_fills_the_company(self):
        company = Company(name="Kaveri")
        verification = GSTVerificationService().verify("27AADCK5678M1Z3", company=company)
        company.refresh_from_db()
        self.assertEqual(company.legal_name, "KAVERI CASTINGS PRIVATE LIMITED")
        self.assertEqual(company.name, "Kaveri")
        self.assertTrue(company.gst_verified)
        self.assertEqual((company.city, company.state, company.state_code, company.pincode), ("Pune", "Maharashtra", "27", "411026"))
        self.assertEqual(company.principal_address, "Plot 22, MIDC Bhosari, Pune - 411026")
        self.assertEqual(company.business_constitution, "Private Limited Company")
        self.assertEqual(company.gst_verified_at, verification.verified_at)

    def test_a_failed_lookup_leaves_the_company_alone(self):
        company = Company.objects.create(legal_name="Kept", gstin=ACTIVE_GSTIN, gst_verified=True, gst_status="Active")
        GSTVerificationService(StubProvider(error=GSTProviderTimeout())).verify(ACTIVE_GSTIN, company=company)
        company.refresh_from_db()
        self.assertTrue(company.gst_verified)
        self.assertEqual(company.gst_status, "Active")


class VerifyGSTINViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("gst-verify")
        self.user = User.objects.create_user(username="signup", email="signup@example.com", password="pass12345", role="consumer")
        self.signing_up_as(self.user)

    def signing_up_as(self, user):
        session = self.client.session
        session["session_user_id"] = user.pk
        session.save()

    def post(self, gstin):
        return self.client.post(self.url, data=json.dumps({"gstin": gstin}), content_type="application/json")

    def test_active_gstin_is_verified_and_remembered_for_sign_up(self):
        response = self.post(ACTIVE_GSTIN)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(data["legal_name"], "BUSINESS AAAAA0000A PRIVATE LIMITED")
        self.assertNotIn("raw_response", data)
        verification = GSTVerification.objects.get()
        self.assertEqual(self.client.session[SESSION_KEY], verification.pk)
        self.assertFalse(Company.objects.exists())  # the sign-up step creates it

    def test_bad_format_is_rejected_before_any_lookup(self):
        response = self.post("garbage")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "INVALID_GSTIN_FORMAT")
        self.assertFalse(GSTVerification.objects.exists())

    def test_unknown_and_inactive_gstins_are_not_remembered(self):
        response = self.post(UNKNOWN_GSTIN)
        self.assertEqual((response.status_code, response.json()["status"]), (404, "INVALID"))
        response = self.post(CANCELLED_GSTIN)
        self.assertEqual((response.status_code, response.json()["status"]), (422, "INACTIVE"))
        self.assertEqual(response.json()["gst_status"], "Cancelled")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_a_new_attempt_forgets_the_previous_success(self):
        self.post(ACTIVE_GSTIN)
        self.post(CANCELLED_GSTIN)
        self.assertNotIn(SESSION_KEY, self.client.session)

    @override_settings(GST_PROVIDER="setu")
    def test_provider_outage_is_a_503_with_our_own_code(self):
        response = self.post(ACTIVE_GSTIN)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "GST_PROVIDER_UNAVAILABLE")
        self.assertEqual(response.json()["message"], "GST verification is temporarily unavailable. Please try again shortly.")

    def test_only_someone_signing_up_can_spend_lookups(self):
        self.client.session.flush()
        self.client.cookies.clear()
        self.assertEqual(self.post(ACTIVE_GSTIN).status_code, 403)
        self.assertFalse(GSTVerification.objects.exists())

    def test_repeated_requests_are_rate_limited(self):
        for _ in range(5):
            self.post(ACTIVE_GSTIN)
        self.assertEqual(self.post(ACTIVE_GSTIN).status_code, 429)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_gstin_another_buyer_has_is_refused_without_a_lookup(self):
        company = Company.objects.create(legal_name="Acme Engineering Private Limited", gstin=ACTIVE_GSTIN, gst_verified=True)
        other = User.objects.create_user(username="firstbuyer", email="firstbuyer@example.com", password="pass12345")
        ConsumerProfile.objects.create(
            user=other, company=company, Name="First", type_of_business="electronics", city="X", state="Y",
            country="India", phone="9400000024", email="firstbuyer@example.com", EORI_number="E2", VAT_number="V2",
        )
        response = self.post(ACTIVE_GSTIN)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "GSTIN_ALREADY_REGISTERED")
        self.assertFalse(GSTVerification.objects.exists())

    def test_a_buyer_can_use_a_gstin_a_supplier_already_has(self):
        # One company may both buy and sell; only the same role is a duplicate.
        supplier_user = User.objects.create_user(username="sellerco", email="sellerco@example.com", password="pass12345", role="manufacturer")
        company = Company.objects.create(legal_name="Acme Engineering Private Limited", gstin=ACTIVE_GSTIN, gst_verified=True)
        ManufacturerProfile.objects.create(
            user=supplier_user, company=company, phone="9400000023", address="x", city="Chennai", state="TN",
            country="India", amount_of_employees="10-20", turnover_per_year="<1", email="sellerco@example.com",
        )
        self.assertEqual(self.post(ACTIVE_GSTIN).status_code, 200)


class CompanyMigrationTests(TransactionTestCase):
    """accounts 0013 carries existing companies over to the new columns."""
    before = [('accounts', '0012_teams_and_approvals')]
    after = [('accounts', '0013_company_gst_identity')]

    def test_existing_companies_are_carried_over(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        OldCompany = executor.loader.project_state(self.before).apps.get_model('accounts', 'Company')
        OldCompany.objects.create(
            legal_name="KAVERI CASTINGS PRIVATE LIMITED", trade_name="Kaveri Castings", gstin="27AADCK5678M1Z3",
            gst_status="ACTIVE", gst_verified=True, registered_address="Plot 22, MIDC Bhosari", entity_type="private_limited",
            verification_status="verified", cin="U12345MH2019PTC000001",
        )
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.after)
        NewCompany = executor.loader.project_state(self.after).apps.get_model('accounts', 'Company')
        company = NewCompany.objects.get()
        self.assertEqual(company.name, "Kaveri Castings")
        self.assertEqual(company.principal_address, "Plot 22, MIDC Bhosari")
        self.assertEqual(company.business_constitution, "Private Limited Company")
        self.assertEqual(company.gst_status, "Active")
        self.assertEqual(company.state_code, "27")
        self.assertTrue(company.gst_verified)
        # Leave the database fully migrated for the tests that follow.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
