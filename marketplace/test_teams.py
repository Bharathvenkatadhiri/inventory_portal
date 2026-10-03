"""Company accounts with a team — buyer Owner/Admin/Procurement/Viewer,
supplier Owner/Admin/Sales/Operations/Viewer (accounts.team) — and internal
approvals (marketplace.approvals)."""
import re
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import team
from accounts.models import ConsumerProfile, ManufacturerProfile, TeamActivity, TeamInvitation, TeamMember
from core.models import User
from marketplace import services
from marketplace.models import ApprovalRequest, MessageThread, Order, Quote, Requirement, RequirementPart

TEST_STORAGES = override_settings(
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
)
PASSWORD = "pass12345"


def make_buyer(prefix, phone):
    user = User.objects.create_user(username=prefix, email=f"{prefix}@example.com", password=PASSWORD, first_name=prefix.title())
    profile = ConsumerProfile.objects.create(
        user=user, Name=f"{prefix} Co", type_of_business="electronics", city="Pune", state="MH", country="India",
        phone=phone, email=f"{prefix}@example.com", EORI_number=f"E-{prefix}", VAT_number=f"V-{prefix}",
    )
    return user, profile


def make_supplier(prefix, phone):
    user = User.objects.create_user(username=prefix, email=f"{prefix}@example.com", password=PASSWORD, role="manufacturer", first_name=prefix.title())
    profile = ManufacturerProfile.objects.create(
        user=user, companyname=f"{prefix} Works", phone=phone, address="1 Rd", city="Chennai", state="TN", country="India",
        amount_of_employees="10-20", turnover_per_year="<1", certificates="", email=f"{prefix}@example.com",
    )
    return user, profile


def add_member(profile, prefix, role=TeamMember.PROCUREMENT):
    is_buyer = isinstance(profile, ConsumerProfile)
    user = User.objects.create_user(
        username=prefix, email=f"{prefix}@example.com", password=PASSWORD,
        role="consumer" if is_buyer else "manufacturer", first_name=prefix.title(),
    )
    TeamMember.objects.create(user=user, role=role, **({'buyer': profile} if is_buyer else {'supplier': profile}))
    return user


def rfq_payload(title):
    return {
        "title": title, "rfq_desc": "d", "quote_currency": "INR", "request_reason": "other", "nda_required": "False",
        "end_date": (timezone.now() + timedelta(days=10)).date().isoformat(),
        "requirement_parts-TOTAL_FORMS": "1", "requirement_parts-INITIAL_FORMS": "0",
        "requirement_parts-0-part_name": title, "requirement_parts-0-technology": "Milling",
        "requirement_parts-0-Material": "Aluminium", "requirement_parts-0-quantity": "10",
    }


QUOTE_DATA = {
    "quote_price": "100", "tooling_cost": "0", "lead_time_value": "10", "lead_time_unit": "days",
    "payment_terms": "net_30", "note": "ok", "action": "submit",
}


def on_plan(user, plan):
    from accounts.models import SubscriptionPlan
    SubscriptionPlan.objects.update_or_create(user_profile=user, defaults={'plan_type': plan, 'price': 0})


class TeamTestCase(TestCase):
    def setUp(self):
        cache.clear()
        # Business plans (15 users, approval workflows), so plan limits stay
        # out of the way of role tests; plan tests switch plans themselves.
        self.owner, self.buyer = make_buyer("bown", "9300000001")
        on_plan(self.owner, 'business')
        self.admin = add_member(self.buyer, "badm", TeamMember.ADMIN)
        self.procurement = add_member(self.buyer, "bpro", TeamMember.PROCUREMENT)
        self.viewer = add_member(self.buyer, "bview", TeamMember.VIEWER)
        self.sowner, self.supplier = make_supplier("sown", "9300000002")
        on_plan(self.sowner, 'business')
        self.sadmin = add_member(self.supplier, "sadm", TeamMember.ADMIN)
        self.ssales = add_member(self.supplier, "ssales", TeamMember.SALES)
        self.sops = add_member(self.supplier, "sops", TeamMember.OPERATIONS)
        self.sviewer = add_member(self.supplier, "sview", TeamMember.VIEWER)
        self.outsider, self.outsider_profile = make_buyer("other", "9300000003")

    def login(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.email, password=PASSWORD))

    def open_rfq(self, user=None, title="Shaft"):
        requirement = Requirement.objects.create(
            user=user or self.owner, title=title, rfq_desc="", quote_currency="INR", request_reason="other",
            end_date=timezone.now() + timedelta(days=10),
        )
        RequirementPart.objects.create(requirement=requirement, part_name=title, technology="Milling", Material="Aluminium", quantity=10)
        return requirement

    def decide(self, req, decision, note=""):
        return self.client.post(reverse("approval-decide", kwargs={"pk": req.pk}), {"decision": decision, "note": note})


