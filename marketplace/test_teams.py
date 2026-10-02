"""Company accounts with a team (accounts.team) and internal approvals
(marketplace.approvals)."""
import re
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
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


def add_member(profile, prefix, role=TeamMember.MEMBER):
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


class TeamTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.manager, self.buyer = make_buyer("bmgr", "9300000001")
        self.supervisor = add_member(self.buyer, "bsup", TeamMember.SUPERVISOR)
        self.member = add_member(self.buyer, "buser")
        self.smanager, self.supplier = make_supplier("smgr", "9300000002")
        self.ssupervisor = add_member(self.supplier, "ssup", TeamMember.SUPERVISOR)
        self.smember = add_member(self.supplier, "suser")
        self.outsider, self.outsider_profile = make_buyer("other", "9300000003")

    def login(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.email, password=PASSWORD))

    def open_rfq(self, user=None, title="Shaft"):
        requirement = Requirement.objects.create(
            user=user or self.manager, title=title, rfq_desc="", quote_currency="INR", request_reason="other",
            end_date=timezone.now() + timedelta(days=10),
        )
        RequirementPart.objects.create(requirement=requirement, part_name=title, technology="Milling", Material="Aluminium", quantity=10)
        return requirement

    def decide(self, req, decision, note=""):
        return self.client.post(reverse("approval-decide", kwargs={"pk": req.pk}), {"decision": decision, "note": note})


@TEST_STORAGES
class TeamHelperTests(TeamTestCase):
    def test_roles_and_company_resolution(self):
        self.assertEqual(team.team_role(self.manager), team.MANAGER)
        self.assertEqual(team.team_role(self.supervisor), team.SUPERVISOR)
        self.assertEqual(team.team_role(self.member), team.MEMBER)
        self.assertEqual(team.buyer_profile(self.member), self.buyer)
        self.assertEqual(team.supplier_profile(self.smember), self.supplier)
        self.assertIsNone(team.supplier_profile(self.member))
        self.assertTrue(team.is_teammate(self.member, self.manager))
        self.assertFalse(team.is_teammate(self.member, self.outsider))

    def test_buyer_and_supplier_rows_with_the_same_id_are_not_confused(self):
        # Buyer and supplier profiles have separate id sequences.
        req = ApprovalRequest(kind=ApprovalRequest.RFQ, supplier_id=self.buyer.pk)
        self.assertFalse(team.belongs_to(req, self.buyer))


