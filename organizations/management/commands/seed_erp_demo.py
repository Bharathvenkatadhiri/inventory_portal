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
from plans import erp

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
