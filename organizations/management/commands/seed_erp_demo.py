"""Seeds demo data for the ERP: organizations with people in every kind of
role (full and light users, one person in two organizations), teams,
addresses and bank accounts, an open invitation, buyer/supplier
relationships in every status, and subscriptions in every billing state.

    python manage.py seed_erp_demo            # seed (refuses if already seeded)
    python manage.py seed_erp_demo --flush    # remove the demo data and seed again

Companies come from the mock GST provider's sample GSTINs, so their names
and addresses match what the GSTIN lookup shows. The other samples are left
free for registering a new company by hand; the command lists them.

Every demo user's email ends in @demo.makesetu.test and their password is
PASSWORD below. Only runs with DEBUG on (or --force), since it creates
accounts with a published password.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts import team as marketplace_team
from accounts.models import Company
from billing.models import OrganizationSubscription as Sub
from billing.services import grace_period
from core.models import User
from gst.services.providers.mock import SAMPLE_COMPANIES, MockGSTProvider
from gst.services.verification import GSTVerificationService
from organizations.models import Address, BankAccount, BusinessRelationship, Invitation, Membership, Team, TeamMembership
from organizations.services import set_up_organization
from inventory import services as inventory
from inventory.models import Batch, Item, StockEntry, Warehouse
from adjustments import services as adjustments
from adjustments.models import CreditDebitNote
from finance import services as finance
from finance.models import Payment
from plans import erp
from production import costing
from production import services as production
from production.models import BillOfMaterials, Machine, ProductionEntry
from quality import services as quality
from quality.models import InspectionLot
from purchase import services as purchase
from purchase.models import GoodsReceipt, PurchaseBill, PurchaseOrder
from sales import services as sales
from sales.models import Dispatch, SalesInvoice, SalesOrder

DEMO_DOMAIN = 'demo.makesetu.test'
PASSWORD = 'Setu#Demo-2026'
Status = BusinessRelationship.Status

# gstin: (buying, selling, plan, subscription state)
ORGANIZATIONS = {
    'sri_lakshmi': ('33AABCS1234K1Z7', True, True, erp.PRO, Sub.ACTIVE),
    'kaveri': ('27AADCK5678M1Z3', False, True, erp.GROWTH, Sub.TRIALING),
    'tejas': ('36AAHCT9753L1Z4', True, False, erp.GROWTH, Sub.PAST_DUE),
    'malabar': ('32AAKCM3141S1Z8', True, True, erp.STARTER, Sub.LAPSED),
}
# On the GST records but not on MakeSetu: a supplier Sri Lakshmi added by
# GSTIN. Registering with this GSTIN claims it.
UNCLAIMED_GSTIN = '24AAGCR1357Q1Z2'

# org: [(email local part, first, last, role key), ...]; the first is the Org Admin.
PEOPLE = {
    'sri_lakshmi': [
        ('arjun', 'Arjun', 'Raman', 'org_admin'),
        ('priya', 'Priya', 'Subramanian', 'purchase_manager'),
        ('karthik', 'Karthik', 'Venkatesan', 'purchase_executive'),
        ('divya', 'Divya', 'Natarajan', 'sales_manager'),
        ('suresh', 'Suresh', 'Babu', 'store'),
        ('meena', 'Meena', 'Krishnan', 'production_planner'),
        ('lakshmi.accounts', 'Lakshmi', 'Iyer', 'accounts'),
        ('vignesh', 'Vignesh', 'Murali', 'qc'),
        ('murugan', 'Murugan', 'S', 'machine_operator'),
        ('selvi', 'Selvi', 'R', 'machine_operator'),
        ('ravi', 'Ravi', 'Kumar', 'store_keeper'),
        ('kumar', 'Kumar', 'P', 'security'),
        ('anand', 'Anand', 'Raj', 'field_sales'),
    ],
    'kaveri': [
        ('nikhil', 'Nikhil', 'Deshpande', 'org_admin'),
        ('sneha', 'Sneha', 'Kulkarni', 'sales_manager'),
        ('rahul', 'Rahul', 'Patil', 'production_planner'),
        ('pooja', 'Pooja', 'Joshi', 'accounts'),
        ('ganesh', 'Ganesh', 'More', 'machine_operator'),
    ],
    'tejas': [
        ('sanjay', 'Sanjay', 'Reddy', 'org_admin'),
        ('kavya', 'Kavya', 'Rao', 'purchase_manager'),
        ('imran', 'Imran', 'Khan', 'purchase_executive'),
        ('anita', 'Anita', 'Goud', 'accounts'),
        ('ramesh', 'Ramesh', 'Naidu', 'store_keeper'),
    ],
    'malabar': [
        ('joseph', 'Joseph', 'Mathew', 'org_admin'),
        ('fathima', 'Fathima', 'Basheer', 'purchase_executive'),
        # Also Accounts at Sri Lakshmi: one person in two organizations.
        ('lakshmi.accounts', 'Lakshmi', 'Iyer', 'accounts'),
    ],
}

# org: [(team, head, [members])]
TEAMS = {
    'sri_lakshmi': [
        ('Purchase', 'priya', ['priya', 'karthik']),
        ('Sales', 'divya', ['divya', 'anand']),
        ('Production', 'meena', ['meena', 'vignesh', 'murugan', 'selvi']),
        ('Stores', 'suresh', ['suresh', 'ravi', 'kumar']),
    ],
    'kaveri': [('Sales', 'sneha', ['sneha']), ('Foundry', 'rahul', ['rahul', 'ganesh'])],
    'tejas': [('Procurement', 'kavya', ['kavya', 'imran', 'ramesh'])],
    'malabar': [('Operations', 'joseph', ['joseph', 'fathima'])],
}

# org: [(kind, label, line1, city, pincode)]
ADDRESSES = {
    'sri_lakshmi': [
        (Address.Kind.WORKS, 'Sriperumbudur plant', 'Plot B-7, SIPCOT Industrial Park', 'Sriperumbudur', '602105'),
        (Address.Kind.WAREHOUSE, 'Ambattur warehouse', '42, Ambattur Industrial Estate', 'Chennai', '600058'),
    ],
    'kaveri': [(Address.Kind.WORKS, 'Chakan foundry', 'Gat 311, Chakan MIDC Phase II', 'Pune', '410501')],
    'tejas': [(Address.Kind.SHIPPING, 'Receiving dock', 'Plot 9, Hardware Park, Gate 2', 'Hyderabad', '501510')],
}

# org: (account name, bank, account number, IFSC, branch)
BANKS = {
    'sri_lakshmi': ('Sri Lakshmi Precision Engineering Pvt Ltd', 'HDFC Bank', '50200012345678', 'HDFC0001234', 'Guindy'),
    'kaveri': ('Kaveri Castings Pvt Ltd', 'Bank of Maharashtra', '60123456789', 'MAHB0000456', 'Bhosari'),
    'tejas': ('Tejas Electronics Pvt Ltd', 'ICICI Bank', '000405012345', 'ICIC0000004', 'Gachibowli'),
}

# org: [(name, code, address label or None)]; the first is the default.
WAREHOUSES = {
    'sri_lakshmi': [('Plant store', 'PS', 'Sriperumbudur plant'), ('Ambattur warehouse', 'AMB', 'Ambattur warehouse')],
    'kaveri': [('Foundry store', 'FS', 'Chakan foundry')],
    'tejas': [('Receiving store', 'RS', 'Receiving dock')],
}

# org: [(code, name, kind, unit, HSN/SAC, GST %, purchase price, sale price, reorder level,
#        {warehouse code: (opening quantity, rate)})]
ITEMS = {
    'sri_lakshmi': [
        ('RM-EN8-40', 'EN8 round bar, 40 mm', 'raw_material', 'KGS', '7214', '18', '78', None, '1500', {'PS': ('4200', '76.50')}),
        ('RM-AL6061-P12', 'Aluminium 6061 plate, 12 mm', 'raw_material', 'KGS', '7606', '18', '310', None, '300', {'PS': ('640', '305')}),
        ('RM-SS304-S2', 'SS304 sheet, 2 mm', 'raw_material', 'KGS', '7219', '18', '245', None, '250', {'AMB': ('180', '240')}),
        ('BO-BOLT-M10', 'Hex bolt M10 x 50, grade 8.8', 'component', 'NOS', '7318', '18', '6.20', None, '1000',
         {'PS': ('3500', '6'), 'AMB': ('1200', '6.10')}),
        ('BO-BRG-6205', 'Ball bearing 6205-2RS', 'component', 'NOS', '8482', '18', '145', None, '100', {'AMB': ('60', '142')}),
        ('CO-OIL-CUT', 'Soluble cutting oil', 'consumable', 'LTR', '3403', '18', '185', None, '50', {'PS': ('210', '180')}),
        ('TL-INS-CNMG', 'CNMG 120408 carbide insert', 'tool', 'NOS', '8209', '18', '420', None, '20', {'PS': ('35', '410')}),
        ('FG-GS-120', 'Gear shaft GS-120', 'finished_good', 'NOS', '8483', '18', None, '850', None, {'AMB': ('240', '610')}),
        ('FG-PH-75', 'Pump housing PH-75', 'finished_good', 'NOS', '8413', '18', None, '2400', None, {'AMB': ('85', '1720')}),
        ('SV-CNC', 'CNC machining charges', 'service', 'OTH', '998898', '18', None, '1200', None, {}),
        ('BO-CAST-VB50', 'Valve body casting VB-50 (from Kaveri)', 'component', 'NOS', '7325', '18', '980', None, '50', {}),
    ],
    'kaveri': [
        ('RM-PIG-IRON', 'Foundry-grade pig iron', 'raw_material', 'TON', '7201', '18', '41000', None, '20', {'FS': ('48', '40500')}),
        ('RM-FESI-75', 'Ferro silicon 75%', 'raw_material', 'KGS', '7202', '18', '118', None, '500', {'FS': ('1250', '115')}),
        ('CO-SAND-SILICA', 'Silica moulding sand', 'consumable', 'TON', '2505', '5', '2600', None, '10', {'FS': ('35', '2550')}),
        ('FG-VB-50', 'Valve body casting VB-50', 'finished_good', 'NOS', '7325', '18', None, '1450', None, {'FS': ('320', '980')}),
    ],
    'tejas': [
        ('BO-PCB-CTRL', 'Controller PCB assembly, rev C', 'component', 'NOS', '8534', '18', '860', None, '50', {'RS': ('120', '845')}),
        ('BO-ENC-AL', 'Machined aluminium enclosure', 'component', 'NOS', '7616', '18', '640', None, '40', {'RS': ('25', '630')}),
        ('FG-DRV-5K', 'Motor drive unit 5 kW', 'finished_good', 'NOS', '8504', '18', None, '18500', None, {}),
    ],
}

# org: {item code: (tracking, shelf life in days, opening stock's batch number)}.
# Serial-numbered opening stock is numbered from the item code.
TRACKING = {
    'sri_lakshmi': {
        'RM-EN8-40': ('batch', None, 'H-24117'),        # mill heat number
        'CO-OIL-CUT': ('batch', 365, 'CO-2609'),
        'FG-GS-120': ('batch', None, 'GS-2610'),
        'BO-CAST-VB50': ('batch', None, None),           # received with Kaveri's heat number
    },
    'kaveri': {'FG-VB-50': ('batch', None, 'VB-2610')},
    'tejas': {'BO-PCB-CTRL': ('serial', None, None)},
}

# Fictional TANs (format only) for the demo companies that deduct TDS.
TANS = {'33AABCS1234K1Z7': 'CHES12345K', '27AADCK5678M1Z3': 'PNEK54321C'}

# org: (logo initials, logo colour, signatory, invoice terms)
BRANDING = {
    'sri_lakshmi': ('SLP', (17, 53, 92), 'Arjun Raman',
                    'Payment within the agreed credit period. Interest at 18% p.a. on overdue amounts. Subject to Chennai jurisdiction.'),
    'kaveri': ('KC', (124, 45, 18), 'Nikhil Deshpande',
               'Castings supplied to drawing and inspected before dispatch. Claims within 7 days of receipt. Subject to Pune jurisdiction.'),
}

# org: [(output item code, BOM name, makes, [(component code, quantity)])]
BOMS = {
    'sri_lakshmi': [
        ('FG-GS-120', 'Standard', '1', [('RM-EN8-40', '2.4'), ('CO-OIL-CUT', '0.05')]),
        ('FG-PH-75', 'Rev B', '1', [('RM-AL6061-P12', '3.2'), ('BO-BOLT-M10', '6')]),
    ],
    'kaveri': [
        ('FG-VB-50', 'Standard', '10', [('RM-PIG-IRON', '0.12'), ('RM-FESI-75', '3'), ('CO-SAND-SILICA', '0.2')]),
    ],
}
# Production costing. org: (labour ₹/h, overhead %, [(machine code, name, ₹/h)],
#   [(scrap item code, name, unit, HSN, sale price)],
#   {BOM output code: (machine, machine hours, labour hours, scrap item, scrap quantity)} for the BOM's quantity)
COSTING = {
    'sri_lakshmi': ('180', '40', [('CNC-01', 'CNC lathe LT-2', '550'), ('VMC-01', 'Vertical machining centre', '750')],
                    [('SC-EN8-TURN', 'EN8 turnings (scrap)', 'KGS', '7204', '22')],
                    {'FG-GS-120': ('CNC-01', '0.35', '0.4', 'SC-EN8-TURN', '0.6'),
                     'FG-PH-75': ('VMC-01', '0.8', '0.9', None, '0')}),
    'kaveri': ('150', '60', [('IND-01', 'Induction furnace, 500 kg', '1800'), ('MLD-01', 'Moulding line', '900')],
               [('SC-RUNNERS', 'Runners and risers (foundry returns)', 'KGS', '7204', '28')],
               {'FG-VB-50': ('IND-01', '0.5', '6', 'SC-RUNNERS', '12')}),
}

# org: [(operator, output item code, good, rejected, materials warehouse code, finished warehouse code, days ago, shift)]
PRODUCTION = {
    'sri_lakshmi': [('murugan', 'FG-GS-120', '40', '2', 'PS', 'AMB', 3, 'A')],
    'kaveri': [('ganesh', 'FG-VB-50', '60', '4', 'FS', 'FS', 2, 'B')],
}

# (buyer, supplier, status, initiated by, vendor code, customer code, payment days, credit limit)
RELATIONSHIPS = [
    ('tejas', 'sri_lakshmi', Status.ACTIVE, 'tejas', 'V-0012', 'C-TEJ01', 45, 500000),
    ('sri_lakshmi', 'kaveri', Status.ACTIVE, 'sri_lakshmi', 'V-CAST-07', 'KC-0031', 30, 300000),
    ('sri_lakshmi', 'malabar', Status.ACTIVE, 'sri_lakshmi', 'V-AUTO-02', 'MA-0105', 30, 150000),
    ('sri_lakshmi', 'rudra', Status.ACTIVE, 'sri_lakshmi', 'V-POLY-01', '', 15, None),
    ('tejas', 'kaveri', Status.INVITED, 'tejas', '', '', None, None),
    ('malabar', 'sri_lakshmi', Status.PENDING_APPROVAL, 'sri_lakshmi', '', '', None, None),
    ('tejas', 'malabar', Status.BLOCKED, 'tejas', 'V-0019', '', 30, None),
]


def demo_email(local):
    return f"{local}@{DEMO_DOMAIN}"


class Command(BaseCommand):
    help = "Seeds ERP demo organizations, people, teams, partners and subscriptions."

    def add_arguments(self, parser):
        parser.add_argument('--flush', action='store_true', help="Remove previously seeded ERP demo data first.")
        parser.add_argument('--force', action='store_true', help="Run even with DEBUG off.")

    def handle(self, *args, flush=False, force=False, **options):
        if not settings.DEBUG and not force:
            raise CommandError("DEBUG is off. This creates accounts with a published password; pass --force if you mean it.")
        with transaction.atomic():
            if flush:
                self._flush()
            if User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}", memberships__isnull=False).exists():
                raise CommandError("ERP demo data already exists. Re-run with --flush to reset it.")
            taken = [g for g, *_ in ORGANIZATIONS.values()] + [UNCLAIMED_GSTIN]
            clash = Company.objects.filter(gstin__in=taken).values_list('gstin', flat=True)
            if clash:
                raise CommandError(f"These sample GSTINs are already registered: {', '.join(clash)}. "
                                   "Remove those companies or run with --flush.")
            self._seed()
        self._report()

    # --- flush -----------------------------------------------------------------

    def _flush(self):
        gstins = [g for g, *_ in ORGANIZATIONS.values()] + [UNCLAIMED_GSTIN]
        companies = Company.objects.filter(gstin__in=gstins)
        # QC lots, production entries and BOMs PROTECT items and warehouses.
        InspectionLot.objects.filter(company__in=companies).delete()
        ProductionEntry.objects.filter(company__in=companies).delete()
        BillOfMaterials.objects.filter(company__in=companies).delete()
        # Notes, payments, sales and purchase documents PROTECT the companies, documents, items and lines they point to.
        CreditDebitNote.objects.filter(Q(company__in=companies) | Q(party__in=companies)).delete()
        Payment.objects.filter(Q(company__in=companies) | Q(party__in=companies)).delete()
        SalesInvoice.objects.filter(Q(company__in=companies) | Q(customer__in=companies)).delete()
        Dispatch.objects.filter(Q(company__in=companies) | Q(order__customer__in=companies)).delete()
        SalesOrder.objects.filter(Q(company__in=companies) | Q(customer__in=companies)).delete()
        either_side = Q(company__in=companies) | Q(supplier__in=companies)
        PurchaseBill.objects.filter(either_side).delete()
        GoodsReceipt.objects.filter(Q(company__in=companies) | Q(order__supplier__in=companies)).delete()
        PurchaseOrder.objects.filter(either_side).delete()
        StockEntry.objects.filter(company__in=companies).delete()  # PROTECTs items and warehouses
        Batch.objects.filter(company__in=companies).delete()  # PROTECTs items
        BusinessRelationship.objects.filter(buyer__in=companies).delete()
        BusinessRelationship.objects.filter(supplier__in=companies).delete()
        Membership.objects.filter(company__in=companies).delete()  # Role is PROTECT from Membership
        Invitation.objects.filter(company__in=companies).delete()
        count = companies.count()
        companies.delete()
        # ERP people only: the marketplace seed (seed_demo_data) uses the same domain.
        User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}", role='member', memberships__isnull=True).delete()
        self.stdout.write(f"Removed {count} demo companies and their people.")

    # --- seed ------------------------------------------------------------------

    def _company(self, gstin):
        """A Company built from the mock provider's record, the way sign-up does."""
        verification = GSTVerificationService(provider=MockGSTProvider()).verify(gstin)
        company = Company(name=(verification.trade_name or verification.legal_name)[:255])
        GSTVerificationService.apply_to_company(verification, company)
        return company

    def _user(self, local, first, last):
        user, created = User.objects.get_or_create(email=demo_email(local), defaults={
            'username': demo_email(local), 'first_name': first, 'last_name': last,
            'role': 'member', 'email_verified': True,
        })
        if created:
            user.set_password(PASSWORD)
            user.save(update_fields=['password'])
        return user

    def _seed(self):
        now = timezone.now()
        self.orgs = {}
        users = {}
        for key, (gstin, buying, selling, plan, state) in ORGANIZATIONS.items():
            company = self._company(gstin)
            people = PEOPLE[key]
            local, first, last, _ = people[0]
            owner = self._user(local, first, last)
            set_up_organization(company, owner, buying=buying, selling=selling, plan=plan)
            roles = {r.key: r for r in company.roles.all()}
            users[key] = {local: owner}
            for local, first, last, role_key in people[1:]:
                user = self._user(local, first, last)
                Membership.objects.create(user=user, company=company, role=roles[role_key], invited_by=owner)
                users[key][local] = user
            for name, head, members in TEAMS.get(key, []):
                team = Team.objects.create(company=company, name=name, head=users[key][head])
                for member in members:
                    TeamMembership.objects.create(team=team, user=users[key][member])
            for kind, label, line1, city, pincode in ADDRESSES.get(key, []):
                Address.objects.create(company=company, kind=kind, label=label, line1=line1, city=city,
                                       state=company.state, state_code=company.state_code, pincode=pincode)
            if key in BANKS:
                name, bank, number, ifsc, branch = BANKS[key]
                BankAccount.objects.create(company=company, account_name=name, bank_name=bank, account_number=number,
                                           ifsc=ifsc, branch=branch, is_default=True)
            self._inventory(key, company, owner, now)
            if key in BRANDING:
                self._branding(company, *BRANDING[key])
            self._production(key, company, users[key], now)
            self._subscription(company, plan, state, now)
            self.orgs[key] = company

        self.orgs['rudra'] = self._company(UNCLAIMED_GSTIN)

        # An open invitation, waiting to be accepted.
        sri = self.orgs['sri_lakshmi']
        _raw, token_hash = marketplace_team.new_invitation_token()
        invitation = Invitation.objects.create(
            company=sri, email=demo_email('deepa'), first_name='Deepa', last_name='Sundar',
            role=sri.roles.get(key='sales_executive'), token_hash=token_hash, invited_by=users['sri_lakshmi']['arjun'],
            expires_at=now + timedelta(days=7),
        )
        invitation.teams.set(sri.teams.filter(name='Sales'))

        for buyer, supplier, status, initiator, vendor_code, customer_code, days, credit in RELATIONSHIPS:
            admin = PEOPLE[initiator][0][0]
            BusinessRelationship.objects.create(
                buyer=self.orgs[buyer], supplier=self.orgs[supplier], status=status,
                initiated_by=self.orgs[initiator], created_by=users[initiator][admin],
                vendor_code=vendor_code, customer_code=customer_code, payment_terms_days=days,
                credit_limit=Decimal(credit) if credit else None,
                activated_at=now - timedelta(days=20) if status in (Status.ACTIVE, Status.BLOCKED) else None,
                blocked_by=self.orgs[initiator] if status == Status.BLOCKED else None,
            )

        self._purchases(users, now)

    def _purchases(self, users, now):
        """Purchase documents in each state, made through purchase.services
        by the people whose roles would make them."""
        sri, tejas = self.orgs['sri_lakshmi'], self.orgs['tejas']

        def member(key, local):
            return Membership.objects.select_related('company', 'role', 'user').get(company=self.orgs[key], user=users[key][local])

        def item(company, code):
            return Item.objects.get(company=company, code=code).pk

        def ago(days):
            return (now - timedelta(days=days)).date()

        plant = Warehouse.objects.get(company=sri, code='PS')
        priya, karthik = member('sri_lakshmi', 'priya'), member('sri_lakshmi', 'karthik')

        # Issued, partly received and billed: castings from Kaveri.
        castings = purchase.save_order(priya, PurchaseOrder(), {
            'supplier_id': self.orgs['kaveri'].pk, 'ship_to_id': plant.pk, 'order_date': ago(20), 'expected_date': ago(5),
            'supplier_reference': 'KC/Q/2627/088', 'notes': 'Castings to drawing VB-50 rev B. Test certificate with each lot.',
            'lines': [{'item_id': item(sri, 'BO-CAST-VB50'), 'quantity': '400', 'rate': '980'}],
        })
        castings = purchase.submit_order(priya, castings)  # a Purchase Manager issues directly

        # Kaveri accepts it on MakeSetu, sends 250 and invoices them.
        kaveri = self.orgs['kaveri']
        sneha = member('kaveri', 'sneha')  # Sales Manager: may accept on Growth
        sales_order = sales.accept(sneha, castings, {
            'dispatch_from_id': Warehouse.objects.get(company=kaveri, code='FS').pk,
            'lines': [{'purchase_order_line_id': castings.lines.get().pk, 'item_id': item(kaveri, 'FG-VB-50')}],
        })
        # Accepting dates the sales order today; this demo happened three weeks ago.
        SalesOrder.objects.filter(pk=sales_order.pk).update(order_date=ago(19))
        sales_order.refresh_from_db()
        challan = sales.post_dispatch(member('kaveri', 'nikhil'), sales_order, {
            'dispatch_date': ago(13), 'vehicle_number': 'MH 14 GU 4410', 'transporter': 'Deccan Roadlines',
            'lines': [{'order_line_id': sales_order.lines.get().pk, 'quantity': '250'}],
        })
        invoice = sales.save_invoice(sneha, SalesInvoice(), sales_order, {
            'invoice_date': ago(13), 'lines': [{'order_line_id': sales_order.lines.get().pk, 'quantity': '250'}],
        })
        invoice = sales.post_invoice(sneha, invoice)

        # Sri Lakshmi receives them against Kaveri's challan (castings need inspection, so they
        # wait under QC), passes 240 and rejects 10, and enters Kaveri's invoice as a bill.
        Item.objects.filter(company=sri, code='BO-CAST-VB50').update(inspection_required=True)
        receipt = purchase.post_receipt(member('sri_lakshmi', 'suresh'), castings, {
            'received_date': ago(12), 'supplier_challan': challan.number, 'vehicle_number': challan.vehicle_number,
            'lines': [{'order_line_id': castings.lines.get().pk, 'quantity': '250',
                       'batches': [{'number': 'VB-2610', 'supplier_batch': 'VB-2610'}]}],
        })
        lot = InspectionLot.objects.get(receipt_line__receipt=receipt)
        quality.inspect(member('sri_lakshmi', 'vignesh'), lot, {
            'accepted': Decimal('240'), 'rejected': Decimal('10'), 'inspected_on': ago(11),
            'remarks': 'Blow holes on 10 castings (radiography); 240 within drawing tolerances.',
        })
        accounts = member('sri_lakshmi', 'lakshmi.accounts')
        bill = purchase.save_bill(accounts, PurchaseBill(), castings, {
            'supplier_invoice_number': invoice.number, 'invoice_date': invoice.invoice_date,
            'lines': [{'order_line_id': castings.lines.get().pk, 'quantity': '250'}],
        })
        bill = purchase.submit_bill(accounts, bill)

        # Sri Lakshmi pays part of it by NEFT; Kaveri records the same money as received.
        utr, part = 'HDFCN52026112600741', Decimal('150000')
        bank = sri.bank_accounts.first()
        finance.record_payment(accounts, {
            'direction': 'made', 'party_id': kaveri.pk, 'amount': part, 'payment_date': ago(4), 'mode': 'bank',
            'reference': utr, 'bank_account_id': bank.pk if bank else None,
            'allocations': [{'document_id': bill.pk, 'amount': part}],
        })
        finance.record_payment(member('kaveri', 'pooja'), {
            'direction': 'received', 'party_id': sri.pk, 'amount': part, 'payment_date': ago(4), 'mode': 'bank',
            'reference': utr, 'bank_account_id': kaveri.bank_accounts.first().pk,
            'allocations': [{'document_id': invoice.pk, 'amount': part}],
        })

        # 10 castings fail Sri Lakshmi's inspection and go back. Kaveri issues a credit note
        # (stock back into its Foundry store); Sri Lakshmi records it against the bill
        # (stock out of its Plant store).
        credit_note = adjustments.save_note(sneha, CreditDebitNote(), {
            'side': 'sales', 'kind': 'credit', 'document_id': invoice.pk, 'reason': 'goods_returned', 'note_date': ago(6),
            'warehouse_id': Warehouse.objects.get(company=kaveri, code='FS').pk,
            'remarks': 'Blow holes found at incoming inspection; 10 castings returned.',
            'lines': [{'line_id': invoice.lines.get().pk, 'quantity': '10'}],
        })
        credit_note = adjustments.post_note(sneha, credit_note)
        supplier_note = adjustments.save_note(accounts, CreditDebitNote(), {
            'side': 'purchase', 'kind': 'credit', 'document_id': bill.pk, 'reason': 'goods_returned', 'note_date': ago(5),
            'party_note_number': credit_note.number,
            'warehouse_id': Warehouse.objects.get(company=sri, kind=Warehouse.Kind.REJECTED).pk,
            'remarks': 'Rejected at incoming inspection (blow holes).',
            'lines': [{'line_id': bill.lines.get().pk, 'quantity': '10'}],
        })
        adjustments.post_note(accounts, supplier_note)

        # Waiting for approval: an executive's order to Malabar (Pro has approvals).
        bearings = purchase.save_order(karthik, PurchaseOrder(), {
            'supplier_id': self.orgs['malabar'].pk, 'ship_to_id': Warehouse.objects.get(company=sri, code='AMB').pk,
            'order_date': ago(2), 'lines': [{'item_id': item(sri, 'BO-BRG-6205'), 'quantity': '200', 'rate': '145'}],
        })
        purchase.submit_order(karthik, bearings)

        # A draft to the supplier that isn't on MakeSetu yet.
        purchase.save_order(priya, PurchaseOrder(), {
            'supplier_id': self.orgs['rudra'].pk, 'ship_to_id': plant.pk, 'order_date': ago(1),
            'lines': [{'item_id': item(sri, 'CO-OIL-CUT'), 'quantity': '100'}],
        })

        # Tejas orders enclosures from Sri Lakshmi: Sri Lakshmi sees it under Orders received.
        kavya = member('tejas', 'kavya')
        enclosures = purchase.save_order(kavya, PurchaseOrder(), {
            'supplier_id': sri.pk, 'ship_to_id': Warehouse.objects.get(company=tejas, code='RS').pk,
            'order_date': ago(6), 'expected_date': (now + timedelta(days=9)).date(),
            'lines': [{'item_id': item(tejas, 'BO-ENC-AL'), 'quantity': '50', 'rate': '640'}],
        })
        purchase.submit_order(kavya, enclosures)

    @staticmethod
    def _inventory(key, company, owner, now):
        """Warehouses, items and opening stock, through inventory.services
        so the same rules apply as in the app."""
        warehouses = {}
        for name, code, address_label in WAREHOUSES.get(key, []):
            address = company.addresses.filter(label=address_label).first() if address_label else None
            warehouses[code] = inventory.save_warehouse(company, Warehouse(), {
                'name': name, 'code': code, 'address_id': address.pk if address else None,
            })
        as_of = (now - timedelta(days=30)).date()
        for code, name, kind, unit, hsn, gst, buy, sell, reorder, opening in ITEMS.get(key, []):
            tracking, shelf_life, batch_number = TRACKING.get(key, {}).get(code, ('none', None, ''))
            item = inventory.save_item(company, owner, Item(), {
                'code': code, 'name': name, 'kind': kind, 'unit': unit, 'hsn_code': hsn, 'gst_rate': Decimal(gst),
                'purchase_price': Decimal(buy) if buy else None, 'sale_price': Decimal(sell) if sell else None,
                'reorder_level': Decimal(reorder) if reorder else None, 'tracking': tracking, 'shelf_life_days': shelf_life,
            })
            for warehouse_code, (quantity, rate) in opening.items():
                inventory.set_opening_stock(company, owner, item_id=item.pk, warehouse_id=warehouses[warehouse_code].pk,
                                            quantity=Decimal(quantity), rate=Decimal(rate), entry_date=as_of,
                                            batch_number=batch_number or '', manufactured_on=as_of if shelf_life else None)

    @staticmethod
    def _production(key, company, people, now):
        """BOMs set up by the Org Admin; a shift's production recorded by a Machine Operator."""
        admin = Membership.objects.select_related('company', 'role', 'user').get(company=company, user=people[PEOPLE[key][0][0]])
        routing = {}
        if key in COSTING:
            labour, overhead, machines, scrap_items, routing = COSTING[key]
            costing.save_settings(company, {'labour_hour_rate': Decimal(labour), 'overhead_percent': Decimal(overhead)})
            for code, name, rate in machines:
                costing.save_machine(company, Machine(), {'code': code, 'name': name, 'hour_rate': Decimal(rate)})
            for code, name, unit, hsn, price in scrap_items:
                inventory.save_item(company, admin.user, Item(), {'code': code, 'name': name, 'kind': 'scrap', 'unit': unit,
                                                                  'hsn_code': hsn, 'gst_rate': Decimal('18'), 'sale_price': Decimal(price)})
        boms = {}
        for code, name, makes, parts in BOMS.get(key, []):
            item = Item.objects.get(company=company, code=code)
            route = {}
            if code in routing:
                machine, machine_hours, labour_hours, scrap, scrap_quantity = routing[code]
                route = {'machine_id': Machine.objects.get(company=company, code=machine).pk, 'machine_hours': Decimal(machine_hours),
                         'labour_hours': Decimal(labour_hours), 'scrap_quantity': Decimal(scrap_quantity),
                         'scrap_item_id': Item.objects.get(company=company, code=scrap).pk if scrap else None}
            boms[code] = production.save_bom(admin, BillOfMaterials(), {
                'item_id': item.pk, 'name': name, 'output_quantity': Decimal(makes),
                'components': [{'item_id': Item.objects.get(company=company, code=c).pk, 'quantity': Decimal(q)} for c, q in parts],
                **route,
            })
        for operator, code, good, rejected, src, dst, days, shift in PRODUCTION.get(key, []):
            member = Membership.objects.select_related('company', 'role', 'user').get(company=company, user=people[operator])
            production.record(member, {
                'bom_id': boms[code].pk, 'quantity_produced': Decimal(good), 'quantity_rejected': Decimal(rejected),
                'entry_date': (now - timedelta(days=days)).date(), 'shift': shift,
                'source_warehouse_id': Warehouse.objects.get(company=company, code=src).pk,
                'target_warehouse_id': Warehouse.objects.get(company=company, code=dst).pk,
            })

    @staticmethod
    def _branding(company, initials, colour, signatory, terms):
        """A generated logo (initials on a coloured tile), the signatory and invoice terms."""
        import io

        from django.core.files.base import ContentFile
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new('RGB', (360, 120), colour)
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.load_default(size=64)
        except TypeError:  # older Pillow: fixed-size default font
            font = ImageFont.load_default()
        draw.text((180, 60), initials, fill=(255, 255, 255), font=font, anchor='mm')
        data = io.BytesIO()
        image.save(data, format='PNG')
        if company.logo:
            company.logo.delete(save=False)
        company.logo.save(f"{company.pk}-logo.png", ContentFile(data.getvalue()), save=False)
        company.signatory_name, company.invoice_terms = signatory, terms
        company.tan = TANS.get(company.gstin, '')
        company.save(update_fields=['logo', 'signatory_name', 'invoice_terms', 'tan', 'updated_at'])

    @staticmethod
    def _subscription(company, plan, state, now):
        """set_up_organization started a trial; move it to `state`."""
        sub = company.erp_subscription
        price = Decimal(erp.PLANS[plan]['monthly'])
        if state == Sub.ACTIVE:
            sub.trial_ends_at = now - timedelta(days=20)
            sub.current_period_start = now - timedelta(days=10)
            sub.expires_at = now + timedelta(days=20)
            sub.plan_price = sub.price = price
            sub.auto_renew = True
        elif state == Sub.PAST_DUE:
            sub.trial_ends_at = now - timedelta(days=47)
            sub.current_period_start = now - timedelta(days=33)
            sub.expires_at = now - timedelta(days=3)
            sub.plan_price = sub.price = price
            sub.auto_renew = True
            sub.renewal_attempts = 1
            sub.grace_until = sub.expires_at + grace_period()
            sub.next_retry_at = now + timedelta(days=1)
        elif state == Sub.LAPSED:
            sub.trial_ends_at = now - timedelta(days=2)
        sub.status = state
        sub.save()

    # --- report ----------------------------------------------------------------

    def _report(self):
        out = self.stdout.write
        out(self.style.SUCCESS("\nERP demo data seeded."))
        out(f"Password for every demo user: {PASSWORD}\n")
        for key, (gstin, buying, selling, plan, state) in ORGANIZATIONS.items():
            sides = ' + '.join(s for s, on in (('buys', buying), ('sells', selling)) if on)
            out(f"{self.orgs[key].name}  ({gstin}, {erp.PLANS[plan]['label']}, {state}, {sides})")
            for local, first, last, role_key in PEOPLE[key]:
                out(f"    {demo_email(local):<42} {role_key}")
        out(f"\nOff-network supplier (claim it by registering its GSTIN): {self.orgs['rudra'].name}  {UNCLAIMED_GSTIN}")
        used = {g for g, *_ in ORGANIZATIONS.values()} | {UNCLAIMED_GSTIN}
        out("\nSample GSTINs still free for registering a new company:")
        for gstin, (legal_name, _trade, _c, _street, city, _pin) in SAMPLE_COMPANIES.items():
            if gstin not in used and not Company.objects.filter(gstin=gstin).exists():
                out(f"    {gstin}  {legal_name.title()}, {city}")