@TEST_STORAGES
class RFQApprovalTests(TeamTestCase):
    def test_users_rfq_waits_for_approval_and_is_hidden_from_suppliers(self):
        self.login(self.member)
        mail.outbox.clear()
        self.client.post(reverse("new-requirement"), rfq_payload("Bracket"))
        rfq = Requirement.objects.get(title="Bracket")
        self.assertEqual(rfq.approval_status, "pending")
        self.assertFalse(services.open_requirements_for(self.supplier).filter(pk=rfq.pk).exists())
        # Approvers (manager + supervisor) are emailed; nobody else.
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["bmgr@example.com", "bsup@example.com"])
        self.login(self.smanager)
        self.assertEqual(self.client.get(reverse("requirement", kwargs={"pk": rfq.pk})).status_code, 404)

        req = ApprovalRequest.objects.get(requirement=rfq)
        self.login(self.supervisor)
        self.decide(req, "approve")
        rfq.refresh_from_db()
        self.assertEqual(rfq.approval_status, "approved")
        self.assertTrue(services.open_requirements_for(self.supplier).filter(pk=rfq.pk).exists())
        self.assertTrue(TeamActivity.objects.filter(buyer=self.buyer, action="rfq.approved").exists())

    def test_supervisor_and_manager_post_directly(self):
        for user, title in ((self.supervisor, "Sup RFQ"), (self.manager, "Mgr RFQ")):
            self.login(user)
            self.client.post(reverse("new-requirement"), rfq_payload(title))
            self.assertEqual(Requirement.objects.get(title=title).approval_status, "approved")
        self.assertFalse(ApprovalRequest.objects.exists())

    def test_rejection_needs_a_reason_and_an_edit_resubmits(self):
        self.login(self.member)
        self.client.post(reverse("new-requirement"), rfq_payload("Hinge"))
        rfq = Requirement.objects.get(title="Hinge")
        req = ApprovalRequest.objects.get(requirement=rfq)
        self.login(self.manager)
        self.decide(req, "reject")
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")  # no reason, nothing happened
        self.decide(req, "reject", "Wrong material")
        rfq.refresh_from_db()
        self.assertEqual(rfq.approval_status, "rejected")

        self.login(self.member)
        data = rfq_payload("Hinge v2")
        data.update({"requirement_parts-INITIAL_FORMS": "1", "requirement_parts-0-id": rfq.requirement_parts.first().pk})
        self.client.post(reverse("edit-requirement", kwargs={"pk": rfq.pk}), data)
        rfq.refresh_from_db()
        self.assertEqual(rfq.approval_status, "pending")
        self.assertEqual(ApprovalRequest.objects.filter(requirement=rfq, status="pending").count(), 1)

    def test_users_can_see_but_not_edit_colleagues_rfqs(self):
        rfq = self.open_rfq(self.manager)
        self.login(self.member)
        self.assertEqual(self.client.get(reverse("requirement", kwargs={"pk": rfq.pk})).status_code, 200)
        self.assertContains(self.client.get(reverse("requirement-list")), rfq.title)
        self.assertEqual(self.client.get(reverse("edit-requirement", kwargs={"pk": rfq.pk})).status_code, 403)
        self.login(self.supervisor)
        self.assertEqual(self.client.get(reverse("edit-requirement", kwargs={"pk": rfq.pk})).status_code, 200)
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("requirement", kwargs={"pk": rfq.pk})).status_code, 404)

    def test_users_and_other_companies_cannot_decide(self):
        self.login(self.member)
        self.client.post(reverse("new-requirement"), rfq_payload("Clip"))
        req = ApprovalRequest.objects.get()
        self.assertEqual(self.decide(req, "approve").status_code, 404)
        self.login(self.outsider)
        self.assertEqual(self.decide(req, "approve").status_code, 404)
        self.login(self.ssupervisor)  # a supervisor, but of another company
        self.assertEqual(self.decide(req, "approve").status_code, 404)


@TEST_STORAGES
class QuoteApprovalTests(TeamTestCase):
    def test_users_quote_is_a_hidden_draft_until_approved(self):
        rfq = self.open_rfq()
        self.login(self.smember)
        self.client.post(reverse("new-quote", kwargs={"pk": rfq.pk}), QUOTE_DATA)
        quote = Quote.objects.get(requirement=rfq)
        self.assertTrue(quote.is_draft)
        self.assertEqual(quote.approval_status, "pending")
        self.assertEqual(quote.created_by, self.smember)
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse("quote", kwargs={"pk": quote.pk})).status_code, 404)

        self.login(self.ssupervisor)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.decide(ApprovalRequest.objects.get(quote=quote), "approve")
        quote.refresh_from_db()
        self.assertFalse(quote.is_draft)
        self.assertIn("bmgr@example.com", [m.to[0] for m in mail.outbox])  # the buyer hears about it now
        self.login(self.member)  # anyone on the buyer's team can open it
        self.assertEqual(self.client.get(reverse("quote", kwargs={"pk": quote.pk})).status_code, 200)

    def test_users_revision_is_staged_until_approved(self):
        rfq = self.open_rfq()
        quote = Quote.objects.create(requirement=rfq, supplier=self.supplier, quote_price=Decimal('100'), created_by=self.smember)
        self.login(self.smember)
        self.client.post(reverse("edit-quote", kwargs={"pk": quote.pk}), {**QUOTE_DATA, "quote_price": "80"})
        quote.refresh_from_db()
        self.assertEqual(quote.quote_price, 100)  # the buyer still sees the old price
        req = ApprovalRequest.objects.get(quote=quote)
        self.assertTrue(req.is_revision)
        self.login(self.smanager)
        self.decide(req, "approve")
        quote.refresh_from_db()
        self.assertEqual(quote.quote_price, 80)

    def test_users_cannot_edit_a_colleagues_quote(self):
        rfq = self.open_rfq()
        quote = Quote.objects.create(requirement=rfq, supplier=self.supplier, quote_price=Decimal('100'), created_by=self.smanager)
        self.login(self.smember)
        self.assertEqual(self.client.get(reverse("edit-quote", kwargs={"pk": quote.pk})).status_code, 403)
        self.login(self.ssupervisor)
        self.assertEqual(self.client.get(reverse("edit-quote", kwargs={"pk": quote.pk})).status_code, 200)


