from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, ConsumerProfile
from core.models import User
from gst.models import GSTVerification
from gst.views import SESSION_KEY as GST_SESSION_KEY
from plans import erp

from . import access, catalog
from .models import Address, BusinessRelationship, Membership, Permission, Role, Team, TeamMembership
from .services import pan_from_gstin, set_up_organization

TEST_STORAGES = override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
})
GSTIN = "33AAAAA0000A1Z5"
PASSWORD = "A-strong-passw0rd"


def verify_gstin_for_session(client, gstin=GSTIN, **fields):
    """What gst.views.verify_gstin leaves behind: a successful
    GSTVerification remembered in the session."""
    values = dict(
        gstin=gstin, provider="mock", status=GSTVerification.Status.SUCCESS,
        legal_name="Vaigai Precision Works Private Limited", trade_name="Vaigai Precision", gst_status="Active",
        business_constitution="Private Limited Company", taxpayer_type="Regular", state="Tamil Nadu",
        state_code=gstin[:2], pincode="625001", verified_at=timezone.now(),
        principal_address={"address": "12 Industrial Estate, Madurai - 625001", "city": "Madurai"},
    )
    values.update(fields)
    verification = GSTVerification.objects.create(**values)
    session = client.session
    session[GST_SESSION_KEY] = verification.pk
    session.save()
    return verification


def make_organization(name, gstin, owner_email, *, buying=True, selling=True, plan=erp.STARTER):
    company = Company.objects.create(name=name, legal_name=f"{name} Private Limited", gstin=gstin, gst_verified=True)
    owner = User.objects.create_user(username=owner_email, email=owner_email, password=PASSWORD, role="member")
    membership = set_up_organization(company, owner, buying=buying, selling=selling, plan=plan)
    return company, membership


def add_member(company, email, role_key):
    user = User.objects.create_user(username=email, email=email, password=PASSWORD, role="member")
    return Membership.objects.create(user=user, company=company, role=company.roles.get(key=role_key))


