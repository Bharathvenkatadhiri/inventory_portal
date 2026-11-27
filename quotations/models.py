"""Quotations, both ways.

- A sales quotation (QT-) is our offer to a customer: items, rates and
  terms, valid until a date. Sent, the customer can accept or decline it
  (on MakeSetu they see it under Purchase); accepted, it becomes a sales
  order. A sent quotation can be revised: the next revision is a new draft
  and the old one is kept as superseded.
- A request for quotation (RFQ-) asks several suppliers to quote for what
  we need. Each supplier's quote is entered by the supplier (on MakeSetu)
  or by us (from their email or letter); the quotes are compared line by
  line, and awarding them makes draft purchase orders.

Changes go through quotations.services.
"""
from django.conf import settings
from django.db import models

from purchase.models import MONEY, QUANTITY, TaxTotals

RATE = dict(max_digits=14, decimal_places=2)


class Quotation(TaxTotals):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT = 'sent', 'Sent'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'
        CONVERTED = 'converted', 'Sales order raised'
        SUPERSEDED = 'superseded', 'Revised'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='quotations')
    number = models.CharField(max_length=30)
    revision = models.PositiveSmallIntegerField(default=1)
    previous = models.OneToOneField('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='next_revision')
    customer = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='quotations_received')
    quote_date = models.DateField()
    valid_until = models.DateField()
    # The customer's enquiry it answers, e.g. their RFQ number or "email of 12 Nov".
    enquiry_reference = models.CharField(max_length=100, blank=True)
    dispatch_from = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    delivery_days = models.PositiveIntegerField(null=True, blank=True)
    payment_terms_days = models.PositiveIntegerField(null=True, blank=True)
    terms = models.TextField(blank=True)  # e.g. freight, packing, warranty
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    response_note = models.CharField(max_length=300, blank=True)  # why declined or cancelled
    # What it was before a revision superseded it, restored if that revision is deleted.
    superseded_status = models.CharField(max_length=12, blank=True)
    sales_order = models.OneToOneField('sales.SalesOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='quotation')
    sent_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-quote_date', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number', 'revision'], name='quotation_number_unique')]

    def __str__(self):
        return self.display_number

    @property
    def display_number(self):
        return self.number if self.revision == 1 else f"{self.number}-R{self.revision}"


class QuotationLine(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name='lines')
    position = models.PositiveSmallIntegerField(default=0)
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='+')
    description = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=3)
    rate = models.DecimalField(**RATE)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_value = models.DecimalField(**MONEY)
    tax_amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ['quotation', 'position', 'id']


class Rfq(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT = 'sent', 'Waiting for quotes'
        AWARDED = 'awarded', 'Awarded'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='rfqs')
    number = models.CharField(max_length=30)
    title = models.CharField(max_length=120)
    rfq_date = models.DateField()
    respond_by = models.DateField(null=True, blank=True)
    required_by = models.DateField(null=True, blank=True)
    ship_to = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    cancel_reason = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    awarded_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-rfq_date', '-id']
        verbose_name = 'request for quotation'
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='rfq_number_unique')]

    def __str__(self):
        return self.number


class RfqLine(models.Model):
    rfq = models.ForeignKey(Rfq, on_delete=models.CASCADE, related_name='lines')
    position = models.PositiveSmallIntegerField(default=0)
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='+')
    description = models.CharField(max_length=200, blank=True)  # specification, drawing number...
    quantity = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=3)
    # Awarded to (one supplier per line).
    awarded_to = models.ForeignKey('RfqSupplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='awarded_lines')

    class Meta:
        ordering = ['rfq', 'position', 'id']


class RfqSupplier(models.Model):
    """A supplier asked to quote, and their quote."""

    class Status(models.TextChoices):
        INVITED = 'invited', 'Waiting'
        QUOTED = 'quoted', 'Quoted'
        DECLINED = 'declined', 'Declined to quote'
        AWARDED = 'awarded', 'Awarded'
        NOT_AWARDED = 'not_awarded', 'Not awarded'

    rfq = models.ForeignKey(Rfq, on_delete=models.CASCADE, related_name='suppliers')
    supplier = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='rfqs_received')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.INVITED)
    their_reference = models.CharField(max_length=60, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    delivery_days = models.PositiveIntegerField(null=True, blank=True)
    payment_terms_days = models.PositiveIntegerField(null=True, blank=True)
    remarks = models.CharField(max_length=300, blank=True)
    # Who entered the quote: the supplier on MakeSetu, or the buyer for them.
    entered_by_supplier = models.BooleanField(default=False)
    quoted_at = models.DateTimeField(null=True, blank=True)
    purchase_order = models.ForeignKey('purchase.PurchaseOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['rfq', 'id']
        constraints = [models.UniqueConstraint(fields=['rfq', 'supplier'], name='rfq_supplier_once')]


class RfqQuoteLine(models.Model):
    invite = models.ForeignKey(RfqSupplier, on_delete=models.CASCADE, related_name='quote_lines')
    line = models.ForeignKey(RfqLine, on_delete=models.CASCADE, related_name='quotes')
    rate = models.DecimalField(**RATE)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['invite', 'line'], name='one_quote_per_rfq_line')]