@TEST_STORAGES
class TeamHelperTests(TeamTestCase):
    def test_roles_and_company_resolution(self):
        self.assertEqual(team.team_role(self.owner), team.OWNER)
        self.assertEqual(team.team_role(self.admin), team.ADMIN)
        self.assertEqual(team.team_role(self.procurement), team.PROCUREMENT)
        self.assertEqual(team.team_role(self.ssales), team.SALES)
        self.assertEqual(team.team_role(self.sops), team.OPERATIONS)
        self.assertEqual(team.buyer_profile(self.viewer), self.buyer)
        self.assertEqual(team.supplier_profile(self.ssales), self.supplier)
        self.assertIsNone(team.supplier_profile(self.procurement))
        self.assertTrue(team.is_teammate(self.viewer, self.owner))
        self.assertFalse(team.is_teammate(self.procurement, self.outsider))

    def check_table(self, users, table):
        for action, expected in table.items():
            self.assertEqual([team.allows(user, action) for user in users], expected, action)

    def test_the_buyer_permission_table(self):
        self.check_table([self.owner, self.admin, self.procurement, self.viewer], {
            'company.view': [True, True, True, True],
            'company.edit': [True, True, False, False],
            'team.manage': [True, True, False, False],
            'rfq.create': [True, True, True, False],
            'quotes.manage': [True, True, True, False],
            'quote.accept': [True, True, False, False],
            'order.manage': [True, True, True, False],
            'order.confirm_payment': [True, True, False, False],
            'invoice.view': [True, True, True, True],
            'invoice.manage': [True, True, True, False],
            'subscription.manage': [True, False, False, False],
            'company.delete': [True, False, False, False],
        })

    def test_the_supplier_permission_table(self):
        self.check_table([self.sowner, self.sadmin, self.ssales, self.sops, self.sviewer], {
            'company.edit': [True, True, False, False, False],
            'team.manage': [True, True, False, False, False],
            'capabilities.manage': [True, True, True, True, False],
            'rfq.view': [True, True, True, True, True],
            'rfq.evaluate': [True, True, True, True, True],
            'quotes.manage': [True, True, True, False, False],
            'buyer_info.view': [True, True, True, True, False],
            'order.manage': [True, True, True, True, True],
            'order.production': [True, True, False, True, True],
            'order.dispatch': [True, True, False, True, True],
            'order.production_docs': [True, True, False, True, False],
            'order.quality_docs': [True, True, False, True, True],
            'invoice.manage': [True, True, True, True, True],
            'analytics.sales': [True, True, True, False, False],
            'analytics.operations': [True, True, False, True, True],
            'subscription.manage': [True, False, False, False, False],
            'audit.view': [True, True, False, False, True],
            'audit.view_own': [False, False, True, True, False],
        })
        # Buyer-only actions don't exist on the supplier side, and vice versa.
        self.assertFalse(team.allows(self.sowner, 'rfq.create'))
        self.assertFalse(team.allows(self.owner, 'order.production'))

    def test_buyer_and_supplier_rows_with_the_same_id_are_not_confused(self):
        # Buyer and supplier profiles have separate id sequences.
        req = ApprovalRequest(kind=ApprovalRequest.AWARD, supplier_id=self.buyer.pk)
        self.assertFalse(team.belongs_to(req, self.buyer))


