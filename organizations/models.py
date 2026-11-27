"""The ERP's platform tables. An organization is accounts.Company: it
registers once, with its GST identity, and switches on the sides it uses
(Company.buying_enabled / selling_enabled). It is never "a buyer" or "a
supplier" as such:

- what a person may do inside an organization is their Role there, held
  through a Membership. One person can belong to several organizations, with
  a different role in each.
- who buys from whom is a BusinessRelationship between two organizations,
  one row per direction, each with its own terms.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .catalog import FULL, LIGHT, LIGHT_PERMISSIONS, USER_TYPE_CHOICES


class Permission(models.Model):
    """One thing a role can allow, e.g. 'po.create'. The rows mirror
    organizations.catalog.PERMISSIONS (synced after every migrate)."""
    code = models.CharField(max_length=60, unique=True)
    label = models.CharField(max_length=120)
    module = models.CharField(max_length=30)

    class Meta:
        ordering = ['module', 'code']

    def __str__(self):
        return self.code


class Role(models.Model):
    """A named set of permissions inside one organization. New organizations
    get the defaults in organizations.catalog.DEFAULT_ROLES (is_system);
    they can edit those or add their own."""
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='roles')
    key = models.SlugField(max_length=40)
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    is_system = models.BooleanField(default=False)
    # Full or light (organizations.catalog). A light role is for shop-floor
    # and support staff and can only hold catalog.LIGHT_PERMISSIONS; plans
    # count full and light users separately.
    user_type = models.CharField(max_length=5, choices=USER_TYPE_CHOICES, default=FULL)
    permissions = models.ManyToManyField(Permission, blank=True, related_name='roles')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['company', 'name']
        constraints = [
            models.UniqueConstraint(fields=['company', 'key'], name='role_key_unique_per_company'),
            models.UniqueConstraint(fields=['company', 'name'], name='role_name_unique_per_company'),
        ]

    def __str__(self):
        return f"{self.name} ({self.company})"

    @property
    def is_light(self):
        return self.user_type == LIGHT

    def save(self, *args, **kwargs):
        # Turning an existing role light: it mustn't keep full-user permissions.
        if self.pk and self.is_light:
            _refuse_full_permissions(self.permissions.values_list('code', flat=True))
        super().save(*args, **kwargs)


def _refuse_full_permissions(codes):
    full_only = sorted(set(codes) - LIGHT_PERMISSIONS)
    if full_only:
        raise ValidationError(f"A light-user role can't have: {', '.join(full_only)}.")


@receiver(m2m_changed, sender=Role.permissions.through)
def _light_roles_keep_light_permissions(sender, instance, action, reverse, pk_set, **kwargs):
    # role.permissions.add()/set() never call Role.save(), so check here too.
    if action != 'pre_add' or not pk_set:
        return
    if not reverse:
        if instance.is_light:
            _refuse_full_permissions(Permission.objects.filter(pk__in=pk_set).values_list('code', flat=True))
    elif instance.code not in LIGHT_PERMISSIONS and Role.objects.filter(pk__in=pk_set, user_type=LIGHT).exists():
        _refuse_full_permissions([instance.code])


class Membership(models.Model):
    """A person's place in an organization, and their role there."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='memberships')
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name='memberships')
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company', 'created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'company'], name='one_membership_per_user_and_company')]

    def __str__(self):
        return f"{self.user} in {self.company} ({self.role.name})"

    def clean(self):
        if self.role_id and self.company_id and self.role.company_id != self.company_id:
            raise ValidationError({'role': "This role belongs to another organization."})

    def save(self, *args, **kwargs):
        # A role from another organization would hand its permissions across
        # companies; refuse it at the model, not only in forms.
        self.clean()
        super().save(*args, **kwargs)


class Team(models.Model):
    """A team or department inside an organization (Purchase, Sales, Store,
    Production, Accounts...), with an optional head. People can be in any
    number of teams; the plan limits how many teams there are (plans.erp),
    while users are one shared pool counted per organization, never per
    team. Teams will route approvals to their head, send alerts to the
    right people and group reports. They never grant or remove
    permissions: what someone may do is only ever their role
    (organizations.access), so being in several teams can't mix them."""
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='teams')
    name = models.CharField(max_length=60)
    head = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through='TeamMembership', related_name='erp_teams')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['company', 'name']
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='team_name_unique_per_company')]

    def __str__(self):
        return f"{self.name} ({self.company})"

    def clean(self):
        if self.head_id and not _is_member(self.head_id, self.company_id):
            raise ValidationError({'head': "The team head must be a member of this organization."})

    def save(self, *args, **kwargs):
        self.clean()
        if self.pk is None:
            from plans import erp

            limit = (erp.PLANS.get(self.company.erp_plan) or {}).get('teams', 0)
            if limit is not None and Team.objects.filter(company_id=self.company_id).count() >= limit:
                raise ValidationError(_team_limit_message(self.company.erp_plan, limit))
        super().save(*args, **kwargs)


