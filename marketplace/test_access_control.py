"""Cross-company access (IDOR/BOLA): every URL that takes an id, requested
by every role of another company with someone else's ids. Nothing may leak
into the response and nothing may change in the database."""
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import (
    Certification, ConsumerProfile, Machine, ManufacturerProfile, SubscriptionPlan, TeamInvitation, TeamMember,
)
from core.models import User
from homepage.models import PortalFeedback
from marketplace import services
from marketplace.models import (
    AmendmentResponse, ApprovalRequest, Message, MessageThread, Order, OrderDocument, OrderEvent, ProductionUpdate,
    Quote, RFQDecline, Requirement, RequirementAmendment, RequirementNDAAcceptance, RequirementPart, SupplierReview,
)

TEMP_MEDIA = tempfile.mkdtemp()
PASSWORD = "pass12345"

# Rows any of these requests could create, change or delete.
WATCHED_MODELS = [
    User, ConsumerProfile, ManufacturerProfile, SubscriptionPlan, TeamMember, TeamInvitation, Machine, Certification,
    Requirement, RequirementPart, Quote, Order, OrderEvent, ProductionUpdate, OrderDocument, MessageThread, Message,
    RequirementAmendment, AmendmentResponse, ApprovalRequest, RFQDecline, RequirementNDAAcceptance, SupplierReview,
    PortalFeedback,
]


def snapshot():
    state = {}
    for model in WATCHED_MODELS:
        rows = model.objects.order_by('pk').values()
        if model is User:
            rows = rows.values('pk', 'is_active', 'is_staff', 'role', 'email', 'password')
        state[model.__name__] = [{k: v for k, v in row.items() if k not in ('last_login', 'updated_at')} for row in rows]
    return state


def buyer_company(prefix, phone):
    manager = User.objects.create_user(username=prefix, email=f"{prefix}@example.com", password=PASSWORD)
    profile = ConsumerProfile.objects.create(
        user=manager, Name=f"{prefix.upper()} BUYER CO", type_of_business="electronics", city="Pune", state="MH",
        country="India", phone=phone, email=f"{prefix}@example.com", EORI_number=f"E-{prefix}", VAT_number=f"V-{prefix}",
    )
    SubscriptionPlan.objects.create(user_profile=manager, plan_type="free", price=0)
    team = {'manager': manager}
    for role in (TeamMember.ADMIN, TeamMember.PROCUREMENT, TeamMember.VIEWER):
        user = User.objects.create_user(username=f"{prefix}-{role}", email=f"{prefix}-{role}@example.com", password=PASSWORD)
        TeamMember.objects.create(user=user, buyer=profile, role=role)
        team[role] = user
    return profile, team


def supplier_company(prefix, phone):
    manager = User.objects.create_user(username=prefix, email=f"{prefix}@example.com", password=PASSWORD, role="manufacturer")
    profile = ManufacturerProfile.objects.create(
        user=manager, companyname=f"{prefix.upper()} WORKS", phone=phone, address="1 Rd", city="Chennai", state="TN",
        country="India", amount_of_employees="10-20", turnover_per_year="<1", certificates="", email=f"{prefix}@example.com",
    )
    team = {'manager': manager}
    for role in (TeamMember.ADMIN, TeamMember.SALES, TeamMember.OPERATIONS, TeamMember.VIEWER):
        user = User.objects.create_user(
            username=f"{prefix}-{role}", email=f"{prefix}-{role}@example.com", password=PASSWORD, role="manufacturer",
        )
        TeamMember.objects.create(user=user, supplier=profile, role=role)
        team[role] = user
    return profile, team


