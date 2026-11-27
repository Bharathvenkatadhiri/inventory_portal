"""Purchase orders, goods receipts and purchase bills. The buying
organization is `company`; `supplier` is the other organization (on
MakeSetu or added by GSTIN). Changes go through purchase.services.

Amounts are in rupees before GST unless named otherwise. Tax is CGST + SGST
when the supplier is in the same state as the place of supply, IGST when
not (purchase.services.tax)."""
from django.conf import settings
from django.db import models
from django.db.models import Q

MONEY = dict(max_digits=14, decimal_places=2, default=0)
QUANTITY = dict(max_digits=14, decimal_places=3)


class TaxTotals(models.Model):
    """Totals shared by orders and bills, kept in step with their lines."""
    place_of_supply = models.CharField(max_length=2, blank=True)  # GST state code
    is_interstate = models.BooleanField(default=False)
    taxable_value = models.DecimalField(**MONEY)
    cgst = models.DecimalField(**MONEY)
    sgst = models.DecimalField(**MONEY)
    igst = models.DecimalField(**MONEY)
    round_off = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)

    class Meta:
        abstract = True


class PurchaseOrder(TaxTotals):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING_APPROVAL = 'pending_approval', 'Waiting for approval'
        ISSUED = 'issued', 'Issued'
        PARTIALLY_RECEIVED = 'partially_received', 'Partly received'
        RECEIVED = 'received', 'Received'
        CLOSED = 'closed', 'Closed'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='purchase_orders')
    supplier = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='purchase_orders_received')
    number = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    order_date = models.DateField()
    expected_date = models.DateField(null=True, blank=True)
    ship_to = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='purchase_orders')
    payment_terms_days = models.PositiveSmallIntegerField(null=True, blank=True)
    supplier_reference = models.CharField(max_length=100, blank=True)  # their quotation number, say
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # The supplier's reply, when the supplier is on MakeSetu (sales.services.accept / decline).
    class SupplierResponse(models.TextChoices):
        AWAITING = '', 'Awaiting reply'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'

    supplier_response = models.CharField(max_length=10, choices=SupplierResponse.choices, blank=True, default='')
    supplier_responded_at = models.DateTimeField(null=True, blank=True)
    supplier_responded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    decline_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-order_date', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='purchase_order_number_unique')]

    def __str__(self):
        return self.number


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    position = models.PositiveSmallIntegerField(default=0)
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='purchase_order_lines')
    description = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_value = models.DecimalField(**MONEY)
    tax_amount = models.DecimalField(**MONEY)
    # Kept by purchase.services.post_receipt.
    received_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        ordering = ['order', 'position', 'id']

    def __str__(self):
        return f"{self.order}: {self.quantity} {self.unit} {self.item}"


class GoodsReceipt(models.Model):
    """Goods received against a purchase order (a GRN). Posting it adds the
    goods to the stock ledger at the order's rates."""
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='goods_receipts')
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name='receipts')
    number = models.CharField(max_length=30)
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='goods_receipts')
    received_date = models.DateField()
    supplier_challan = models.CharField(max_length=60, blank=True)  # their delivery challan / invoice number
    vehicle_number = models.CharField(max_length=20, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_date', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='goods_receipt_number_unique')]

    def __str__(self):
        return self.number


class GoodsReceiptLine(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name='lines')
    order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name='receipt_lines')
    quantity = models.DecimalField(**QUANTITY)


class PurchaseBill(TaxTotals):
    """The supplier's invoice, entered against goods received."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING_APPROVAL = 'pending_approval', 'Waiting for approval'
        POSTED = 'posted', 'Posted'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='purchase_bills')
    supplier = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='purchase_bills_received')
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name='bills')
    number = models.CharField(max_length=30)  # our own reference
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    supplier_invoice_number = models.CharField(max_length=30)
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # TDS deducted on this bill, worked out when it's posted (tds.services).
    tds_section = models.CharField(max_length=12, blank=True)
    tds_rate = models.DecimalField(max_digits=5, decimal_places=3, default=0)
    tds_base = models.DecimalField(**MONEY)
    tds_amount = models.DecimalField(**MONEY)
    # Set when posted, for micro and small suppliers (finance.msme): the 45-day due date.
    msme_category = models.CharField(max_length=6, blank=True)
    msme_due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-invoice_date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], name='purchase_bill_number_unique'),
            # A supplier's invoice number is entered once (cancelled bills don't count).
            models.UniqueConstraint(fields=['company', 'supplier', 'supplier_invoice_number'],
                                    condition=~Q(status='cancelled'), name='supplier_invoice_entered_once'),
        ]

    def __str__(self):
        return f"{self.number} ({self.supplier_invoice_number})"


class PurchaseBillLine(models.Model):
    bill = models.ForeignKey(PurchaseBill, on_delete=models.CASCADE, related_name='lines')
    order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name='bill_lines')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='purchase_bill_lines')
    quantity = models.DecimalField(**QUANTITY)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_value = models.DecimalField(**MONEY)
    tax_amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ['bill', 'order_line__position', 'id']