@TEST_STORAGES
class RFQTests(TeamTestCase):
    def test_procurement_posts_rfqs_straight_to_suppliers(self):
        self.login(self.procurement)
        self.client.post(reverse("new-requirement"), rfq_payload("Bracket"))
        rfq = Requirement.objects.get(title="Bracket")
        self.assertEqual(rfq.approval_status, "approved")
        self.assertTrue(services.open_requirements_for(self.supplier).filter(pk=rfq.pk).exists())
        self.assertFalse(ApprovalRequest.objects.exists())
        self.assertTrue(TeamActivity.objects.filter(buyer=self.buyer, action="rfq.created").exists())

    def test_procurement_can_edit_a_colleagues_rfq_but_a_viewer_cannot(self):
        rfq = self.open_rfq(self.owner)
        self.login(self.procurement)
        self.assertEqual(self.client.get(reverse("edit-requirement", kwargs={"pk": rfq.pk})).status_code, 200)
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("requirement", kwargs={"pk": rfq.pk})).status_code, 200)
        self.assertContains(self.client.get(reverse("requirement-list")), rfq.title)
        self.assertEqual(self.client.get(reverse("edit-requirement", kwargs={"pk": rfq.pk})).status_code, 403)
        self.assertEqual(self.client.get(reverse("new-requirement")).status_code, 403)
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("requirement", kwargs={"pk": rfq.pk})).status_code, 404)

    def test_an_rfq_still_pending_from_the_old_roles_can_be_approved(self):
        rfq = self.open_rfq(self.procurement, title="Legacy")
        rfq.approval_status = "pending"
        rfq.save()
        req = ApprovalRequest.objects.create(kind=ApprovalRequest.RFQ, buyer=self.buyer, requirement=rfq, requested_by=self.procurement)
        self.login(self.admin)
        self.decide(req, "approve")
        rfq.refresh_from_db()
        self.assertEqual(rfq.approval_status, "approved")


@TEST_STORAGES
class QuoteTests(TeamTestCase):
    def test_sales_sends_quotes_directly(self):
        rfq = self.open_rfq()
        self.login(self.ssales)
        mail.outbox.clear()
        self.client.post(reverse("new-quote", kwargs={"pk": rfq.pk}), QUOTE_DATA)
        quote = Quote.objects.get(requirement=rfq)
        self.assertFalse(quote.is_draft)
        self.assertIsNotNone(quote.submitted_at)
        self.assertIn("bown@example.com", [m.to[0] for m in mail.outbox])
        self.assertFalse(ApprovalRequest.objects.exists())

    def test_sales_revises_a_colleagues_quote_and_the_old_version_is_kept(self):
        rfq = self.open_rfq()
        quote = Quote.objects.create(requirement=rfq, supplier=self.supplier, quote_price=Decimal('100'), created_by=self.sowner, submitted_at=timezone.now())
        self.login(self.ssales)
        self.client.post(reverse("edit-quote", kwargs={"pk": quote.pk}), {**QUOTE_DATA, "quote_price": "80"})
        quote.refresh_from_db()
        self.assertEqual(quote.quote_price, 80)
        self.assertEqual(list(quote.revisions.values_list('quote_price', flat=True)), [Decimal('100')])
        self.assertContains(self.client.get(reverse("requirement", kwargs={"pk": rfq.pk})), "Revision history")

    def test_operations_and_viewers_cannot_quote(self):
        rfq = self.open_rfq()
        for user in (self.sops, self.sviewer):
            self.login(user)
            self.assertEqual(self.client.get(reverse("new-quote", kwargs={"pk": rfq.pk})).status_code, 403, user)
            self.client.post(reverse("new-quote", kwargs={"pk": rfq.pk}), QUOTE_DATA)
        self.assertFalse(Quote.objects.exists())