@TEST_STORAGES
class AwardAndPaymentApprovalTests(TeamTestCase):
    def setUp(self):
        super().setUp()
        self.rfq = self.open_rfq()
        self.quote = Quote.objects.create(requirement=self.rfq, supplier=self.supplier, quote_price=Decimal('100'))

    def award_url(self, quote=None):
        return reverse("quote-update-status", kwargs={"pk": (quote or self.quote).pk, "status": "Approved"})

    def test_users_award_waits_for_approval(self):
        self.login(self.member)
        self.client.post(self.award_url())
        self.assertFalse(Order.objects.exists())
        req = ApprovalRequest.objects.get(kind=ApprovalRequest.AWARD)
        self.login(self.manager)
        self.decide(req, "approve")
        self.quote.refresh_from_db()
        self.assertTrue(self.quote.is_selected)
        self.assertTrue(Order.objects.filter(requirement=self.rfq, customer=self.buyer).exists())

    def test_award_approval_is_cancelled_if_the_rfq_was_awarded_meanwhile(self):
        other_supplier_user, other_supplier = make_supplier("rival", "9300000009")
        rival_quote = Quote.objects.create(requirement=self.rfq, supplier=other_supplier, quote_price=Decimal('90'))
        self.login(self.member)
        self.client.post(self.award_url())
        req = ApprovalRequest.objects.get()
        self.login(self.supervisor)
        self.client.post(self.award_url(rival_quote))  # supervisor awards someone else directly
        self.decide(req, "approve")
        req.refresh_from_db()
        self.assertEqual(req.status, "cancelled")
        self.assertEqual(Order.objects.count(), 1)

    def test_users_payment_confirmation_waits_for_approval(self):
        order, _ = services.award_quote(self.rfq, self.quote)
        order.start_production()
        order.request_payment()
        order.save()
        self.login(self.member)
        self.client.post(reverse("order-update-status", kwargs={"billno": order.billno, "status": "paid"}))
        order.refresh_from_db()
        self.assertEqual(order.status, "payment_pending")
        req = ApprovalRequest.objects.get(kind=ApprovalRequest.PAYMENT)
        self.login(self.supervisor)
        self.decide(req, "approve")
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")

    def test_supplier_team_member_can_run_the_order(self):
        order, _ = services.award_quote(self.rfq, self.quote)
        self.login(self.smember)
        self.client.post(reverse("order-update-status", kwargs={"billno": order.billno, "status": "in_production"}))
        order.refresh_from_db()
        self.assertEqual(order.status, "in_production")