@override_settings(
    MEDIA_ROOT=TEMP_MEDIA,
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    },
)
class CrossCompanyAccessTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        # Victims: buyer B and its supplier S2.
        self.buyer_b, self.team_b = buyer_company("victimbuyer", "9200000001")
        self.supplier_s2, self.team_s2 = supplier_company("victimmaker", "9200000002")
        # Attackers: another buyer company A and a rival supplier S1.
        self.buyer_a, self.team_a = buyer_company("attackbuyer", "9200000003")
        self.supplier_s1, self.team_s1 = supplier_company("attackmaker", "9200000004")

        due = timezone.now() + timedelta(days=10)
        self.rfq_open = self._rfq("SECRET-RFQ-OPEN", due)
        self.rfq_awarded = self._rfq("SECRET-RFQ-AWARDED", due)
        self.rfq_pending = self._rfq("SECRET-RFQ-PENDING", due, creator=self.team_b[TeamMember.PROCUREMENT], approval_status="pending")

        self.quote_open = Quote.objects.create(
            requirement=self.rfq_open, supplier=self.supplier_s2, quote_price=Decimal("777.77"),
            note="SECRET-QUOTE-NOTE", created_by=self.team_s2['manager'],
        )
        quote_awarded = Quote.objects.create(
            requirement=self.rfq_awarded, supplier=self.supplier_s2, quote_price=Decimal("666.66"),
            note="SECRET-QUOTE-NOTE", created_by=self.team_s2['manager'],
        )
        self.order, _ = services.award_quote(self.rfq_awarded, quote_awarded)
        self.document = OrderDocument.objects.filter(order=self.order).first()

        self.thread = MessageThread.objects.create(requirement=self.rfq_open, supplier=self.supplier_s2)
        self.message = Message.objects.create(
            thread=self.thread, sender=self.team_b['manager'], body="SECRET-MESSAGE",
            attachment=SimpleUploadedFile("spec.pdf", b"%PDF-1.4 secret"), attachment_name="spec.pdf",
            attachment_content_type="application/pdf", attachment_size=15,
        )
        amendment = RequirementAmendment.objects.create(
            requirement=self.rfq_open, proposed_by=self.team_b['manager'],
            changes={"requirement": {"title": {"old": "SECRET-RFQ-OPEN", "new": "SECRET-RFQ-RENAMED"}}},
        )
        self.amendment = amendment
        self.response = AmendmentResponse.objects.create(amendment=amendment, quote=self.quote_open)

        self.approval = ApprovalRequest.objects.create(
            kind=ApprovalRequest.RFQ, buyer=self.buyer_b, requirement=self.rfq_pending,
            requested_by=self.team_b[TeamMember.PROCUREMENT],
        )
        self.b_member = TeamMember.objects.get(user=self.team_b[TeamMember.PROCUREMENT])
        self.b_supervisor = TeamMember.objects.get(user=self.team_b[TeamMember.ADMIN])
        self.b_invitation = TeamInvitation.objects.create(
            buyer=self.buyer_b, email="newhire@example.com", first_name="New", role=TeamMember.PROCUREMENT,
            token_hash="x" * 64, invited_by=self.team_b['manager'], expires_at=timezone.now() + timedelta(days=7),
        )
        self.machine = Machine.objects.create(manufacturer=self.supplier_s2, machine_type="SECRET-MACHINE")
        self.certification = Certification.objects.create(manufacturer=self.supplier_s2, name="SECRET-CERT")
        self.feedback = PortalFeedback.objects.create(user=self.team_b['manager'], role="consumer", rating=5)

    def _rfq(self, title, due, creator=None, approval_status="approved"):
        rfq = Requirement.objects.create(
            user=creator or self.team_b['manager'], title=title, rfq_desc="", quote_currency="INR",
            request_reason="other", end_date=due, approval_status=approval_status,
        )
        RequirementPart.objects.create(requirement=rfq, part_name=title, technology="Milling", Material="Aluminium", quantity=10)
        return rfq

    def _urls(self):
        """(url, also_check_as_supplier_attacker) for every route with an id."""
        b = self.order.billno
        rfq, quote = self.rfq_open.pk, self.quote_open.pk
        return [
            # RFQs: a rival supplier may legitimately open an open RFQ (and its
            # quotes poll), but never see S2's quote in it.
            (reverse('requirement', args=[rfq]), True),
            (reverse('requirement', args=[self.rfq_pending.pk]), True),
            (reverse('requirement', args=[self.rfq_awarded.pk]), True),
            (reverse('edit-requirement', args=[rfq]), True),
            (reverse('delete-requirement', args=[rfq]), True),
            (reverse('requirement-update-status', args=[self.rfq_awarded.pk, 'Production']), True),
            (reverse('requirement-extend', args=[rfq]), True),
            (reverse('live-quotes', args=[rfq]), True),
            # Change requests
            (reverse('amendment-withdraw', args=[self.amendment.pk]), True),
            (reverse('amendment-respond', args=[self.response.pk]), True),
            (reverse('amendment-decide', args=[self.response.pk]), True),
            # Quotes
            (reverse('quote', args=[quote]), True),
            (reverse('edit-quote', args=[quote]), True),
            (reverse('delete-quote', args=[quote]), True),
            (reverse('quote-update-status', args=[quote, 'Approved']), True),
            (reverse('quote-update-status', args=[quote, 'Rejected']), True),
            (reverse('quote-request-revision', args=[quote]), True),
            (reverse('quote-decline-revision', args=[quote]), True),
            # Orders and their documents
            (reverse('order-detail', args=[b]), True),
            (reverse('order-update-status', args=[b, 'in_production']), True),
            (reverse('order-update-status', args=[b, 'cancelled']), True),
            (reverse('order-production-advance', args=[b]), True),
            (reverse('order-post-update', args=[b]), True),
            (reverse('order-qc-toggle', args=[b, 0]), True),
            (reverse('order-review', args=[b]), True),
            (reverse('order-shipment-update', args=[b]), True),
            (reverse('order-document', args=[self.document.pk]), True),
            # Approvals
            (reverse('approval-decide', args=[self.approval.pk]), True),
            # Messages
            (reverse('message-thread', args=[self.thread.pk]), True),
            (reverse('message-thread-close', args=[self.thread.pk]), True),
            (reverse('message-delete', args=[self.message.pk]), True),
            (reverse('message-attachment', args=[self.message.pk]), True),
            (reverse('live-thread', args=[self.thread.pk]), True),
            (reverse('message-thread-start', args=[rfq, self.supplier_s2.pk]), True),
            # Team
            (reverse('team-member-role', args=[self.b_member.pk]), True),
            (reverse('team-member-active', args=[self.b_member.pk]), True),
            (reverse('team-transfer', args=[self.b_supervisor.pk]), True),
            (reverse('team-invitation-revoke', args=[self.b_invitation.pk]), True),
            # A supplier's own company profile items
            (reverse('company-machine-remove', args=[self.machine.pk]), True),
            (reverse('company-certification-delete', args=[self.certification.pk]), True),
            # Staff-only administration
            (reverse('customer', args=[self.buyer_b.pk]), True),
            (reverse('edit-customer', args=[self.buyer_b.pk]), True),
            (reverse('delete-customer', args=[self.buyer_b.pk]), True),
            (reverse('activate-customer', args=[self.buyer_b.pk]), True),
            (reverse('edit-supplier', args=[self.supplier_s2.pk]), True),
            (reverse('delete-supplier', args=[self.supplier_s2.pk]), True),
            (reverse('activate-supplier', args=[self.supplier_s2.pk]), True),
            (reverse('edit-subscription', args=[SubscriptionPlan.objects.get(user_profile=self.team_b['manager']).pk]), True),
            (reverse('delete-subscription', args=[SubscriptionPlan.objects.get(user_profile=self.team_b['manager']).pk]), True),
            (reverse('portal-feedback-moderate', args=[self.feedback.pk, 'hide']), True),
            # Buyer-only actions a rival supplier may legitimately take on an
            # open RFQ (decline it, accept its NDA, quote, ask a question):
            # only checked against the other buyer company.
            (reverse('requirement-decline', args=[rfq]), False),
            (reverse('requirement-accept-nda', args=[rfq]), False),
            (reverse('new-quote', args=[rfq]), False),
            (reverse('message-thread-ask-buyer', args=[rfq]), False),
        ]

    POST_DATA = {
        'decision': 'approve', 'action': 'deactivate', 'role': 'supervisor', 'note': 'x', 'body': 'x',
        'end_date': '2099-01-01', 'quote_price': '1', 'tooling_cost': '0', 'lead_time_value': '1',
        'lead_time_unit': 'days', 'payment_terms': 'net_30', 'rating': '1', 'supplier_note': 'x',
        'title': 'HACKED', 'rfq_desc': 'x', 'quote_currency': 'INR', 'request_reason': 'other',
    }

    def _attack(self, attacker, urls, secrets):
        self.client.logout()
        self.assertTrue(self.client.login(username=attacker.email, password=PASSWORD))
        for url in urls:
            for method in ('get', 'post'):
                with self.subTest(attacker=attacker.email, method=method, url=url):
                    before = snapshot()
                    response = getattr(self.client, method)(url, self.POST_DATA if method == 'post' else None)
                    body = b''.join(response.streaming_content) if response.streaming else response.content
                    for secret in secrets:
                        self.assertNotIn(secret.encode(), body, f"{secret} leaked")
                    self.assertEqual(before, snapshot(), "request changed data it shouldn't be able to")

    def test_another_buyer_company_reaches_nothing(self):
        urls = [url for url, _ in self._urls()]
        secrets = ["SECRET-RFQ", "SECRET-QUOTE-NOTE", "777.77", "666.66", "SECRET-MESSAGE", "VICTIMBUYER BUYER CO", "%PDF-1.4 secret"]
        for role in ('manager', TeamMember.ADMIN, TeamMember.PROCUREMENT, TeamMember.VIEWER):
            self._attack(self.team_a[role], urls, secrets)

    def test_a_rival_supplier_never_sees_or_changes_another_suppliers_work(self):
        urls = [url for url, check in self._urls() if check]
        # The open RFQ's title is fine for a rival to see (it's in their inbox);
        # S2's quote, the order, the conversation and the pending RFQ are not.
        secrets = ["SECRET-QUOTE-NOTE", "777.77", "666.66", "SECRET-MESSAGE", "SECRET-RFQ-PENDING", "%PDF-1.4 secret", "SECRET-MACHINE"]
        for role in ('manager', TeamMember.ADMIN, TeamMember.SALES, TeamMember.OPERATIONS, TeamMember.VIEWER):
            self._attack(self.team_s1[role], urls, secrets)

    def test_the_owners_can_reach_their_own_resources(self):
        # Guards the test itself: the same ids do work for their real owners.
        self.client.login(username=self.team_b[TeamMember.PROCUREMENT].email, password=PASSWORD)
        self.assertContains(self.client.get(reverse('requirement', args=[self.rfq_open.pk])), "SECRET-QUOTE-NOTE")
        self.assertContains(self.client.get(reverse('order-detail', args=[self.order.billno])), "SECRET-RFQ-AWARDED")
        self.assertContains(self.client.get(reverse('message-thread', args=[self.thread.pk])), "SECRET-MESSAGE")
        self.client.logout()
        self.client.login(username=self.team_s2[TeamMember.SALES].email, password=PASSWORD)
        self.assertContains(self.client.get(reverse('quote', args=[self.quote_open.pk])), "777.77")