@TEST_STORAGES
class AwardAndPaymentTests(TeamTestCase):
    def setUp(self):
        super().setUp()
        self.rfq = self.open_rfq()
        self.quote = Quote.objects.create(requirement=self.rfq, supplier=self.supplier, quote_price=Decimal('100'))

    def award_url(self, quote=None):
        return reverse("quote-update-status", kwargs={"pk": (quote or self.quote).pk, "status": "Approved"})

    def payment_pending_order(self):
        order, _ = services.award_quote(self.rfq, self.quote)
        order.start_production()
        order.request_payment()
        order.save()
        return order

    def test_procurement_acceptance_waits_for_an_owner_or_admin_on_business(self):
        self.login(self.procurement)
        mail.outbox.clear()
        self.client.post(self.award_url())
        self.assertFalse(Order.objects.exists())
        req = ApprovalRequest.objects.get(kind=ApprovalRequest.AWARD)
        # Approvers (owner + admin) are emailed; not procurement or viewers.
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["badm@example.com", "bown@example.com"])
        self.login(self.admin)
        self.decide(req, "approve")
        self.quote.refresh_from_db()
        self.assertTrue(self.quote.is_selected)
        self.assertTrue(Order.objects.filter(requirement=self.rfq, customer=self.buyer).exists())

    def test_without_approval_workflows_procurement_cannot_accept(self):
        on_plan(self.owner, 'starter')
        self.login(self.procurement)
        page = self.client.get(reverse("requirement", kwargs={"pk": self.rfq.pk}))
        self.assertContains(page, "Only your company&#x27;s owner or an admin can accept a quotation on your plan")
        self.assertNotContains(page, "Select this quote")
        self.client.post(self.award_url())
        self.assertFalse(ApprovalRequest.objects.exists())
        self.assertFalse(Order.objects.exists())

    def test_admin_accepts_directly(self):
        self.login(self.admin)
        self.client.post(self.award_url())
        self.assertTrue(Order.objects.filter(requirement=self.rfq).exists())
        self.assertFalse(ApprovalRequest.objects.exists())

    def test_approval_is_cancelled_if_the_rfq_was_awarded_meanwhile(self):
        _, other_supplier = make_supplier("rival", "9300000009")
        rival_quote = Quote.objects.create(requirement=self.rfq, supplier=other_supplier, quote_price=Decimal('90'))
        self.login(self.procurement)
        self.client.post(self.award_url())
        req = ApprovalRequest.objects.get()
        self.login(self.owner)
        self.client.post(self.award_url(rival_quote))
        self.decide(req, "approve")
        req.refresh_from_db()
        self.assertEqual(req.status, "cancelled")
        self.assertEqual(Order.objects.count(), 1)

    def test_procurement_and_others_cannot_decide(self):
        self.login(self.procurement)
        self.client.post(self.award_url())
        req = ApprovalRequest.objects.get()
        for user in (self.procurement, self.viewer, self.outsider, self.sadmin):
            self.login(user)
            self.assertIn(self.decide(req, "approve").status_code, (302, 404), user)
            req.refresh_from_db()
            self.assertEqual(req.status, "pending", user)

    def test_only_an_owner_or_admin_confirms_payment(self):
        order = self.payment_pending_order()
        url = reverse("order-update-status", kwargs={"billno": order.billno, "status": "paid"})
        self.login(self.procurement)
        self.assertContains(self.client.get(reverse("order-detail", kwargs={"billno": order.billno})), "owner or an admin confirms payments")
        self.client.post(url)
        order.refresh_from_db()
        self.assertEqual(order.status, "payment_pending")
        self.login(self.admin)
        self.client.post(url)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")

    def test_supplier_production_follows_the_supplier_table(self):
        order, _ = services.award_quote(self.rfq, self.quote)
        url = reverse("order-update-status", kwargs={"billno": order.billno, "status": "in_production"})
        self.login(self.ssales)  # sales can't update production status
        self.client.post(url)
        order.refresh_from_db()
        self.assertEqual(order.status, "quote_selected")
        self.login(self.sviewer)  # a supplier viewer can
        self.client.post(url)
        order.refresh_from_db()
        self.assertEqual(order.status, "in_production")
        self.login(self.sops)  # operations can request payment ("manage invoices")
        self.client.post(reverse("order-update-status", kwargs={"billno": order.billno, "status": "payment_pending"}))
        order.refresh_from_db()
        self.assertEqual(order.status, "payment_pending")

    def test_production_documents_are_for_operations_not_viewers(self):
        order, _ = services.award_quote(self.rfq, self.quote)
        url = reverse("order-post-update", kwargs={"billno": order.billno})
        self.login(self.sviewer)
        self.client.post(url, {"body": "From a viewer"})
        self.assertFalse(order.updates.exists())
        self.login(self.sops)
        self.client.post(url, {"body": "Machining done"})
        self.assertTrue(order.updates.filter(body="Machining done").exists())