@TEST_STORAGES
class TeamManagementTests(TeamTestCase):
    def invite(self, email, role):
        return self.client.post(reverse("team-invite"), {"first_name": "New", "last_name": "Person", "email": email, "role": role})

    def join_link(self):
        return re.search(r"(/accounts/team/join/[^\s\"<]+/)", mail.outbox[-1].body).group(1)

    def test_invitation_flow_creates_a_member_who_can_sign_in(self):
        _, solo = make_buyer("solo", "9300000010")
        self.login(solo.user)
        mail.outbox.clear()
        self.invite("newsup@example.com", TeamMember.SUPERVISOR)
        invitation = TeamInvitation.objects.get(email="newsup@example.com")
        self.assertNotIn(invitation.token_hash, mail.outbox[-1].body)  # only the raw token is emailed
        self.client.logout()
        link = self.join_link()
        self.client.post(link, {"first_name": "New", "last_name": "Person", "password1": "a-strong-passw0rd", "password2": "a-strong-passw0rd"})
        user = User.objects.get(email="newsup@example.com")
        self.assertEqual(team.team_role(user), team.SUPERVISOR)
        self.assertEqual(team.buyer_profile(user), solo)
        self.assertTrue(user.email_verified)
        self.assertTrue(self.client.login(username="newsup@example.com", password="a-strong-passw0rd"))
        # The link can't be used twice.
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 404)

    def test_seat_limit_follows_the_plan(self):
        # Basic = 3 seats: the manager, a supervisor and a user are already in.
        self.login(self.manager)
        response = self.invite("fourth@example.com", TeamMember.MEMBER)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(TeamInvitation.objects.filter(email="fourth@example.com").exists())

    def test_supervisor_invites_users_but_not_supervisors(self):
        self.member.is_active = False  # free a seat
        self.member.save()
        self.login(self.supervisor)
        self.assertEqual(self.invite("x@example.com", TeamMember.SUPERVISOR).status_code, 400)
        self.invite("y@example.com", TeamMember.MEMBER)
        self.assertTrue(TeamInvitation.objects.filter(email="y@example.com").exists())

    def test_users_cannot_invite(self):
        self.login(self.member)
        self.assertEqual(self.invite("z@example.com", TeamMember.MEMBER).status_code, 404)

    def test_supervisor_deactivates_users_only_and_deactivated_users_cannot_sign_in(self):
        sup_member = TeamMember.objects.get(user=self.supervisor)
        user_member = TeamMember.objects.get(user=self.member)
        other_sup = add_member(self.buyer, "bsup2", TeamMember.SUPERVISOR)
        self.login(self.supervisor)
        url = lambda m: reverse("team-member-active", kwargs={"pk": m.pk})
        self.assertEqual(self.client.post(url(TeamMember.objects.get(user=other_sup)), {"action": "deactivate"}).status_code, 404)
        self.assertEqual(self.client.post(url(sup_member), {"action": "deactivate"}).status_code, 404)  # not yourself
        self.client.post(url(user_member), {"action": "deactivate"})
        self.client.logout()
        self.assertFalse(self.client.login(username=self.member.email, password=PASSWORD))

    def test_manager_hands_over_to_a_supervisor(self):
        self.login(self.manager)
        self.client.post(reverse("team-transfer", kwargs={"pk": TeamMember.objects.get(user=self.supervisor).pk}))
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.user, self.supervisor)
        team.forget(self.manager)
        team.forget(self.supervisor)
        self.assertEqual(team.team_role(self.manager), team.SUPERVISOR)
        self.assertEqual(team.team_role(self.supervisor), team.MANAGER)

    def test_only_the_manager_changes_the_plan(self):
        self.login(self.supervisor)
        self.client.post(reverse("subscription-upgrade"), {"plan_type": "standard"})
        from accounts.models import SubscriptionPlan
        self.assertFalse(SubscriptionPlan.objects.filter(pending_plan_type="standard").exists())

    def test_activity_is_for_supervisors_and_the_manager(self):
        self.login(self.member)
        self.assertNotContains(self.client.get(reverse("team") + "?tab=activity"), "Latest 200 entries")
        self.login(self.supervisor)
        self.assertContains(self.client.get(reverse("team") + "?tab=activity"), "Latest 200 entries")

    def test_team_member_cannot_reuse_registration_to_start_a_company(self):
        response = self.client.post(reverse("register"), {
            "username": "buser", "first_name": "B", "last_name": "U", "email": self.member.email,
            "password1": PASSWORD, "password2": PASSWORD, "account_type": "buyer",
        })
        self.assertContains(response, "Please log in instead")
        self.assertNotIn("session_user_id", self.client.session)


@TEST_STORAGES
class SharedConversationTests(TeamTestCase):
    def test_both_teams_are_participants(self):
        rfq = self.open_rfq(self.manager)
        thread = MessageThread.objects.create(requirement=rfq, supplier=self.supplier)
        for user in (self.member, self.supervisor, self.smember):
            self.assertTrue(thread.is_participant(user))
        self.assertFalse(thread.is_participant(self.outsider))
        self.login(self.member)
        self.assertEqual(self.client.get(reverse("message-thread", kwargs={"pk": thread.pk})).status_code, 200)


@TEST_STORAGES
class ApprovalPageTests(TeamTestCase):
    def test_approvers_see_the_queue_and_users_see_their_own_requests(self):
        self.login(self.member)
        self.client.post(reverse("new-requirement"), rfq_payload("Flange"))
        page = self.client.get(reverse("approvals"))
        self.assertContains(page, "Flange")
        self.assertContains(page, "Waiting")
        self.assertNotContains(page, 'value="approve"')
        self.login(self.supervisor)
        page = self.client.get(reverse("approvals"))
        self.assertContains(page, 'value="approve"')
        self.assertContains(self.client.get(reverse("home")), "Approvals")
        self.login(self.outsider)
        self.assertNotContains(self.client.get(reverse("approvals")), "Flange")