@TEST_STORAGES
class OrganizationSignUpStepTests(TestCase):
    """Sign-up step three. Like the old buyer/supplier steps, the browser
    can't assert a company identity: only this session's server-side GSTIN
    check counts, and the organization goes to the session's user."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="owner@example.com", email="owner@example.com", password=PASSWORD, role="member",
            first_name="Meena", email_verified=True,
        )
        self.other = User.objects.create_user(username="other@example.com", email="other@example.com", password=PASSWORD)
        session = self.client.session
        session["session_user_id"] = self.user.pk
        session.save()
        self.url = reverse("register-organization")

    def payload(self, **extra):
        data = {"buying_enabled": "on", "selling_enabled": "on", "plan": "growth"}
        data.update(extra)
        return data

    def test_needs_a_signing_up_user(self):
        anonymous = self.client_class()
        self.assertRedirects(anonymous.get(self.url), reverse("register"), fetch_redirect_response=False)
        session = anonymous.session
        session["pending_registration_id"] = 123
        session.save()
        self.assertRedirects(anonymous.get(self.url), reverse("verify-email"), fetch_redirect_response=False)

    def test_page_waits_for_the_gstin_and_reshows_this_sessions_check(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Welcome, Meena")
        self.assertContains(page, 'id="registration-submit-btn" class="btn-primary" disabled')
        self.assertNotContains(page, "other@example.com")
        verify_gstin_for_session(self.client)
        page = self.client.get(self.url)
        self.assertContains(page, "Vaigai Precision Works Private Limited")
        self.assertNotContains(page, 'id="registration-submit-btn" class="btn-primary" disabled')

    def test_without_a_verified_gstin_nothing_is_created(self):
        response = self.client.post(self.url, self.payload(gstin=GSTIN, legal_name="Made Up Ltd", gst_verified="true"))
        self.assertContains(response, "verify your company")
        self.assertFalse(Company.objects.exists())

    def test_stale_or_inactive_checks_are_refused(self):
        stale = verify_gstin_for_session(self.client)
        GSTVerification.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(hours=2))
        self.assertContains(self.client.post(self.url, self.payload()), "verify your company")
        verify_gstin_for_session(self.client, status=GSTVerification.Status.INACTIVE, gst_status="Cancelled")
        self.assertContains(self.client.post(self.url, self.payload()), "verify your company")
        self.assertFalse(Membership.objects.exists())

    def test_sets_up_the_organization_with_the_user_as_org_admin(self):
        verification = verify_gstin_for_session(self.client)
        response = self.client.post(self.url, self.payload(
            user=self.other.pk, legal_name="Something Else", principal_address="Elsewhere",
            company_display_name="  Vaigai   Works ",
        ))
        self.assertRedirects(response, "/", fetch_redirect_response=False)

        company = Company.objects.get(gstin=GSTIN)
        self.assertEqual(company.legal_name, "Vaigai Precision Works Private Limited")
        self.assertEqual(company.name, "Vaigai Works")
        self.assertEqual(company.pan, "AAAAA0000A")
        self.assertEqual((company.buying_enabled, company.selling_enabled, company.erp_plan), (True, True, "growth"))
        self.assertTrue(company.is_organization)
        address = company.addresses.get()
        self.assertEqual((address.kind, address.line1, address.state_code), (Address.Kind.REGISTERED, "12 Industrial Estate, Madurai - 625001", "33"))

        self.assertEqual(set(company.roles.values_list("key", flat=True)), set(catalog.DEFAULT_ROLES))
        membership = Membership.objects.get()
        self.assertEqual((membership.user, membership.company, membership.role.key), (self.user, company, catalog.ORG_ADMIN))
        self.assertEqual(membership.role.permissions.count(), len(catalog.PERMISSIONS))
        self.assertFalse(Membership.objects.filter(user=self.other).exists())

        verification.refresh_from_db()
        self.assertEqual(verification.company, company)
        self.assertNotIn(GST_SESSION_KEY, self.client.session)
        self.assertNotIn("session_user_id", self.client.session)
        # One sign-up, one organization: the step is closed now.
        self.assertRedirects(self.client.get(self.url), reverse("register"), fetch_redirect_response=False)

    def test_one_side_is_enough_but_not_none(self):
        verify_gstin_for_session(self.client)
        response = self.client.post(self.url, {"plan": "starter"})
        self.assertContains(response, "Switch on buying, selling or both.")
        self.assertFalse(Company.objects.exists())
        self.client.post(self.url, {"plan": "starter", "buying_enabled": "on"})
        company = Company.objects.get()
        self.assertEqual((company.buying_enabled, company.selling_enabled), (True, False))

    def test_unknown_plan_is_refused(self):
        verify_gstin_for_session(self.client)
        self.client.post(self.url, self.payload(plan="platinum"))
        self.assertFalse(Company.objects.exists())

    def test_a_company_already_on_makesetu_cant_register_twice(self):
        make_organization("Existing", GSTIN, "admin@existing.example")
        verify_gstin_for_session(self.client)
        response = self.client.post(self.url, self.payload())
        self.assertContains(response, "already registered on MakeSetu")
        self.assertFalse(Membership.objects.filter(user=self.user).exists())

    def test_a_company_with_a_marketplace_account_cant_register_either(self):
        company = Company.objects.create(legal_name="Buyer Co", gstin=GSTIN, gst_verified=True)
        ConsumerProfile.objects.create(
            user=self.other, company=company, Name="Buyer Co", type_of_business="electronics", city="Pune", state="MH",
            country="India", phone="9400000001", email="buyer@example.com", EORI_number="E1", VAT_number="V1",
        )
        verify_gstin_for_session(self.client)
        self.assertContains(self.client.post(self.url, self.payload()), "already registered on MakeSetu")

    def test_gstin_check_is_open_to_the_signing_up_user_only(self):
        url = reverse("gst-verify")
        body = '{"gstin": "%s"}' % GSTIN
        self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 200)
        self.assertEqual(self.client_class().post(url, body, content_type="application/json").status_code, 403)


@TEST_STORAGES
class SignUpEndToEndTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def test_from_register_to_the_app(self):
        self.client.get(reverse("register") + "?plan=pro")
        response = self.client.post(reverse("register"), {
            "first_name": "Meena", "last_name": "Raman", "email": "meena@vaigai.example", "phone": "9876543210",
            "password1": PASSWORD, "password2": PASSWORD,
        })
        self.assertRedirects(response, reverse("verify-email"))
        code = next(l.strip() for l in mail.outbox[-1].body.splitlines() if l.strip().isdigit() and len(l.strip()) == 6)
        self.assertRedirects(self.client.post(reverse("verify-email"), {"code": code}), reverse("register-organization"))
        verify_gstin_for_session(self.client)
        self.client.post(reverse("register-organization"), {"buying_enabled": "on", "selling_enabled": "on", "plan": "pro"})

        self.assertTrue(self.client.login(username="meena@vaigai.example", password=PASSWORD))
        self.assertRedirects(self.client.get(reverse("home")), reverse("erp-app"), fetch_redirect_response=False)
        me = self.client.get("/api/v1/me").json()
        self.assertEqual(me["user"]["phone"], "9876543210")
        active = me["active"]
        self.assertEqual((active["company"]["legal_name"], active["company"]["pan"], active["company"]["plan"]),
                         ("Vaigai Precision Works Private Limited", "AAAAA0000A", "pro"))
        self.assertEqual(active["role"]["name"], "Org Admin")
        self.assertEqual(active["subscription"]["status"], "trialing")


class AccessCheckTests(TestCase):
    """organizations.access.check: role, then side and plan, then the
    relationship with the other company."""

    def setUp(self):
        self.company, self.admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example", plan=erp.STARTER)
        self.supplier, _ = make_organization("Annai", "33BBBBB0000B1Z5", "admin@annai.example")
        self.purchase = add_member(self.company, "buyer@vaigai.example", "purchase_executive")

    def test_role_must_have_the_permission(self):
        self.assertTrue(access.check(self.purchase, "po.create"))
        decision = access.check(self.purchase, "so.create")
        self.assertFalse(decision)
        self.assertIn("Purchase Executive", decision.reason)

    def test_the_side_must_be_switched_on(self):
        Company.objects.filter(pk=self.company.pk).update(buying_enabled=False)
        self.purchase.company.refresh_from_db()
        decision = access.check(self.purchase, "po.create")
        self.assertFalse(decision)
        self.assertIn("Buying isn't switched on", decision.reason)

    def test_the_plan_must_include_the_feature(self):
        self.assertTrue(access.check(self.admin, "bom.manage"))  # single-level BOM is on every plan
        decision = access.check(self.admin, "work_order.manage")  # Growth and up
        self.assertEqual((decision.allowed, decision.reason), (False, "This needs the Growth plan."))
        self.assertIn("Growth", access.check(self.admin, "po.approve").reason)  # basic approvals: Growth
        Company.objects.filter(pk=self.company.pk).update(erp_plan=erp.GROWTH)
        self.admin.company.refresh_from_db()
        self.assertTrue(access.check(self.admin, "work_order.manage"))
        self.assertTrue(access.check(self.admin, "po.approve"))

    def test_the_other_company_needs_an_active_relationship_in_the_right_direction(self):
        self.assertIn("isn't an active supplier", access.check(self.purchase, "po.create", self.supplier).reason)
        relationship = BusinessRelationship.objects.create(
            buyer=self.company, supplier=self.supplier, initiated_by=self.company, status=BusinessRelationship.Status.PENDING_APPROVAL,
        )
        self.assertFalse(access.check(self.purchase, "po.create", self.supplier))
        relationship.status = BusinessRelationship.Status.ACTIVE
        relationship.save()
        self.assertTrue(access.check(self.purchase, "po.create", self.supplier))
        # Buying from them doesn't make them a customer.
        self.assertIn("isn't an active customer", access.check(self.admin, "so.create", self.supplier).reason)

    def test_inactive_members_and_unknown_permissions_are_refused(self):
        self.purchase.is_active = False
        self.purchase.save()
        self.assertFalse(access.check(self.purchase, "po.create"))
        self.assertFalse(access.check(self.admin, "po.teleport"))
        self.assertFalse(access.check(None, "po.create"))

    def test_can_looks_up_the_membership(self):
        self.assertTrue(access.can(self.purchase.user, self.company, "po.create"))
        self.assertFalse(access.can(self.purchase.user, self.supplier, "po.create"))  # not a member there


class LightUserTests(TestCase):
    """Light users (shop floor, support staff) can only ever do the few
    things in catalog.LIGHT_PERMISSIONS, and are counted apart from full
    users."""

    def setUp(self):
        self.company, self.admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example", plan=erp.GROWTH)

    def test_default_light_roles_hold_only_light_permissions(self):
        light = self.company.roles.filter(user_type=catalog.LIGHT)
        self.assertEqual(set(light.values_list("key", flat=True)),
                         {"store_keeper", "machine_operator", "qc_inspector", "security", "field_sales"})
        for role in light:
            self.assertTrue(set(role.permissions.values_list("code", flat=True)) <= catalog.LIGHT_PERMISSIONS, role.key)

    def test_a_light_role_cant_be_given_a_full_permission(self):
        from django.core.exceptions import ValidationError
        operator = self.company.roles.get(key="machine_operator")
        approve = Permission.objects.get(code="po.approve")
        with transaction.atomic(), self.assertRaises(ValidationError):
            operator.permissions.add(approve)
        with transaction.atomic(), self.assertRaises(ValidationError):
            approve.roles.add(operator)  # from the other side of the relation
        operator.permissions.add(Permission.objects.get(code="stock.view"))  # a light permission is fine
        accounts = self.company.roles.get(key="accounts")
        accounts.user_type = catalog.LIGHT
        with transaction.atomic(), self.assertRaises(ValidationError):
            accounts.save()  # a full role with finance permissions can't be turned light

    def test_the_check_refuses_full_actions_even_if_the_role_somehow_has_them(self):
        operator = add_member(self.company, "op@vaigai.example", "machine_operator")
        self.assertTrue(access.check(operator, "production.record"))
        Role.permissions.through.objects.create(role=operator.role, permission=Permission.objects.get(code="po.approve"))
        decision = access.check(operator, "po.approve")
        self.assertEqual((decision.allowed, decision.reason), (False, "Light users can't do this; it needs a full user."))

    def test_seats_count_full_and_light_users_separately(self):
        from .workspace import seat_usage
        add_member(self.company, "buyer@vaigai.example", "purchase_manager")
        for i in range(22):
            add_member(self.company, f"op{i}@vaigai.example", "machine_operator")
        gone = add_member(self.company, "left@vaigai.example", "store_keeper")
        gone.is_active = False
        gone.save()
        self.assertEqual(seat_usage(self.company), {
            "full": 2, "full_invited": 0, "full_included": 10, "light": 22, "light_invited": 0, "light_included": 20,
            "extra_light": 2, "extra_light_monthly": 150, "teams": 0, "teams_included": 5,
        })


class TeamTests(TestCase):
    """Teams are departments inside an organization. The plan limits how
    many there are; users are one pool, counted once each however many
    teams they're in."""

    def setUp(self):
        self.company, self.admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example", plan=erp.STARTER)
        self.buyer = add_member(self.company, "buyer@vaigai.example", "purchase_manager")

    def test_starter_allows_two_teams_then_points_to_growth(self):
        from django.core.exceptions import ValidationError
        Team.objects.create(company=self.company, name="Purchase", head=self.buyer.user)
        Team.objects.create(company=self.company, name="Store")
        with self.assertRaises(ValidationError) as raised:
            Team.objects.create(company=self.company, name="Sales")
        self.assertIn("Your plan includes 2 teams. Move to Growth for more.", str(raised.exception))
        Company.objects.filter(pk=self.company.pk).update(erp_plan=erp.PRO)
        company = Company.objects.get(pk=self.company.pk)
        for name in ("Sales", "Production", "Accounts", "QC", "Maintenance"):
            Team.objects.create(company=company, name=name)  # Pro: no limit
        self.assertEqual(company.teams.count(), 7)

    def test_people_can_be_in_many_teams_and_count_once(self):
        from .workspace import seat_usage
        purchase = Team.objects.create(company=self.company, name="Purchase")
        store = Team.objects.create(company=self.company, name="Store")
        TeamMembership.objects.create(team=purchase, user=self.buyer.user)
        TeamMembership.objects.create(team=store, user=self.buyer.user)
        self.assertEqual(set(self.buyer.user.erp_teams.all()), {purchase, store})
        usage = seat_usage(self.company)
        self.assertEqual((usage["full"], usage["teams"], usage["teams_included"]), (2, 2, 2))

    def test_only_members_of_the_organization_can_join_or_head_a_team(self):
        from django.core.exceptions import ValidationError
        _, outsider = make_organization("Other", "33BBBBB0000B1Z5", "admin@other.example")
        with self.assertRaises(ValidationError):
            Team.objects.create(company=self.company, name="Purchase", head=outsider.user)
        team = Team.objects.create(company=self.company, name="Store")
        with self.assertRaises(ValidationError):
            TeamMembership.objects.create(team=team, user=outsider.user)

    def test_team_names_are_unique_per_organization(self):
        Team.objects.create(company=self.company, name="Store")
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Team.objects.create(company=self.company, name="Store")
        other, _ = make_organization("Other", "33BBBBB0000B1Z5", "admin@other.example")
        Team.objects.create(company=other, name="Store")  # another company may use the name