@TEST_STORAGES
class ViewerTests(TeamTestCase):
    def test_every_buyer_viewer_write_is_refused(self):
        self.login(self.viewer)
        response = self.client.post(reverse("new-requirement"), rfq_payload("Sneaky"), HTTP_REFERER="/marketplace/requirement")
        self.assertRedirects(response, "/marketplace/requirement", fetch_redirect_response=False)
        self.assertFalse(Requirement.objects.filter(title="Sneaky").exists())
        response = self.client.post(reverse("team-invite"), {"email": "x@example.com"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["HX-Refresh"], "true")

    def test_personal_actions_still_work(self):
        self.login(self.viewer)
        self.assertEqual(self.client.post(reverse("notification-mark-all-read")).status_code, 302)
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_buyer_dashboard_says_view_only_and_hides_write_controls(self):
        self.login(self.viewer)
        page = self.client.get(reverse("requirement-list"))
        self.assertContains(page, "You have view-only access")
        self.login(self.procurement)
        self.assertNotContains(self.client.get(reverse("requirement-list")), "You have view-only access")


@TEST_STORAGES
class TeamManagementTests(TeamTestCase):
    def invite(self, email, role):
        return self.client.post(reverse("team-invite"), {"first_name": "New", "last_name": "Person", "email": email, "role": role})

    def join_link(self):
        return re.search(r"(/accounts/team/join/[^\s\"<]+/)", mail.outbox[-1].body).group(1)

    def set_role(self, user, role):
        return self.client.post(reverse("team-member-role", kwargs={"pk": TeamMember.objects.get(user=user).pk}), {"role": role})

    def role_of(self, user):
        return TeamMember.objects.get(user=user).role

    def test_invitation_flow_creates_a_member_who_can_sign_in(self):
        _, solo = make_buyer("solo", "9300000010")
        self.login(solo.user)
        mail.outbox.clear()
        self.invite("newadmin@example.com", TeamMember.ADMIN)
        invitation = TeamInvitation.objects.get(email="newadmin@example.com")
        self.assertNotIn(invitation.token_hash, mail.outbox[-1].body)  # only the raw token is emailed
        self.client.logout()
        link = self.join_link()
        self.client.post(link, {"first_name": "New", "last_name": "Person", "password1": "a-strong-passw0rd", "password2": "a-strong-passw0rd"})
        user = User.objects.get(email="newadmin@example.com")
        self.assertEqual(team.team_role(user), team.ADMIN)
        self.assertEqual(team.buyer_profile(user), solo)
        self.assertTrue(user.email_verified)
        self.assertTrue(self.client.login(username="newadmin@example.com", password="a-strong-passw0rd"))
        # The link can't be used twice.
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 404)

    def test_the_user_limit_counts_everyone_viewers_too(self):
        _, solo = make_buyer("free", "9300000011")  # Free: 2 users
        self.login(solo.user)
        self.invite("first@example.com", TeamMember.VIEWER)
        self.assertEqual(team.users_counted(solo), 2)
        response = self.invite("second@example.com", TeamMember.VIEWER)
        self.assertContains(response, "Your plan includes 2 users", status_code=400)
        self.assertFalse(TeamInvitation.objects.filter(email="second@example.com").exists())

    def test_roles_are_per_side_and_change_freely(self):
        self.login(self.owner)
        self.set_role(self.viewer, TeamMember.ADMIN)
        self.assertEqual(self.role_of(self.viewer), TeamMember.ADMIN)
        self.set_role(self.procurement, TeamMember.SALES)  # not a buyer role
        self.assertEqual(self.role_of(self.procurement), TeamMember.PROCUREMENT)
        self.login(self.sowner)
        self.set_role(self.ssales, TeamMember.OPERATIONS)
        self.assertEqual(self.role_of(self.ssales), TeamMember.OPERATIONS)
        self.set_role(self.sops, TeamMember.PROCUREMENT)  # not a supplier role
        self.assertEqual(self.role_of(self.sops), TeamMember.OPERATIONS)
        self.assertTrue(TeamActivity.objects.filter(buyer=self.buyer, action="team.role_changed").exists())

    def test_admins_manage_users_and_roles_except_their_own(self):
        self.login(self.admin)
        self.set_role(self.procurement, TeamMember.VIEWER)
        self.assertEqual(self.role_of(self.procurement), TeamMember.VIEWER)
        self.assertEqual(self.set_role(self.admin, TeamMember.VIEWER).status_code, 404)
        self.client.post(reverse("team-member-active", kwargs={"pk": TeamMember.objects.get(user=self.viewer).pk}), {"action": "deactivate"})
        self.viewer.refresh_from_db()
        self.assertFalse(self.viewer.is_active)
        self.client.logout()
        self.assertFalse(self.client.login(username=self.viewer.email, password=PASSWORD))

    def test_procurement_viewers_and_other_companies_cannot_manage_the_team(self):
        # Refused either by the role check (a redirect with a message) or,
        # for another company's member, as not found.
        for user in (self.procurement, self.outsider):
            self.login(user)
            self.assertIn(self.set_role(self.viewer, TeamMember.ADMIN).status_code, (302, 404), user)
        self.login(self.procurement)
        self.assertIn(self.invite("z@example.com", TeamMember.VIEWER).status_code, (302, 404))
        self.login(self.viewer)
        self.set_role(self.procurement, TeamMember.VIEWER)  # refused by the role check
        self.assertEqual(self.role_of(self.procurement), TeamMember.PROCUREMENT)
        self.assertFalse(TeamInvitation.objects.exists())

    def test_reactivation_needs_room_under_the_user_limit(self):
        on_plan(self.owner, 'free')  # 2 users; the team already has 4
        self.login(self.owner)
        member = TeamMember.objects.get(user=self.admin)
        self.client.post(reverse("team-member-active", kwargs={"pk": member.pk}), {"action": "deactivate"})
        response = self.client.post(reverse("team-member-active", kwargs={"pk": member.pk}), {"action": "activate"})
        self.assertContains(self.client.get(response.url), "Your plan includes 2 users")
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)

    def test_owner_hands_over_to_an_admin(self):
        self.login(self.admin)  # only the owner hands over
        self.client.post(reverse("team-transfer", kwargs={"pk": TeamMember.objects.get(user=self.admin).pk}))
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.user, self.owner)
        self.login(self.owner)
        self.client.post(reverse("team-transfer", kwargs={"pk": TeamMember.objects.get(user=self.admin).pk}))
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.user, self.admin)
        team.forget(self.owner)
        team.forget(self.admin)
        self.assertEqual(team.team_role(self.owner), team.ADMIN)
        self.assertEqual(team.team_role(self.admin), team.OWNER)

    def test_only_the_owner_changes_the_plan(self):
        from accounts.models import SubscriptionPlan
        on_plan(self.owner, 'starter')
        self.login(self.admin)
        self.client.post(reverse("subscription-upgrade"), {"plan_type": "business"})
        self.assertFalse(SubscriptionPlan.objects.filter(pending_plan_type="business").exists())
        self.assertContains(self.client.get(reverse("profile") + "?tab=billing"), "Only your company's owner can change the plan")

    def test_activity_log_by_role(self):
        team.log(self.ssales, 'quote.submitted', "Sales did something")
        team.log(self.sops, 'order.in_production', "Operations did something")
        for user, sees in ((self.procurement, False), (self.viewer, False), (self.admin, True)):
            self.login(user)
            page = self.client.get(reverse("team") + "?tab=activity")
            (self.assertContains if sees else self.assertNotContains)(page, "Latest 200 entries")
        self.login(self.ssales)  # sales and operations see their own actions
        page = self.client.get(reverse("team") + "?tab=activity")
        self.assertContains(page, "Sales did something")
        self.assertNotContains(page, "Operations did something")
        self.login(self.sviewer)  # a supplier viewer sees everyone's
        self.assertContains(self.client.get(reverse("team") + "?tab=activity"), "Operations did something")

    def test_admins_edit_the_company_profile_sales_manage_capabilities_only(self):
        self.login(self.sadmin)
        self.assertEqual(self.client.get(reverse("company-profile")).status_code, 200)
        self.client.post(reverse("company-about-update"), {"about": "Precision machining since 1998."})
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.about, "Precision machining since 1998.")
        self.login(self.ssales)
        page = self.client.get(reverse("company-profile"))
        self.assertContains(page, "You can update capabilities, materials, machines and certifications")
        self.client.post(reverse("company-about-update"), {"about": "Hijacked"})
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.about, "Precision machining since 1998.")
        self.client.post(reverse("company-machine-add"), {"machine_type": "VMC", "quantity": 2})
        self.assertTrue(self.supplier.machines.filter(machine_type="VMC").exists())
        self.login(self.sviewer)  # viewers can't manage capabilities
        self.client.post(reverse("company-machine-add"), {"machine_type": "Lathe", "quantity": 1})
        self.assertFalse(self.supplier.machines.filter(machine_type="Lathe").exists())

    def test_team_member_cannot_reuse_registration_to_start_a_company(self):
        response = self.client.post(reverse("register"), {
            "username": "bpro", "first_name": "B", "last_name": "P", "email": self.procurement.email,
            "password1": PASSWORD, "password2": PASSWORD, "account_type": "buyer",
        })
        self.assertContains(response, "Please log in instead")
        self.assertNotIn("session_user_id", self.client.session)


