"""Seeds a broad set of realistic demo/test data across every app: buyers,
suppliers, teams, RFQs at every stage, quotes, orders through full
production, GST/export treatment, approvals, subscriptions/billing, GST
verification, messaging and staff auditing.

All seeded rows use emails ending in "@demo.makesetu.test", so a clean
re-seed is just:

    python manage.py seed_demo_data --flush
    python manage.py seed_demo_data

Every seeded user's password is "Demo@12345".
"""
import datetime
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import models, transaction
from django.utils import timezone

from accounts.models import (
    Certification, Company, ConsumerProfile, Machine, ManufacturerProfile,
    ManufacturingTech, MaterialCapability, SubscriptionPlan,
    TeamActivity, TeamInvitation, TeamMember, PendingRegistration, EmailVerification,
)
from billing.models import Payment, WebhookEvent
from core.models import AuditLogEntry, User
from gst.models import GSTVerification
from marketplace import documents
from marketplace.models import (
    AmendmentResponse, ApprovalRequest, Message, MessageThread, Order,
    OrderDocument, Quote, QuoteTemplate, RequirementAmendment,
    RequirementNDAAcceptance, RequirementPart, Requirement, RFQAlertPreference,
    RFQDecline, SupplierReview,
)
from plans import catalog
from plans.models import RFQReceipt, StoredFile

DEMO_DOMAIN = "demo.makesetu.test"
PASSWORD = "Demo@12345"
NOW = timezone.now()


def email(local):
    return f"{local}@{DEMO_DOMAIN}"