class TeamMembership(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='erp_team_memberships')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['team', 'user'], name='one_team_membership_per_user')]

    def __str__(self):
        return f"{self.user} in {self.team}"

    def save(self, *args, **kwargs):
        if not _is_member(self.user_id, self.team.company_id):
            raise ValidationError("Only members of this organization can join its teams.")
        super().save(*args, **kwargs)


def _is_member(user_id, company_id):
    return Membership.objects.filter(user_id=user_id, company_id=company_id).exists()


def _team_limit_message(plan_key, limit):
    from plans import erp

    bigger = next((k for k in erp.PLAN_ORDER[erp.PLAN_ORDER.index(plan_key) + 1:]
                   if erp.PLANS[k]['teams'] is None or erp.PLANS[k]['teams'] > limit), None) if plan_key in erp.PLANS else None
    hint = f" Move to {erp.PLANS[bigger]['label']} for more." if bigger else ""
    return f"Your plan includes {limit} team{'' if limit == 1 else 's'}.{hint}"


class Invitation(models.Model):
    """An emailed invitation for a person to join an organization with a
    role (and, optionally, teams). Only a hash of the link's token is
    stored, like a password reset token. An open invitation holds a seat
    of its role's type until it's accepted, revoked or expires."""
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name='invitations')
    teams = models.ManyToManyField(Team, blank=True, related_name='invitations')
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['company', 'email'])]

    def __str__(self):
        return f"Invitation for {self.email} to {self.company}"

    @property
    def is_open(self):
        from django.utils import timezone
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()


class Address(models.Model):
    class Kind(models.TextChoices):
        REGISTERED = 'registered', 'Registered office'
        BILLING = 'billing', 'Billing'
        SHIPPING = 'shipping', 'Shipping'
        WORKS = 'works', 'Works / factory'
        WAREHOUSE = 'warehouse', 'Warehouse'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='addresses')
    kind = models.CharField(max_length=12, choices=Kind.choices)
    label = models.CharField(max_length=80, blank=True)
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=2, blank=True)   # GST state code, e.g. 33
    pincode = models.CharField(max_length=10, blank=True)
    country = models.CharField(max_length=60, default='India')
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'addresses'
        ordering = ['company', 'kind', '-is_default']

    def __str__(self):
        return f"{self.get_kind_display()}: {self.line1[:40]}"


class BankAccount(models.Model):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='bank_accounts')
    account_name = models.CharField(max_length=150)
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=34)
    ifsc = models.CharField('IFSC', max_length=11)
    branch = models.CharField(max_length=100, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['company', '-is_default', 'bank_name']

    def __str__(self):
        return f"{self.bank_name} ····{self.account_number[-4:]}"


class BusinessRelationship(models.Model):
    """`buyer` buys from `supplier`. Two companies that buy from each other
    have two rows. A purchase order will carry both organizations, so the
    buyer sees it under Purchase and the supplier sees the same row under
    Sales, with nothing copied between them.

    It becomes active once the side that didn't start it accepts (or, for a
    new supplier, once the buyer's vendor approval passes)."""

    class Status(models.TextChoices):
        INVITED = 'invited', 'Invited'
        PENDING_APPROVAL = 'pending_approval', 'Pending approval'
        ACTIVE = 'active', 'Active'
        BLOCKED = 'blocked', 'Blocked'

    buyer = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='supplier_relationships')
    supplier = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='customer_relationships')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INVITED)
    initiated_by = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='+')
    # The buyer's terms with this supplier.
    payment_terms_days = models.PositiveSmallIntegerField(null=True, blank=True)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    vendor_code = models.CharField(max_length=30, blank=True)    # the buyer's code for the supplier
    customer_code = models.CharField(max_length=30, blank=True)  # the supplier's code for the buyer
    # The buyer deducts TDS under this section when it pays the supplier (tds.rules).
    tds_section = models.CharField('TDS section', max_length=12, blank=True)
    # The buyer's record of the supplier's Udyam registration (finance.msme): micro and
    # small manufacturers and service providers must be paid within 45 days.
    msme_category = models.CharField(max_length=6, blank=True, choices=[('micro', 'Micro'), ('small', 'Small'), ('medium', 'Medium')])
    msme_activity = models.CharField(max_length=13, blank=True,
                                     choices=[('manufacturing', 'Manufacturing'), ('services', 'Services'), ('trading', 'Trading')])
    udyam_number = models.CharField(max_length=19, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    activated_at = models.DateTimeField(null=True, blank=True)
    # The side that blocked it; only that side can unblock.
    blocked_by = models.ForeignKey('accounts.Company', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['buyer', 'supplier'], name='one_relationship_per_direction'),
            models.CheckConstraint(condition=~Q(buyer=F('supplier')), name='relationship_between_two_companies'),
            models.CheckConstraint(
                condition=Q(initiated_by=F('buyer')) | Q(initiated_by=F('supplier')),
                name='relationship_initiated_by_a_party',
            ),
        ]

    def __str__(self):
        return f"{self.buyer} buys from {self.supplier} ({self.get_status_display()})"