@TEST_STORAGES
class SharedConversationTests(TeamTestCase):
    def test_both_teams_are_participants(self):
        rfq = self.open_rfq(self.owner)
        thread = MessageThread.objects.create(requirement=rfq, supplier=self.supplier)
        for user in (self.procurement, self.admin, self.viewer, self.ssales):
            self.assertTrue(thread.is_participant(user))
        self.assertFalse(thread.is_participant(self.outsider))
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("message-thread", kwargs={"pk": thread.pk})).status_code, 200)


@TEST_STORAGES
class ApprovalPageTests(TeamTestCase):
    def test_approvers_see_the_queue_and_procurement_sees_its_own_requests(self):
        rfq = self.open_rfq(title="Flange")
        quote = Quote.objects.create(requirement=rfq, supplier=self.supplier, quote_price=Decimal('100'))
        self.login(self.procurement)
        self.client.post(reverse("quote-update-status", kwargs={"pk": quote.pk, "status": "Approved"}))
        page = self.client.get(reverse("approvals"))
        self.assertContains(page, "Flange")
        self.assertContains(page, "Waiting")
        self.assertNotContains(page, 'value="approve"')
        self.login(self.admin)
        page = self.client.get(reverse("approvals"))
        self.assertContains(page, 'value="approve"')
        self.assertContains(self.client.get(reverse("home")), "Approvals")
        for user in (self.viewer, self.outsider):
            self.login(user)
            self.assertNotContains(self.client.get(reverse("approvals")), "Flange")