class ModelRuleTests(TestCase):
    def setUp(self):
        self.a, self.a_admin = make_organization("Alpha", GSTIN, "admin@alpha.example")
        self.b, _ = make_organization("Beta", "33BBBBB0000B1Z5", "admin@beta.example")

    def test_permissions_table_matches_the_catalogue(self):
        self.assertEqual(set(Permission.objects.values_list("code", flat=True)), set(catalog.PERMISSIONS))

    def test_two_companies_can_buy_from_each_other_once_per_direction(self):
        BusinessRelationship.objects.create(buyer=self.a, supplier=self.b, initiated_by=self.a, payment_terms_days=30)
        BusinessRelationship.objects.create(buyer=self.b, supplier=self.a, initiated_by=self.b, payment_terms_days=45)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BusinessRelationship.objects.create(buyer=self.a, supplier=self.b, initiated_by=self.b)

    def test_a_company_cant_be_its_own_supplier_and_a_stranger_cant_start_one(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BusinessRelationship.objects.create(buyer=self.a, supplier=self.a, initiated_by=self.a)
        c, _ = make_organization("Gamma", "33CCCCC0000C1Z5", "admin@gamma.example")
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BusinessRelationship.objects.create(buyer=self.a, supplier=self.b, initiated_by=c)

    def test_a_membership_cant_use_another_organizations_role(self):
        from django.core.exceptions import ValidationError
        user = User.objects.create_user(username="x@example.com", email="x@example.com", password=PASSWORD, role="member")
        with self.assertRaises(ValidationError):
            Membership.objects.create(user=user, company=self.a, role=self.b.roles.get(key="purchase_manager"))

    def test_one_person_can_work_in_two_organizations_with_different_roles(self):
        consultant = add_member(self.a, "consultant@example.com", "accounts")
        Membership.objects.create(user=consultant.user, company=self.b, role=self.b.roles.get(key="qc"))
        self.assertTrue(access.can(consultant.user, self.a, "ledger.view"))
        self.assertFalse(access.can(consultant.user, self.b, "ledger.view"))

    def test_pan_comes_from_the_gstin(self):
        self.assertEqual(pan_from_gstin("33aaaaa0000a1z5"), "AAAAA0000A")
        self.assertEqual(self.a.pan, "AAAAA0000A")


@TEST_STORAGES
class WorkspaceScopeTests(TestCase):
    """ERP members don't have marketplace profiles, so marketplace pages
    send them back to their workspace instead of erroring."""

    def setUp(self):
        self.company, self.admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example")
        self.client.login(username="admin@vaigai.example", password=PASSWORD)

    def test_marketplace_pages_redirect_home(self):
        for name in ("requirement-list", "orders-list", "profile", "team"):
            self.assertRedirects(self.client.get(reverse(name)), reverse("home"), msg_prefix=name,
                                 fetch_redirect_response=False)

    def test_home_sends_members_to_the_app_and_public_pages_stay_open(self):
        self.assertRedirects(self.client.get(reverse("home")), reverse("erp-app"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("pricing")).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/plans").status_code, 200)

    def test_the_app_knows_what_the_plan_holds_back(self):
        active = self.client.get("/api/v1/me").json()["active"]
        self.assertIn("po.create", active["allowed"])
        self.assertIn("work_order.manage", active["granted"])   # the Org Admin role has it...
        self.assertNotIn("work_order.manage", active["allowed"])  # ...but Starter doesn't include work orders


@TEST_STORAGES
class PublicPagesTests(TestCase):
    def test_tabs_before_login(self):
        page = self.client.get(reverse("home"))
        for name in ("home", "features", "how-it-works", "pricing", "about", "contact"):
            self.assertContains(page, f'href="{reverse(name)}"', msg_prefix=name)
        self.assertContains(page, "ERP for Indian manufacturers")
        self.assertContains(page, "Welcome Back")  # the login card stays on the landing page

    def test_features_lists_each_plan_from_the_catalogue(self):
        page = self.client.get(reverse("features"))
        for plan in erp.cards():
            self.assertContains(page, plan["label"])
            self.assertContains(page, f"&#8377;{plan['monthly']} / month")

    def test_register_has_no_account_type_or_username(self):
        page = self.client.get(reverse("register"))
        self.assertNotContains(page, 'name="account_type"')
        self.assertNotContains(page, 'name="username"')
        self.assertContains(page, 'name="phone"')


class ApiTests(TestCase):
    def test_public_endpoints(self):
        self.assertEqual(self.client.get("/api/v1/health").json(), {"status": "ok"})
        plans = self.client.get("/api/v1/plans").json()
        self.assertEqual([(p["key"], p["monthly_price_inr"], p["users"], p["light_users"], p["warehouses"], p["support"]) for p in plans],
                         [("starter", 1500, 3, 5, 1, "Email/chat"), ("growth", 4000, 10, 20, 3, "Phone"),
                          ("pro", 10000, 25, 50, 10, "Priority")])
        self.assertEqual({p["extra_light_user_monthly_inr"] for p in plans}, {150})
        self.assertEqual([p["teams"] for p in plans], [2, 5, None])
        self.assertNotIn("einvoice", plans[0]["features"])  # an add-on on Starter, not included
        self.assertIn("einvoice", plans[1]["features"])
        roles = {r["key"]: r for r in self.client.get("/api/v1/roles/defaults").json()}
        self.assertIn("po.create", roles["purchase_manager"]["permissions"])
        self.assertEqual((roles["machine_operator"]["user_type"], roles["accounts"]["user_type"]), ("light", "full"))
        self.assertEqual(len(self.client.get("/api/v1/permissions").json()), len(catalog.PERMISSIONS))

    def test_api_docs_are_for_staff(self):
        self.assertEqual(self.client.get("/api/v1/docs").status_code, 302)
        User.objects.create_user(username="staff@example.com", email="staff@example.com", password=PASSWORD, is_staff=True)
        self.client.login(username="staff@example.com", password=PASSWORD)
        self.assertEqual(self.client.get("/api/v1/openapi.json").status_code, 200)


class EmailJobTests(TestCase):
    def test_email_is_sent_inline_if_the_queue_is_down(self):
        from core import tasks
        mail.outbox.clear()
        with mock.patch.object(tasks.send_template_email_task, "delay", side_effect=ConnectionError("no broker")):
            tasks.queue_template_email("a@example.com", "emails/otp_verification_subject.txt",
                                       "emails/otp_verification.txt", {"name": "A", "code": "123456", "ttl_minutes": 10})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("123456", mail.outbox[0].body)
