"""Payments received from customers and made to suppliers, and how each is
allocated to invoices or bills. What's left of a payment after allocation
is an advance on account. Changes go through finance.services."""
from django.conf import settings
from django.db import models
from django.db.models import Q


class Payment(models.Model):
    class Direction(models.TextChoices):
        RECEIVED = 'received', 'Received from customer'
        MADE = 'made', 'Paid to supplier'

    class Mode(models.TextChoices):
        BANK = 'bank', 'Bank transfer (NEFT/RTGS/IMPS)'
        UPI = 'upi', 'UPI'
        CHEQUE = 'cheque', 'Cheque / DD'
        CASH = 'cash', 'Cash'

    class Status(models.TextChoices):
        PENDING_APPROVAL = 'pending_approval', 'Waiting for approval'
        POSTED = 'posted', 'Posted'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='payments')
    direction = models.CharField(max_length=8, choices=Direction.choices)
    party = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='+')
    number = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.POSTED)
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.BANK)
    reference = models.CharField(max_length=60, blank=True)  # UTR, UPI reference or cheque number
    bank_account = models.ForeignKey('organizations.BankAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    notes = models.CharField(max_length=300, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='payment_number_unique')]

    def __str__(self):
        return self.number


class PaymentAllocation(models.Model):
    """Part of a payment set against one sales invoice (money received) or
    one purchase bill (money paid)."""
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='allocations')
    sales_invoice = models.ForeignKey('sales.SalesInvoice', on_delete=models.PROTECT, null=True, blank=True, related_name='allocations')
    purchase_bill = models.ForeignKey('purchase.PurchaseBill', on_delete=models.PROTECT, null=True, blank=True, related_name='allocations')
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(sales_invoice__isnull=False, purchase_bill__isnull=True)
                           | Q(sales_invoice__isnull=True, purchase_bill__isnull=False)),
                name='allocation_to_exactly_one_document',
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name='allocation_amount_positive'),
        ]
