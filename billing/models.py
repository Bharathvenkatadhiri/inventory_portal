from django.conf import settings
from django.db import models

from plans import erp
from plans.catalog import BILLING_CYCLES, PLAN_LABELS


class Payment(models.Model):
    """One charge for a subscription: a new paid subscription, an upgrade,
    or a renewal (automatic or paid by hand while past due).

    `basis_*` record the subscription state the amount was priced against.
    A payment is only applied while the subscription still matches it, so a
    late, duplicate or out-of-order gateway result can't double-extend a
    period or apply an upgrade priced for a state that no longer exists
    (billing.services.apply_payment)."""
    NEW, UPGRADE, RENEWAL = 'new', 'upgrade', 'renewal'
    KIND_CHOICES = [(NEW, 'New subscription'), (UPGRADE, 'Upgrade'), (RENEWAL, 'Renewal')]
    PENDING, SUCCEEDED, FAILED, CANCELED = 'pending', 'succeeded', 'failed', 'canceled'
    STATUS_CHOICES = [(PENDING, 'Pending'), (SUCCEEDED, 'Paid'), (FAILED, 'Failed'), (CANCELED, 'Cancelled')]
    PLAN_CHOICES = [(key, label) for key, label in PLAN_LABELS.items()]

    subscription = models.ForeignKey('accounts.SubscriptionPlan', on_delete=models.CASCADE, related_name='payments')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    plan_type = models.CharField(max_length=20, choices=PLAN_CHOICES)
    billing_cycle = models.CharField(max_length=10, choices=BILLING_CYCLES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)

    # The period this payment pays for. Upgrades keep the current period.
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)

    # The subscription as it was when this was priced.
    basis_plan_type = models.CharField(max_length=20, blank=True)
    basis_expires_at = models.DateTimeField(null=True, blank=True)
    # How an upgrade's amount was worked out (credit, cost, seconds left).
    proration = models.JSONField(default=dict, blank=True)

    gateway = models.CharField(max_length=20)
    gateway_order_id = models.CharField(max_length=100, unique=True)
    gateway_payment_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(default=1)
    failure_reason = models.CharField(max_length=300, blank=True)
    # Whether the subscription change was carried out; False on a successful
    # payment means it arrived too late to apply (refund it, see services).
    applied = models.BooleanField(default=False)
    needs_refund = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['subscription', 'status'])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.plan_type} ₹{self.amount} ({self.status})"


class WebhookEvent(models.Model):
    """Every gateway event received, by the gateway's own event id, so each
    is processed exactly once however many times it's delivered."""
    gateway = models.CharField(max_length=20)
    event_id = models.CharField(max_length=100)
    event_type = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-received_at']
        constraints = [models.UniqueConstraint(fields=['gateway', 'event_id'], name='webhookevent_once')]

    def __str__(self):
        return f"{self.gateway}:{self.event_id} {self.event_type}"


class OrganizationSubscription(models.Model):
    """An ERP organization's subscription (billing.erp holds the lifecycle).

        Trial ──pay──▶ Active ──expiry, auto-renew──▶ renewal charge
          │              │                               │ fails
          │ ends unpaid  │ cancelled, expires            ▼
          ▼              ▼                           Past due (access until
        Lapsed ◀─────────┴───────────────────────── grace_until, retried)

    Lapsed = read-only until someone pays. The organization's plan
    (accounts.Company.erp_plan) always follows `plan` here."""
    TRIALING, ACTIVE, PAST_DUE, LAPSED = 'trialing', 'active', 'past_due', 'lapsed'
    STATUS_CHOICES = [(TRIALING, 'Free trial'), (ACTIVE, 'Active'), (PAST_DUE, 'Past due'), (LAPSED, 'Lapsed')]

    company = models.OneToOneField('accounts.Company', on_delete=models.CASCADE, related_name='erp_subscription')
    plan = models.CharField(max_length=20, choices=erp.PLAN_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=TRIALING)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    # The paid period. plan_price is the plan's part of `price`; the rest is
    # extra light users, counted when the period was charged.
    current_period_start = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    plan_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    extra_light_users = models.PositiveIntegerField(default=0)
    auto_renew = models.BooleanField(default=False)
    # A downgrade waiting for the end of the period (or the plan a past-due
    # renewal will be paid on).
    scheduled_plan = models.CharField(max_length=20, choices=erp.PLAN_CHOICES, blank=True)
    grace_until = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    renewal_attempts = models.PositiveSmallIntegerField(default=0)
    gateway_mandate_id = models.CharField(max_length=100, blank=True)
    # The trial end / expiry each reminder was last sent for (once each).
    trial_reminder_sent_for = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.company} — {self.get_plan_display()} ({self.get_status_display()})"


class OrganizationPayment(models.Model):
    """One charge for an ERP subscription. Like billing.Payment: applied
    once, and only while the subscription still matches the state it was
    priced for (billing.erp.apply_result)."""
    NEW, UPGRADE, RENEWAL = 'new', 'upgrade', 'renewal'
    KIND_CHOICES = [(NEW, 'New subscription'), (UPGRADE, 'Upgrade'), (RENEWAL, 'Renewal')]
    PENDING, SUCCEEDED, FAILED, CANCELED = 'pending', 'succeeded', 'failed', 'canceled'
    STATUS_CHOICES = [(PENDING, 'Pending'), (SUCCEEDED, 'Paid'), (FAILED, 'Failed'), (CANCELED, 'Cancelled')]

    subscription = models.ForeignKey(OrganizationSubscription, on_delete=models.CASCADE, related_name='payments')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    plan = models.CharField(max_length=20, choices=erp.PLAN_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    plan_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    extra_light_users = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    basis_status = models.CharField(max_length=10, blank=True)
    basis_plan = models.CharField(max_length=20, blank=True)
    basis_expires_at = models.DateTimeField(null=True, blank=True)
    proration = models.JSONField(default=dict, blank=True)
    gateway = models.CharField(max_length=20)
    gateway_order_id = models.CharField(max_length=100, unique=True)
    gateway_payment_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(default=1)
    failure_reason = models.CharField(max_length=300, blank=True)
    applied = models.BooleanField(default=False)
    needs_refund = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['subscription', 'status'])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.plan} ₹{self.amount} ({self.status})"