class Command(BaseCommand):
    help = "Seeds demo/test data covering RFQs, quotes, orders, teams, billing, GST and approvals."

    def add_arguments(self, parser):
        parser.add_argument('--flush', action='store_true', help="Delete any previously seeded demo data first.")

    def handle(self, *args, flush=False, **options):
        if flush:
            self._flush()
        if User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}").exists():
            self.stdout.write(self.style.WARNING(
                "Demo data already exists. Re-run with --flush to reset it first."
            ))
            return
        with transaction.atomic():
            self._seed()
        self.stdout.write(self.style.SUCCESS("Demo data seeded."))

    def _flush(self):
        # OrderDocument.order is on_delete=PROTECT, so any issued PO/invoice
        # must go before the Users->Profiles->Orders cascade can reach them.
        # Company is on_delete=PROTECT from the profile side too, and isn't
        # owned by the User cascade at all, so it's deleted explicitly once
        # the profiles pointing to it are gone.
        demo_user_ids = list(User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}").values_list('pk', flat=True))
        company_ids = set(
            ManufacturerProfile.objects.filter(user_id__in=demo_user_ids).values_list('company_id', flat=True)
        ) | set(
            ConsumerProfile.objects.filter(user_id__in=demo_user_ids).values_list('company_id', flat=True)
        )
        company_ids.discard(None)
        OrderDocument.objects.filter(
            models.Q(order__supplier__user_id__in=demo_user_ids) | models.Q(order__customer__user_id__in=demo_user_ids)
        ).delete()
        deleted, _ = User.objects.filter(pk__in=demo_user_ids).delete()
        Company.objects.filter(pk__in=company_ids).delete()
        PendingRegistration.objects.filter(email__endswith=f"@{DEMO_DOMAIN}").delete()
        # Not tied to any demo FK, so it survives the cascade above.
        WebhookEvent.objects.filter(event_id__startswith='evt_demo').delete()
        self.stdout.write(f"Flushed {deleted} demo rows (cascaded).")

    # -- helpers --------------------------------------------------------

    def _user(self, username, first_name, last_name, role, **extra):
        user = User.objects.create_user(
            username=username, email=email(username), password=PASSWORD,
            first_name=first_name, last_name=last_name, role=role, email_verified=True,
            **extra,
        )
        return user

    def _subscription(self, owner_user, plan_type, **extra):
        extra.setdefault('price', 0)
        extra.setdefault('started_at', NOW)
        extra.setdefault('current_period_start', NOW)
        return SubscriptionPlan.objects.create(
            user_profile=owner_user, plan_type=plan_type, **extra,
        )

    def _file(self, name, content=b"%PDF-1.4 demo file content for storage testing\n"):
        return ContentFile(content, name=name)

    def _seed(self):
        self.stdout.write("Creating manufacturing/material lookups...")
        tech_milling, _ = ManufacturingTech.objects.get_or_create(technology_type='milling')
        tech_turning, _ = ManufacturingTech.objects.get_or_create(technology_type='turning')
        mat_steel, _ = MaterialCapability.objects.get_or_create(material_type='structural_steel')
        mat_alu, _ = MaterialCapability.objects.get_or_create(material_type='aluminium')

        # ---------------------------------------------------------------
        # 1. Fresh signups, incomplete profiles (Free plan)
        # ---------------------------------------------------------------
        self.stdout.write("1. Fresh signups...")
        buyer_new_user = self._user('buyer_new', 'Nina', 'Fresh', 'consumer')
        buyer_new = ConsumerProfile.objects.create(
            user=buyer_new_user, Name='Fresh Buyer Co', type_of_business='Others',
            city='Pune', state='Maharashtra', country='India', phone='9000000001',
            email=email('buyer_new'), EORI_number='EORI-DEMO-001', VAT_number='VAT-DEMO-001',
        )
        self._subscription(buyer_new_user, catalog.FREE)

        supplier_new_user = self._user('supplier_new', 'Sam', 'Newcomer', 'manufacturer')
        trial_company = Company.objects.create(legal_name='Trial Forge Industries Pvt Ltd', name='Trial Forge')
        supplier_new = ManufacturerProfile.objects.create(
            user=supplier_new_user, company=trial_company, companyname='Trial Forge',
            phone='9000000002', address='Plot 12, MIDC', city='Nashik', state='Maharashtra', country='India',
            amount_of_employees='10-20', turnover_per_year='<1', email=email('supplier_new'),
        )
        self._subscription(supplier_new_user, catalog.FREE)

        # GST verification trail for this company: one failed lookup, then success.
        GSTVerification.objects.create(
            company=trial_company, gstin='27AAACT1111F1Z1', provider='mock', status=GSTVerification.Status.FAILED,
            error_code='GST_PROVIDER_TIMEOUT', error_message='Provider timed out after 10s.', created_at=NOW - datetime.timedelta(days=10),
        )
        GSTVerification.objects.create(
            company=trial_company, gstin='27AAACT1111F1Z1', provider='mock', status=GSTVerification.Status.SUCCESS,
            legal_name='Trial Forge Industries Pvt Ltd', trade_name='Trial Forge', gst_status='Active',
            registration_date=datetime.date(2020, 6, 1), taxpayer_type='Regular', business_constitution='Private Limited Company',
            principal_address={'address': 'Plot 12, MIDC', 'city': 'Nashik'}, state='Maharashtra', state_code='27', pincode='422101',
            verified_at=NOW - datetime.timedelta(days=9), created_at=NOW - datetime.timedelta(days=9),
        )
        trial_company.gstin = '27AAACT1111F1Z1'
        trial_company.gst_verified = True
        trial_company.gst_status = 'Active'
        trial_company.gst_registration_date = datetime.date(2020, 6, 1)
        trial_company.taxpayer_type = 'Regular'
        trial_company.business_constitution = 'Private Limited Company'
        trial_company.principal_address = 'Plot 12, MIDC'
        trial_company.city = 'Nashik'
        trial_company.state = 'Maharashtra'
        trial_company.state_code = '27'
        trial_company.pincode = '422101'
        trial_company.gst_verified_at = NOW - datetime.timedelta(days=9)
        trial_company.save()

        # ---------------------------------------------------------------
        # 2. Buyer company with a full team (Business plan)
        # ---------------------------------------------------------------
        self.stdout.write("2. Buyer team (Alpha Trading)...")
        buyer_owner_user = self._user('buyer_owner', 'Asha', 'Rao', 'consumer')
        alpha_company = Company.objects.create(
            legal_name='Alpha Trading Private Limited', name='Alpha Trading', gstin='27AAAPL5678C1ZV',
            gst_verified=True, gst_status='Active', gst_registration_date=datetime.date(2018, 4, 1),
            taxpayer_type='Regular', business_constitution='Private Limited Company',
            principal_address='14 MG Road', city='Pune', state='Maharashtra', state_code='27', pincode='411001',
            gst_verified_at=NOW - datetime.timedelta(days=200),
        )
        buyer_owner = ConsumerProfile.objects.create(
            user=buyer_owner_user, company=alpha_company, Name='Alpha Trading Private Limited',
            type_of_business='custom_machinery', Address='14 MG Road', city='Pune', state='Maharashtra',
            country='India', phone='9000000010', email=email('buyer_owner'),
            EORI_number='EORI-DEMO-010', VAT_number='VAT-DEMO-010', industry_Choice=161,
        )
        buyer_sub = self._subscription(
            buyer_owner_user, catalog.BUSINESS, status=SubscriptionPlan.ACTIVE, auto_renew=True,
            started_at=NOW - datetime.timedelta(days=120), current_period_start=NOW - datetime.timedelta(days=5),
            expires_at=NOW + datetime.timedelta(days=25), price=Decimal('2999.00'),
        )
        Payment.objects.create(
            subscription=buyer_sub, kind=Payment.NEW, plan_type=catalog.STARTER, billing_cycle='monthly',
            amount=Decimal('999.00'), currency='INR', status=Payment.SUCCEEDED, gateway='razorpay',
            gateway_order_id='order_demo_alpha_1', gateway_payment_id='pay_demo_alpha_1', applied=True,
            created_by=buyer_owner_user, paid_at=NOW - datetime.timedelta(days=120),
        )
        Payment.objects.create(
            subscription=buyer_sub, kind=Payment.UPGRADE, plan_type=catalog.BUSINESS, billing_cycle='monthly',
            amount=Decimal('2200.00'), currency='INR', status=Payment.SUCCEEDED, gateway='razorpay',
            gateway_order_id='order_demo_alpha_2', gateway_payment_id='pay_demo_alpha_2', applied=True,
            basis_plan_type=catalog.STARTER, proration={'credit': '799.00', 'days_left': 20},
            created_by=buyer_owner_user, paid_at=NOW - datetime.timedelta(days=5),
        )

        buyer_admin_user = self._user('buyer_admin', 'Vikram', 'Shah', 'consumer')
        TeamMember.objects.create(user=buyer_admin_user, buyer=buyer_owner, role=TeamMember.ADMIN, invited_by=buyer_owner_user)
        buyer_procurement_user = self._user('buyer_procurement', 'Priya', 'Nair', 'consumer')
        TeamMember.objects.create(user=buyer_procurement_user, buyer=buyer_owner, role=TeamMember.PROCUREMENT, invited_by=buyer_owner_user)
        buyer_viewer_user = self._user('buyer_viewer', 'Karan', 'Mehta', 'consumer')
        TeamMember.objects.create(user=buyer_viewer_user, buyer=buyer_owner, role=TeamMember.VIEWER, invited_by=buyer_owner_user)

        TeamInvitation.objects.create(
            buyer=buyer_owner, email=email('buyer_pending_invite'), first_name='Rohit', last_name='Jain',
            role=TeamMember.PROCUREMENT, token_hash='demo-token-hash-buyer-invite-0001',
            invited_by=buyer_owner_user, expires_at=NOW + datetime.timedelta(days=7),
        )
        for action, summary in [
            ('team.invited', 'Invited Rohit Jain as Procurement'),
            ('rfq.created', 'Created RFQ "Precision Shaft Milling Batch"'),
            ('quote.rejected', 'Rejected a quote on "Precision Shaft Milling Batch"'),
        ]:
            TeamActivity.objects.create(buyer=buyer_owner, actor=buyer_owner_user, actor_name='Asha Rao', action=action, summary=summary)

        # ---------------------------------------------------------------
        # 3. Supplier company with a full team (Business plan)
        # ---------------------------------------------------------------
        self.stdout.write("3. Supplier team (Precision Components)...")
        supplier_owner_user = self._user('supplier_owner', 'Deepak', 'Kulkarni', 'manufacturer')
        precision_company = Company.objects.create(
            legal_name='Precision Components Private Limited', name='Precision Components', gstin='27AABCP9012D1ZQ',
            gst_verified=True, gst_status='Active', gst_registration_date=datetime.date(2015, 7, 1),
            taxpayer_type='Regular', business_constitution='Private Limited Company',
            principal_address='Plot 45, Bhosari Industrial Area', city='Pune', state='Maharashtra',
            state_code='27', pincode='411026', gst_verified_at=NOW - datetime.timedelta(days=300),
        )
        supplier_owner = ManufacturerProfile.objects.create(
            user=supplier_owner_user, company=precision_company, companyname='Precision Components',
            phone='9000000020', address='Plot 45, Bhosari Industrial Area', city='Pune', state='Maharashtra',
            country='India', amount_of_employees='100-200', turnover_per_year='10-20',
            certificates='ISO 9001:2015', email=email('supplier_owner'),
            about='Precision CNC milling and turning for automotive and industrial OEMs since 2008.',
            minimum_order_qty=50, typical_lead_time_days=15, contact_name='Deepak Kulkarni',
            contact_role='Director', contact_phone='9000000020',
        )
        supplier_owner.capabilities.add(tech_milling, tech_turning)
        supplier_owner.materials.add(mat_steel, mat_alu)
        Machine.objects.create(manufacturer=supplier_owner, machine_type='5-axis VMC', make_model='DMG MORI DMU 50', quantity=3, work_envelope='500x450x400mm')
        Machine.objects.create(manufacturer=supplier_owner, machine_type='CNC Lathe', make_model='Haas ST-20', quantity=2, work_envelope='Dia 400mm')
        Certification.objects.create(
            manufacturer=supplier_owner, name='ISO 9001:2015', valid_until=datetime.date(2027, 6, 30),
            document=self._file('iso-9001-precision.pdf'), is_verified=True,
        )
        QuoteTemplate.objects.create(
            supplier=supplier_owner, name='Standard 30-day terms', tooling_cost=Decimal('5000.00'),
            lead_time_value=20, lead_time_unit='days', payment_terms='50_50', valid_for_days=30,
            note='Standard pricing for repeat orders.', created_by=supplier_owner_user,
        )
        RFQAlertPreference.objects.create(supplier=supplier_owner, processes=['Milling', 'Turning'], materials=['Aluminium', 'Structural steel'], min_quantity=50)

        supplier_sub = self._subscription(
            supplier_owner_user, catalog.BUSINESS, status=SubscriptionPlan.ACTIVE, auto_renew=True,
            started_at=NOW - datetime.timedelta(days=90), current_period_start=NOW - datetime.timedelta(days=10),
            expires_at=NOW + datetime.timedelta(days=20), price=Decimal('2999.00'),
        )
        Payment.objects.create(
            subscription=supplier_sub, kind=Payment.NEW, plan_type=catalog.BUSINESS, billing_cycle='monthly',
            amount=Decimal('2999.00'), currency='INR', status=Payment.SUCCEEDED, gateway='razorpay',
            gateway_order_id='order_demo_precision_1', gateway_payment_id='pay_demo_precision_1', applied=True,
            created_by=supplier_owner_user, paid_at=NOW - datetime.timedelta(days=90),
        )

        supplier_sales_user = self._user('supplier_sales', 'Meera', 'Joshi', 'manufacturer')
        TeamMember.objects.create(user=supplier_sales_user, supplier=supplier_owner, role=TeamMember.SALES, invited_by=supplier_owner_user)
        supplier_ops_user = self._user('supplier_ops', 'Ravi', 'Patil', 'manufacturer')
        TeamMember.objects.create(user=supplier_ops_user, supplier=supplier_owner, role=TeamMember.OPERATIONS, invited_by=supplier_owner_user)
        TeamActivity.objects.create(supplier=supplier_owner, actor=supplier_owner_user, actor_name='Deepak Kulkarni', action='team.invited', summary='Invited Meera Joshi as Sales')

        # ---------------------------------------------------------------
        # 4. Competitor suppliers (Starter / past-due Business)
        # ---------------------------------------------------------------
        self.stdout.write("4. Competitor suppliers...")
        supplier_b_user = self._user('supplier_b', 'Farah', 'Sheikh', 'manufacturer')
        bright_company = Company.objects.create(legal_name='Bright Metal Works Pvt Ltd', name='Bright Metal Works', gstin='27AABCB3456E1ZK', gst_verified=True, gst_status='Active')
        supplier_b = ManufacturerProfile.objects.create(
            user=supplier_b_user, company=bright_company, companyname='Bright Metal Works',
            phone='9000000030', address='Sector 5, MIDC', city='Aurangabad', state='Maharashtra', country='India',
            amount_of_employees='50-100', turnover_per_year='5-10', email=email('supplier_b'),
        )
        self._subscription(supplier_b_user, catalog.STARTER, status=SubscriptionPlan.ACTIVE, price=Decimal('999.00'), expires_at=NOW + datetime.timedelta(days=15))

        supplier_c_user = self._user('supplier_c', 'Thomas', 'Kutty', 'manufacturer')
        coastal_company = Company.objects.create(legal_name='Coastal CNC Solutions Pvt Ltd', name='Coastal CNC Solutions', gstin='32AABCC7890G1ZH', gst_verified=True, gst_status='Active')
        supplier_c = ManufacturerProfile.objects.create(
            user=supplier_c_user, company=coastal_company, companyname='Coastal CNC Solutions',
            phone='9000000040', address='Industrial Estate', city='Kochi', state='Kerala', country='India',
            amount_of_employees='20-50', turnover_per_year='1-5', email=email('supplier_c'),
        )
        coastal_sub = self._subscription(
            supplier_c_user, catalog.BUSINESS, status=SubscriptionPlan.PAST_DUE, price=Decimal('2999.00'),
            started_at=NOW - datetime.timedelta(days=60), current_period_start=NOW - datetime.timedelta(days=32),
            expires_at=NOW - datetime.timedelta(days=2), grace_until=NOW + datetime.timedelta(days=5),
            next_retry_at=NOW + datetime.timedelta(days=1), renewal_attempts=2,
        )
        failed_payment = Payment.objects.create(
            subscription=coastal_sub, kind=Payment.RENEWAL, plan_type=catalog.BUSINESS, billing_cycle='monthly',
            amount=Decimal('2999.00'), currency='INR', status=Payment.FAILED, gateway='razorpay',
            gateway_order_id='order_demo_coastal_1', attempt=2, failure_reason='Card declined by issuing bank.',
            created_at=NOW - datetime.timedelta(days=2),
        )
        WebhookEvent.objects.create(
            gateway='razorpay', event_id='evt_demo_coastal_failed_1', event_type='payment.failed',
            payload={'order_id': 'order_demo_coastal_1', 'reason': 'card_declined'},
            processed_at=NOW - datetime.timedelta(days=2), result=f'Marked payment {failed_payment.pk} failed; subscription moved to past_due.',
        )

        # ---------------------------------------------------------------
        # 5. Export buyer + LUT / no-LUT suppliers
        # ---------------------------------------------------------------
        self.stdout.write("5. Export buyer and suppliers...")
        buyer_export_user = self._user('buyer_export', 'Lars', 'Nilsen', 'consumer')
        buyer_export = ConsumerProfile.objects.create(
            user=buyer_export_user, Name='Nordic Engineering AS', type_of_business='infrastructure',
            city='Oslo', state='Oslo', country='Norway', phone='9000000050', email=email('buyer_export'),
            EORI_number='NO1234567890EORI', VAT_number='NO123456789MVA', industry_Choice=68,
        )
        self._subscription(buyer_export_user, catalog.STARTER, status=SubscriptionPlan.ACTIVE, price=Decimal('999.00'), expires_at=NOW + datetime.timedelta(days=18))

        supplier_lut_user = self._user('supplier_lut', 'Anil', 'Deshmukh', 'manufacturer')
        exportready_company = Company.objects.create(
            legal_name='ExportReady Forge Private Limited', name='ExportReady Forge', gstin='24AABCE2345H1ZF',
            gst_verified=True, gst_status='Active', lut_reference='AD240324001234R', lut_valid_until=datetime.date(2027, 3, 31),
        )
        supplier_lut = ManufacturerProfile.objects.create(
            user=supplier_lut_user, company=exportready_company, companyname='ExportReady Forge',
            phone='9000000060', address='GIDC Estate', city='Ahmedabad', state='Gujarat', country='India',
            amount_of_employees='200-500', turnover_per_year='20-50', email=email('supplier_lut'),
        )
        self._subscription(supplier_lut_user, catalog.BUSINESS, status=SubscriptionPlan.ACTIVE, price=Decimal('2999.00'), expires_at=NOW + datetime.timedelta(days=22))

        supplier_igst_user = self._user('supplier_igst', 'Sunil', 'Rathi', 'manufacturer')
        global_company = Company.objects.create(legal_name='Global Machining Co Pvt Ltd', name='Global Machining', gstin='29AABCG6789J1ZD', gst_verified=True, gst_status='Active')
        supplier_igst = ManufacturerProfile.objects.create(
            user=supplier_igst_user, company=global_company, companyname='Global Machining',
            phone='9000000070', address='Peenya Industrial Area', city='Bengaluru', state='Karnataka', country='India',
            amount_of_employees='50-100', turnover_per_year='5-10', email=email('supplier_igst'),
        )
        self._subscription(supplier_igst_user, catalog.STARTER, status=SubscriptionPlan.ACTIVE, price=Decimal('999.00'), expires_at=NOW + datetime.timedelta(days=12))

        # ---------------------------------------------------------------
        # 6. RFQ #1: multi-quote comparison, undecided, with a revision
        #    requested then declined, and a live message thread.
        # ---------------------------------------------------------------
        self.stdout.write("6. RFQ #1 - multi-quote comparison...")
        r1 = Requirement.objects.create(
            user=buyer_owner_user, title='Precision Shaft Milling Batch',
            rfq_desc='250 precision-milled steel shafts, tight tolerance, for an automotive gearbox line.',
            quote_currency='INR', request_reason='new_product', parts=1,
            end_date=NOW + datetime.timedelta(days=10), industry='Automotive and vehicle construction',
            file=self._file('r1-drawing.pdf'),
        )
        RequirementPart.objects.create(
            requirement=r1, part_name='Input Shaft', Part_desc='EN8 steel, ground finish', technology='Milling',
            Material='Structural steel', quantity=250,
        )
        q1a = Quote.objects.create(
            requirement=r1, supplier=supplier_owner, quote_price=Decimal('450.00'), tooling_cost=Decimal('15000.00'),
            lead_time_value=20, lead_time_unit='days', payment_terms='50_50', valid_until=NOW.date() + datetime.timedelta(days=30),
            submitted_at=NOW - datetime.timedelta(days=3), created_by=supplier_owner_user, note='Can expedite for an extra 5%.',
        )
        q1b = Quote.objects.create(
            requirement=r1, supplier=supplier_b, quote_price=Decimal('470.00'), tooling_cost=Decimal('12000.00'),
            lead_time_value=25, lead_time_unit='days', payment_terms='net_30', valid_until=NOW.date() + datetime.timedelta(days=20),
            submitted_at=NOW - datetime.timedelta(days=4), created_by=supplier_b_user, quote_file=self._file('q1b-quote.pdf'),
            revision_requested_at=None, revision_declined_at=NOW - datetime.timedelta(days=1),
            revision_note='Buyer asked for a faster lead time; supplier kept original terms.',
        )
        q1c = Quote.objects.create(
            requirement=r1, supplier=supplier_c, quote_price=Decimal('440.00'), tooling_cost=Decimal('18000.00'),
            lead_time_value=4, lead_time_unit='weeks', payment_terms='100_advance', valid_until=NOW.date() + datetime.timedelta(days=15),
            submitted_at=NOW - datetime.timedelta(days=2), created_by=supplier_c_user,
        )
        thread1 = MessageThread.objects.create(requirement=r1, supplier=supplier_owner)
        Message.objects.create(thread=thread1, sender=buyer_owner_user, body='Can you confirm the surface finish spec for the shaft?')
        Message.objects.create(thread=thread1, sender=supplier_owner_user, body='Yes, we quote Ra 0.8 as standard, can go to Ra 0.4 on request.')
        Message.objects.create(thread=thread1, sender=buyer_owner_user, body='Ra 0.8 works. Please confirm tooling lead time separately.')
        Message.objects.create(thread=thread1, sender=supplier_owner_user, body='Tooling is 7 days, included in the 20-day lead time quoted.')
        thread1.supplier_last_read_at = NOW - datetime.timedelta(hours=2)
        thread1.save()

        # ---------------------------------------------------------------
        # 7. RFQ #2: full order lifecycle through completion, with PO,
        #    invoice, QC checklist, review and a closed message thread.
        # ---------------------------------------------------------------
        self.stdout.write("7. RFQ #2 - full order lifecycle...")
        r2 = Requirement.objects.create(
            user=buyer_owner_user, title='Aluminium Bracket Production', rfq_desc='1000 aluminium mounting brackets, anodized.',
            quote_currency='INR', request_reason='second_source', parts=1, status='Completed',
        )
        RequirementPart.objects.create(requirement=r2, part_name='Mounting Bracket', Part_desc='6061-T6 aluminium, black anodized', technology='Milling', Material='Aluminium', quantity=1000)
        q2 = Quote.objects.create(
            requirement=r2, supplier=supplier_owner, quote_price=Decimal('120.00'), tooling_cost=Decimal('8000.00'),
            lead_time_value=15, lead_time_unit='days', payment_terms='50_50', valid_until=NOW.date() + datetime.timedelta(days=30),
            submitted_at=NOW - datetime.timedelta(days=40), created_by=supplier_owner_user,
            status='Approved', is_selected=True, decided_at=NOW - datetime.timedelta(days=38),
        )
        order2 = Order.objects.create(requirement=r2, quote=q2, supplier=supplier_owner, customer=buyer_owner, courier='bluedart', tracking_number='BD123456789IN', eway_bill_number='EWB-DEMO-0002')
        order2.mark_quoted(note='Quote approved by buyer')
        order2.select_quote(note='Quote #%s awarded by the buyer' % q2.pk)
        order2.start_production(note='Supplier started production')
        order2.save()
        for _ in range(5):
            order2.advance_production_stage(note='Stage advanced')
        for index in range(len(order2.qc_checklist)):
            order2.toggle_qc_item(index, supplier_ops_user)
        order2.request_payment(note='Payment requested on dispatch')
        order2.mark_paid(note='Payment received via bank transfer')
        order2.complete(note='Delivered and confirmed by buyer')
        order2.save()
        documents.issue_purchase_order(order2)
        documents.issue_invoice(order2)
        SupplierReview.objects.create(order=order2, rating=5, comment='Excellent quality and on-time delivery. Will reorder.')
        thread2 = MessageThread.objects.create(requirement=r2, supplier=supplier_owner, closed_at=NOW - datetime.timedelta(days=1), closed_by=buyer_owner_user)
        Message.objects.create(thread=thread2, sender=supplier_owner_user, body='Order dispatched, tracking BD123456789IN.')
        Message.objects.create(thread=thread2, sender=buyer_owner_user, body='Received in good condition, thank you!')

        # ---------------------------------------------------------------
        # 8. RFQ #3 (export, LUT): zero-rated export order
        # ---------------------------------------------------------------
        self.stdout.write("8. RFQ #3 - export order (LUT, zero-rated)...")
        r3 = Requirement.objects.create(
            user=buyer_export_user, title='CNC Turned Fittings Export Order', rfq_desc='5000 brass hydraulic fittings for export to Norway.',
            quote_currency='EUR', request_reason='new_product', parts=1,
        )
        RequirementPart.objects.create(requirement=r3, part_name='Hydraulic Fitting', Part_desc='Brass, CNC turned', technology='Turning', Material='Aluminium', quantity=5000)
        q3 = Quote.objects.create(
            requirement=r3, supplier=supplier_lut, quote_price=Decimal('3.50'), tooling_cost=Decimal('500.00'),
            lead_time_value=30, lead_time_unit='days', payment_terms='against_lc', valid_until=NOW.date() + datetime.timedelta(days=45),
            submitted_at=NOW - datetime.timedelta(days=15), created_by=supplier_lut_user,
            status='Approved', is_selected=True, decided_at=NOW - datetime.timedelta(days=14),
        )
        order3 = Order.objects.create(requirement=r3, quote=q3, supplier=supplier_lut, customer=buyer_export)
        order3.mark_quoted()
        order3.select_quote(note=f'Quote #{q3.pk} awarded by the buyer')
        order3.start_production(note='Supplier started production')
        order3.request_payment(note='Export shipment ready, awaiting payment confirmation')
        order3.save()
        documents.issue_purchase_order(order3)

        # ---------------------------------------------------------------
        # 9. RFQ #4 (export, no LUT): IGST-on-export order
        # ---------------------------------------------------------------
        self.stdout.write("9. RFQ #4 - export order (IGST)...")
        r4 = Requirement.objects.create(
            user=buyer_export_user, title='Stainless Steel Flange Export', rfq_desc='800 stainless steel pipe flanges for export.',
            quote_currency='USD', request_reason='benchmarking', parts=1,
        )
        RequirementPart.objects.create(requirement=r4, part_name='Pipe Flange', Part_desc='SS316, forged and machined', technology='Turning', Material='Stainless steel', quantity=800)
        q4 = Quote.objects.create(
            requirement=r4, supplier=supplier_igst, quote_price=Decimal('22.00'), tooling_cost=Decimal('0.00'),
            lead_time_value=25, lead_time_unit='days', payment_terms='net_45', valid_until=NOW.date() + datetime.timedelta(days=20),
            submitted_at=NOW - datetime.timedelta(days=6), created_by=supplier_igst_user,
            status='Approved', is_selected=True, decided_at=NOW - datetime.timedelta(days=5),
        )
        order4 = Order.objects.create(requirement=r4, quote=q4, supplier=supplier_igst, customer=buyer_export)
        order4.mark_quoted()
        order4.save()

        # ---------------------------------------------------------------
        # 10. RFQ #5: NDA-gated - one supplier accepted, one hasn't
        # ---------------------------------------------------------------
        self.stdout.write("10. RFQ #5 - NDA-gated...")
        r5 = Requirement.objects.create(
            user=buyer_owner_user, title='Confidential Prototype Housing', rfq_desc='Prototype enclosure for an unreleased product, NDA required.',
            quote_currency='INR', request_reason='new_product', parts=1, nda_required=True,
        )
        RequirementPart.objects.create(requirement=r5, part_name='Enclosure Shell', Part_desc='Confidential - see NDA', technology='Milling', Material='Aluminium', quantity=20)
        RequirementNDAAcceptance.objects.create(requirement=r5, supplier=supplier_owner)
        Quote.objects.create(
            requirement=r5, supplier=supplier_owner, quote_price=Decimal('2500.00'), tooling_cost=Decimal('30000.00'),
            lead_time_value=10, lead_time_unit='days', submitted_at=NOW - datetime.timedelta(days=1), created_by=supplier_owner_user,
        )
        # supplier_b has NOT accepted the NDA and has not quoted.

        # ---------------------------------------------------------------
        # 11. RFQ #6: amendment with mixed supplier responses
        # ---------------------------------------------------------------
        self.stdout.write("11. RFQ #6 - amendment...")
        r6 = Requirement.objects.create(
            user=buyer_owner_user, title='Gearbox Housing - Rev Control', rfq_desc='Cast and machined gearbox housings.',
            quote_currency='INR', request_reason='new_product', parts=1,
        )
        r6_part = RequirementPart.objects.create(requirement=r6, part_name='Gearbox Housing', Part_desc='Cast iron, machined', technology='Milling', Material='Structural steel', quantity=100)
        q6a = Quote.objects.create(
            requirement=r6, supplier=supplier_owner, quote_price=Decimal('1800.00'), tooling_cost=Decimal('40000.00'),
            lead_time_value=25, lead_time_unit='days', submitted_at=NOW - datetime.timedelta(days=5), created_by=supplier_owner_user,
        )
        q6b = Quote.objects.create(
            requirement=r6, supplier=supplier_b, quote_price=Decimal('1750.00'), tooling_cost=Decimal('42000.00'),
            lead_time_value=28, lead_time_unit='days', submitted_at=NOW - datetime.timedelta(days=5), created_by=supplier_b_user,
        )
        amendment = RequirementAmendment.objects.create(
            requirement=r6, proposed_by=buyer_owner_user,
            changes={'parts': {str(r6_part.pk): {'quantity': {'old': 100, 'new': 150}}}},
            status=RequirementAmendment.PENDING,
        )
        AmendmentResponse.objects.create(
            amendment=amendment, quote=q6a, status=AmendmentResponse.ACCEPTED,
            supplier_note='We can do 150 units at a slightly better unit price.',
            quote_price=Decimal('1700.00'), tooling_cost=Decimal('40000.00'), lead_time_value=30, lead_time_unit='days',
            responded_at=NOW - datetime.timedelta(hours=12),
        )
        AmendmentResponse.objects.create(
            amendment=amendment, quote=q6b, status=AmendmentResponse.REJECTED,
            supplier_note='150 units exceeds our current casting capacity for this lead time.',
            responded_at=NOW - datetime.timedelta(hours=10),
        )

        # ---------------------------------------------------------------
        # 12. RFQ #7: expired with no quotes
        # ---------------------------------------------------------------
        self.stdout.write("12. RFQ #7 - expired, no quotes...")
        Requirement.objects.create(
            user=buyer_owner_user, title='Urgent Laser Cutting - Expired', rfq_desc='Sheet metal laser cutting, 50 panels.',
            quote_currency='INR', request_reason='other', parts=1, end_date=NOW - datetime.timedelta(days=3),
        )

        # ---------------------------------------------------------------
        # 13. RFQ #8: declined by a supplier
        # ---------------------------------------------------------------
        self.stdout.write("13. RFQ #8 - declined...")
        r8 = Requirement.objects.create(
            user=buyer_owner_user, title='Heavy Fabrication Weldment', rfq_desc='Large structural steel weldment, 2 tonnes.',
            quote_currency='INR', request_reason='other', parts=1,
        )
        RequirementPart.objects.create(requirement=r8, part_name='Structural Weldment', Part_desc='Heavy fabrication', technology='Full-range turning', Material='Structural steel', quantity=2)
        RFQDecline.objects.create(requirement=r8, supplier=supplier_b, reason='Outside our capability range (too large for our shop floor).')

        # ---------------------------------------------------------------
        # 14. RFQ #9: awarded then cancelled order
        # ---------------------------------------------------------------
        self.stdout.write("14. RFQ #9 - cancelled order...")
        r9 = Requirement.objects.create(
            user=buyer_owner_user, title='Injection Molded Enclosure', rfq_desc='500 injection molded plastic enclosures.',
            quote_currency='INR', request_reason='new_product', parts=1, status='Rejected',
        )
        RequirementPart.objects.create(requirement=r9, part_name='Enclosure', Part_desc='ABS plastic', technology='Milling', Material='Aluminium', quantity=500)
        q9 = Quote.objects.create(
            requirement=r9, supplier=supplier_c, quote_price=Decimal('95.00'), tooling_cost=Decimal('60000.00'),
            lead_time_value=35, lead_time_unit='days', submitted_at=NOW - datetime.timedelta(days=20), created_by=supplier_c_user,
            status='Approved', is_selected=True, decided_at=NOW - datetime.timedelta(days=19),
        )
        order9 = Order.objects.create(requirement=r9, quote=q9, supplier=supplier_c, customer=buyer_owner)
        order9.mark_quoted()
        order9.select_quote(note=f'Quote #{q9.pk} awarded by the buyer')
        order9.save()
        order9.cancel(note='Buyer cancelled - project put on hold.')
        order9.save()

        # ---------------------------------------------------------------
        # 15. Approval workflow (Business plan): one pending request of
        #     each kind.
        # ---------------------------------------------------------------
        self.stdout.write("15. Approval workflow...")
        r10 = Requirement.objects.create(
            user=buyer_procurement_user, title='New Stamping Dies RFQ', rfq_desc='Progressive stamping dies for a new part family.',
            quote_currency='INR', request_reason='new_product', parts=1, approval_status='pending',
        )
        RequirementPart.objects.create(requirement=r10, part_name='Stamping Die', Part_desc='Progressive die, 3 stations', technology='Milling', Material='Structural steel', quantity=1)
        ApprovalRequest.objects.create(
            kind=ApprovalRequest.RFQ, buyer=buyer_owner, requirement=r10, requested_by=buyer_procurement_user,
            note='Please approve - first-time supplier spend over threshold.',
        )

        q11 = Quote.objects.create(
            requirement=r1, supplier=supplier_owner, quote_price=Decimal('460.00'), tooling_cost=Decimal('15000.00'),
            lead_time_value=18, lead_time_unit='days', created_by=supplier_sales_user, is_draft=True,
            approval_status='pending',
        )
        ApprovalRequest.objects.create(
            kind=ApprovalRequest.QUOTE, supplier=supplier_owner, requirement=r1, quote=q11, requested_by=supplier_sales_user,
            note='First quote from a new sales hire - please review before sending.',
        )

        ApprovalRequest.objects.create(
            kind=ApprovalRequest.AWARD, buyer=buyer_owner, requirement=r1, quote=q1a, requested_by=buyer_procurement_user,
            note='Recommend awarding to Precision Components - best lead time.',
        )

        ApprovalRequest.objects.create(
            kind=ApprovalRequest.PAYMENT, supplier=supplier_owner, order=order3, requested_by=supplier_sales_user,
            note='Export order ready to invoice once payment is confirmed by finance.',
        )

        # ---------------------------------------------------------------
        # 16. Staff / audit log
        # ---------------------------------------------------------------
        self.stdout.write("16. Staff and audit log...")
        admin_user = self._user('admin_demo', 'Staff', 'Admin', 'admin', is_staff=True)
        AuditLogEntry.objects.create(
            actor=admin_user, actor_email=admin_user.email, action='company.deactivated',
            target_type='ManufacturerProfile', target_id=str(supplier_b.pk), target_repr=str(supplier_b),
            detail='Deactivated pending a compliance document review.',
        )
        AuditLogEntry.objects.create(
            actor=admin_user, actor_email=admin_user.email, action='subscription.edited',
            target_type='SubscriptionPlan', target_id=str(coastal_sub.pk), target_repr=str(coastal_sub),
            detail='Extended grace period by 3 days after customer support escalation.',
        )
        AuditLogEntry.objects.create(
            actor=admin_user, actor_email=admin_user.email, action='feedback.moderated',
            target_type='SupplierReview', target_id='1', target_repr='Review of order #%s' % order2.pk,
            detail='Reviewed and approved for publication.',
        )

        # ---------------------------------------------------------------
        # 17. Unverified signup (OTP not confirmed)
        # ---------------------------------------------------------------
        self.stdout.write("17. Unverified signup...")
        pending = PendingRegistration.objects.create(
            email=email('pending_signup'), username='pending_signup', first_name='Not', last_name='Yet',
            password='pbkdf2_sha256$demo$unusable$hash', role='consumer',
        )
        EmailVerification.objects.create(
            pending_registration=pending, code_hash='demo-otp-hash-0001',
            expires_at=NOW + datetime.timedelta(minutes=10),
        )

        # ---------------------------------------------------------------
        # 18. Round out every role: supplier side also gets Admin and
        #     Viewer (Sales/Operations were already covered on
        #     supplier_owner above).
        # ---------------------------------------------------------------
        self.stdout.write("18. Remaining team roles (supplier admin/viewer)...")
        supplier_owner_admin_user = self._user('supplier_owner_admin', 'Nikhil', 'Bhatt', 'manufacturer')
        TeamMember.objects.create(user=supplier_owner_admin_user, supplier=supplier_owner, role=TeamMember.ADMIN, invited_by=supplier_owner_user)
        supplier_owner_viewer_user = self._user('supplier_owner_viewer', 'Pooja', 'Iyer', 'manufacturer')
        TeamMember.objects.create(user=supplier_owner_viewer_user, supplier=supplier_owner, role=TeamMember.VIEWER, invited_by=supplier_owner_user)

        # ---------------------------------------------------------------
        # 19. A Starter-plan buyer and a Starter-plan supplier, each with
        #     their own small team - every plan tier now has at least one
        #     team example on both sides (Free: owner only by default,
        #     Starter: owner + a couple of members, Business: full team).
        # ---------------------------------------------------------------
        self.stdout.write("19. Starter-plan teams...")
        buyer_starter_user = self._user('buyer_starter', 'Imran', 'Qureshi', 'consumer')
        starter_traders = ConsumerProfile.objects.create(
            user=buyer_starter_user, Name='Starter Tier Traders', type_of_business='electronics',
            city='Jaipur', state='Rajasthan', country='India', phone='9000000080', email=email('buyer_starter'),
            EORI_number='EORI-DEMO-080', VAT_number='VAT-DEMO-080',
        )
        self._subscription(buyer_starter_user, catalog.STARTER, status=SubscriptionPlan.ACTIVE, price=Decimal('999.00'), expires_at=NOW + datetime.timedelta(days=25))
        buyer_starter_admin_user = self._user('buyer_starter_admin', 'Zoya', 'Khan', 'consumer')
        TeamMember.objects.create(user=buyer_starter_admin_user, buyer=starter_traders, role=TeamMember.ADMIN, invited_by=buyer_starter_user)
        buyer_starter_procurement_user = self._user('buyer_starter_procurement', 'Aman', 'Gupta', 'consumer')
        TeamMember.objects.create(user=buyer_starter_procurement_user, buyer=starter_traders, role=TeamMember.PROCUREMENT, invited_by=buyer_starter_user)

        supplier_starter_admin_user = self._user('supplier_b_admin', 'Imtiaz', 'Ansari', 'manufacturer')
        TeamMember.objects.create(user=supplier_starter_admin_user, supplier=supplier_b, role=TeamMember.ADMIN, invited_by=supplier_b_user)

        # ---------------------------------------------------------------
        # 20. Free-plan buyer at its usage caps: 2/2 users, 5/5 RFQs this
        #     period - exercises the "upgrade to do more" paths.
        # ---------------------------------------------------------------
        self.stdout.write("20. Free-plan buyer at usage caps...")
        buyer_new_admin_user = self._user('buyer_new_admin', 'Omar', 'Siddiqui', 'consumer')
        TeamMember.objects.create(user=buyer_new_admin_user, buyer=buyer_new, role=TeamMember.ADMIN, invited_by=buyer_new_user)
        for index in range(1, 6):
            Requirement.objects.create(
                user=buyer_new_user, title=f'Free Plan Sample RFQ #{index}', rfq_desc='A small trial RFQ posted on the Free plan.',
                quote_currency='INR', request_reason='other', parts=1,
            )

        # ---------------------------------------------------------------
        # 21. Free-plan supplier at its usage caps: 2/2 users, 10/10 RFQs
        #     received this period, 10/10 quotes submitted this period.
        #     The 10 filler RFQs belong to the Business-plan buyer (which
        #     has plenty of room) so they don't also push buyer_owner over
        #     its own limit.
        # ---------------------------------------------------------------
        self.stdout.write("21. Free-plan supplier at usage caps...")
        supplier_new_admin_user = self._user('supplier_new_admin', 'Divya', 'Menon', 'manufacturer')
        TeamMember.objects.create(user=supplier_new_admin_user, supplier=supplier_new, role=TeamMember.ADMIN, invited_by=supplier_new_user)
        for index in range(1, 11):
            filler = Requirement.objects.create(
                user=buyer_owner_user, title=f'Filler RFQ for supplier cap test #{index}',
                rfq_desc='Minor RFQ used only to exercise a Free-plan supplier\'s monthly caps.',
                quote_currency='INR', request_reason='other', parts=1,
            )
            RequirementPart.objects.create(requirement=filler, part_name='Sample Part', Part_desc='', technology='Milling', Material='Aluminium', quantity=10)
            RFQReceipt.objects.create(supplier=supplier_new, requirement=filler)
            Quote.objects.create(
                requirement=filler, supplier=supplier_new, quote_price=Decimal('100.00'),
                submitted_at=NOW, created_by=supplier_new_user,
            )

        # ---------------------------------------------------------------
        # 22. Free-plan storage near its 1 GB cap - a real small file is
        #     uploaded (so the storage ledger has a genuine row), then its
        #     recorded size is raised to simulate being nearly full without
        #     actually writing a 1 GB file to disk.
        # ---------------------------------------------------------------
        self.stdout.write("22. Free-plan storage near its cap...")
        near_cap_cert = Certification.objects.create(
            manufacturer=supplier_new, name='Trial Certificate', document=self._file('trial-cert.pdf'),
        )
        StoredFile.objects.filter(
            content_type=ContentType.objects.get_for_model(Certification), object_id=near_cap_cert.pk, field='document',
        ).update(size=950 * 1024 * 1024)  # ~950 MB of its 1 GB Free-plan limit
