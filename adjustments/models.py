"""Credit and debit notes. One model for both sides:

- side 'sales': issued by this organization to a customer, against one of
  its posted tax invoices. A GST document with its own number series
  (CN- / DN-), given when posted.
- side 'purchase': issued by a supplier to this organization, recorded
  against the supplier's posted bill with the supplier's own number; ours
  (SN-) is an internal reference.

Each line points at a line of the original invoice or bill. A return has
a quantity (and moves stock); a value-only note (discount, rate change)
has quantity 0 and a taxable value. Changes go through
adjustments.services."""
from django.conf import settings
from django.db import models
from django.db.models import Q

from purchase.models import MONEY, QUANTITY, TaxTotals


class CreditDebitNote(TaxTotals):
    class Side(models.TextChoices):
        SALES = 'sales', 'Issued to customer'
        PURCHASE = 'purchase', 'Received from supplier'

    class Kind(models.TextChoices):
        CREDIT = 'credit', 'Credit note'
        DEBIT = 'debit', 'Debit note'

    class Reason(models.TextChoices):
        GOODS_RETURNED = 'goods_returned', 'Goods returned'
        DISCOUNT = 'discount', 'Discount after sale'
        RATE_CHANGE = 'rate_change', 'Rate correction'
        SHORT_SUPPLY = 'short_supply', 'Billed but not supplied'
        ADDITIONAL_CHARGES = 'additional_charges', 'Additional charges'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        POSTED = 'posted', 'Posted'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='credit_debit_notes')
    side = models.CharField(max_length=8, choices=Side.choices)
    kind = models.CharField(max_length=6, choices=Kind.choices)
    party = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='+')
    sales_invoice = models.ForeignKey('sales.SalesInvoice', on_delete=models.PROTECT, null=True, blank=True, related_name='adjustment_notes')
    purchase_bill = models.ForeignKey('purchase.PurchaseBill', on_delete=models.PROTECT, null=True, blank=True, related_name='adjustment_notes')
    number = models.CharField(max_length=16, blank=True)  # given when posted
    party_note_number = models.CharField(max_length=30, blank=True)  # the supplier's own number (purchase side)
    note_date = models.DateField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    # Where returned goods come into (sales) or go out of (purchase).
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    remarks = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-note_date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], condition=~Q(number=''), name='note_number_unique'),
            models.CheckConstraint(
                condition=(Q(side='sales', sales_invoice__isnull=False, purchase_bill__isnull=True)
                           | Q(side='purchase', sales_invoice__isnull=True, purchase_bill__isnull=False)),
                name='note_against_its_sides_document',
            ),
        ]

    def __str__(self):
        return self.number or f"Draft {self.get_kind_display().lower()} #{self.pk}"

    @property
    def document(self):
        return self.sales_invoice if self.side == self.Side.SALES else self.purchase_bill

    @property
    def is_return(self):
        return self.reason == self.Reason.GOODS_RETURNED

    @property
    def signed_total(self):
        """What it does to the amount owed: + for a debit note, - for a credit note."""
        return self.total if self.kind == self.Kind.DEBIT else -self.total


class CreditDebitNoteLine(models.Model):
    note = models.ForeignKey(CreditDebitNote, on_delete=models.CASCADE, related_name='lines')
    sales_invoice_line = models.ForeignKey('sales.SalesInvoiceLine', on_delete=models.PROTECT, null=True, blank=True, related_name='note_lines')
    purchase_bill_line = models.ForeignKey('purchase.PurchaseBillLine', on_delete=models.PROTECT, null=True, blank=True, related_name='note_lines')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='+')
    hsn_code = models.CharField(max_length=8, blank=True)
    quantity = models.DecimalField(**QUANTITY, default=0)  # returned quantity; 0 for value-only notes
    unit = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_value = models.DecimalField(**MONEY)
    tax_amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ['note', 'id']
