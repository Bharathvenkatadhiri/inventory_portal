"""Sales orders, dispatches and GST tax invoices. The selling organization
is `company`; `customer` is the buyer. A sales order made by accepting a
customer's purchase order points to it (purchase_order), so both sides
look at the same deal. Changes go through sales.services."""
from django.conf import settings
from django.db import models
from django.db.models import Q

from purchase.models import MONEY, QUANTITY, TaxTotals


class SalesOrder(TaxTotals):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING_APPROVAL = 'pending_approval', 'Waiting for approval'
        CONFIRMED = 'confirmed', 'Confirmed'
        PARTIALLY_DISPATCHED = 'partially_dispatched', 'Partly dispatched'
        DISPATCHED = 'dispatched', 'Dispatched'
        CLOSED = 'closed', 'Closed'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='sales_orders')
    customer = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='sales_orders_placed')
    number = models.CharField(max_length=30)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    order_date = models.DateField()
    delivery_date = models.DateField(null=True, blank=True)
    # The customer's PO this order came from, when they raised it on MakeSetu.
    purchase_order = models.OneToOneField('purchase.PurchaseOrder', on_delete=models.PROTECT, null=True, blank=True,
                                          related_name='sales_order')
    customer_po_number = models.CharField(max_length=40, blank=True)
    dispatch_from = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='sales_orders')
    ship_to = models.TextField(blank=True)  # the customer's delivery address
    payment_terms_days = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-order_date', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='sales_order_number_unique')]

    def __str__(self):
        return self.number


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='lines')
    position = models.PositiveSmallIntegerField(default=0)
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='sales_order_lines')
    # From an accepted PO: the customer's own item and line, so the next
    # order from them can suggest the same item.
    customer_item = models.ForeignKey('inventory.Item', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    purchase_order_line = models.OneToOneField('purchase.PurchaseOrderLine', on_delete=models.PROTECT, null=True, blank=True,
                                               related_name='sales_order_line')
    description = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_value = models.DecimalField(**MONEY)
    tax_amount = models.DecimalField(**MONEY)
    dispatched_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        ordering = ['order', 'position', 'id']


class Dispatch(models.Model):
    """Goods sent against a sales order (a delivery challan). Posting it
    takes them out of stock at average cost."""
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='dispatches')
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='dispatches')
    number = models.CharField(max_length=30)
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='dispatches')
    dispatch_date = models.DateField()
    vehicle_number = models.CharField(max_length=20, blank=True)
    transporter = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-dispatch_date', '-id']
        verbose_name_plural = 'dispatches'
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='dispatch_number_unique')]

    def __str__(self):
        return self.number


class DispatchLine(models.Model):
    dispatch = models.ForeignKey(Dispatch, on_delete=models.CASCADE, related_name='lines')
    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name='dispatch_lines')
    quantity = models.DecimalField(**QUANTITY)


class SalesInvoice(TaxTotals):
    """A GST tax invoice. Its number is given when it's posted, so drafts
    never leave gaps in the series GST requires."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        POSTED = 'posted', 'Posted'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='sales_invoices')
    customer = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='sales_invoices_received')
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='invoices')
    number = models.CharField(max_length=16, blank=True)  # GST allows up to 16 characters
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-invoice_date', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], condition=~Q(number=''),
                                               name='sales_invoice_number_unique')]

    def __str__(self):
        return self.number or f"Draft invoice #{self.pk}"


class SalesInvoiceLine(models.Model):
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name='lines')
    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name='invoice_lines')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='sales_invoice_lines')
    hsn_code = models.CharField(max_length=8, blank=True)  # as on the item when invoiced
    quantity = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_value = models.DecimalField(**MONEY)
    tax_amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ['invoice', 'order_line__position', 'id']