class RoleMigrationTests(TransactionTestCase):
    """accounts 0014 maps supervisors to admins and users to procurement;
    0015 moves supplier-side procurement to sales and old plan names to
    the new ones."""

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def test_existing_roles_and_plans_are_mapped(self):
        before = [('accounts', '0013_company_gst_identity')]
        apps = self.migrate(before)
        OldUser, OldBuyer, OldSupplier, OldMember, OldPlan = (apps.get_model(*name) for name in (
            ('core', 'User'), ('accounts', 'ConsumerProfile'), ('accounts', 'ManufacturerProfile'),
            ('accounts', 'TeamMember'), ('accounts', 'SubscriptionPlan'),
        ))
        owner = OldUser.objects.create(username="o", email="o@example.com")
        buyer = OldBuyer.objects.create(
            user=owner, Name="Co", type_of_business="electronics", city="Pune", state="MH", country="India",
            phone="9300000099", email="o@example.com", EORI_number="E", VAT_number="V",
        )
        OldPlan.objects.create(user_profile=owner, plan_type="enterprise", price=4999, rfq_limit="unlimited")
        maker = OldUser.objects.create(username="m", email="m@example.com", role="manufacturer")
        supplier = OldSupplier.objects.create(
            user=maker, phone="9300000098", address="x", city="Chennai", state="TN", country="India",
            amount_of_employees="10-20", turnover_per_year="<1", email="m@example.com",
        )
        for name, role, company in (("s", "supervisor", {'buyer': buyer}), ("u", "member", {'buyer': buyer}),
                                    ("su", "member", {'supplier': supplier})):
            OldMember.objects.create(user=OldUser.objects.create(username=name, email=f"{name}@example.com"), role=role, **company)
        apps = self.migrate([('accounts', '0015_plans_and_supplier_roles')])
        NewMember, NewPlan = apps.get_model('accounts', 'TeamMember'), apps.get_model('accounts', 'SubscriptionPlan')
        self.assertEqual(dict(NewMember.objects.values_list('user__username', 'role')), {"s": "admin", "u": "procurement", "su": "sales"})
        self.assertEqual(list(NewPlan.objects.values_list('plan_type', 'billing_cycle', 'price')), [("business", "monthly", 2999)])
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
