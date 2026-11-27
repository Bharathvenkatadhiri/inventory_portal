"""Items, warehouses and the stock ledger. Everything belongs to one
organization (accounts.Company); nothing here is shared between companies.
Changes go through inventory.services, which applies the plan limits and
the rules below."""
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

# GST unit quantity codes (UQC): the units GST returns and e-way bills accept.
UNITS = [
    ('NOS', 'Numbers'), ('PCS', 'Pieces'), ('SET', 'Sets'), ('PRS', 'Pairs'), ('DOZ', 'Dozens'),
    ('KGS', 'Kilograms'), ('GMS', 'Grams'), ('TON', 'Tonnes'), ('QTL', 'Quintals'),
    ('MTR', 'Metres'), ('CMS', 'Centimetres'), ('KME', 'Kilometres'),
    ('SQM', 'Square metres'), ('SQF', 'Square feet'), ('CBM', 'Cubic metres'),
    ('LTR', 'Litres'), ('MLT', 'Millilitres'),
    ('BOX', 'Boxes'), ('BAG', 'Bags'), ('BDL', 'Bundles'), ('ROL', 'Rolls'), ('PAC', 'Packs'), ('BTL', 'Bottles'),
    ('CAN', 'Cans'), ('DRM', 'Drums'), ('UNT', 'Units'), ('OTH', 'Others'),
]

# GST rates in percent. Since the September 2025 rate changes most goods are
# at 5% or 18% (40% for a few); 12% and 28% stay for items still billed at
# them. The smaller rates are for precious metals and stones.
GST_RATES = ['0', '0.1', '0.25', '1.5', '3', '5', '12', '18', '28', '40']
GST_RATE_VALUES = {Decimal(r) for r in GST_RATES}

HSN_PATTERN = re.compile(r'^(\d{4}|\d{6}|\d{8})$')
SAC_PATTERN = re.compile(r'^99\d{4}$')


class Item(models.Model):
    """Anything the company buys, makes, stocks or sells."""

    class Kind(models.TextChoices):
        RAW_MATERIAL = 'raw_material', 'Raw material'
        COMPONENT = 'component', 'Bought-out part'
        SEMI_FINISHED = 'semi_finished', 'Semi-finished'
        FINISHED_GOOD = 'finished_good', 'Finished good'
        CONSUMABLE = 'consumable', 'Consumable'
        TOOL = 'tool', 'Tool / spare'
        SCRAP = 'scrap', 'Scrap / by-product'
        SERVICE = 'service', 'Service'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='items')
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.RAW_MATERIAL)
    unit = models.CharField(max_length=3, choices=UNITS, default='NOS')
    # HSN for goods (4, 6 or 8 digits), SAC for services (6 digits, 99xxxx).
    hsn_code = models.CharField('HSN/SAC', max_length=8, blank=True)
    gst_rate = models.DecimalField('GST rate (%)', max_digits=5, decimal_places=2, default=18)
    purchase_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    sale_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    # Alert when stock across warehouses falls to this.
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    description = models.TextField(blank=True)
    # Received or produced quantities wait under inspection until QC passes
    # them (quality app; plans with QC only).
    inspection_required = models.BooleanField(default=False)
    # Batch: every quantity carries a batch or heat number. Serial: every
    # unit has its own number. Plans with batch/serial tracking only
    # (inventory.batches); can't change while there's stock.
    tracking = models.CharField(max_length=6, choices=[('none', 'Not tracked'), ('batch', 'By batch'), ('serial', 'By serial number')],
                                default='none')
    # Expiry is filled in from this when a batch is made, unless given.
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company', 'code']
        constraints = [
            models.UniqueConstraint(Lower('code'), 'company', name='item_code_unique_per_company'),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def tracks_stock(self):
        return self.kind != self.Kind.SERVICE

    @property
    def by_batch(self):
        return self.tracking == 'batch' and self.tracks_stock

    @property
    def by_serial(self):
        return self.tracking == 'serial' and self.tracks_stock

    @property
    def tracked(self):
        return self.by_batch or self.by_serial

    def clean(self):
        errors = {}
        code = (self.hsn_code or '').strip()
        if code:
            if self.kind == self.Kind.SERVICE and not SAC_PATTERN.match(code):
                errors['hsn_code'] = "A service's SAC code is 6 digits starting with 99, e.g. 998898."
            elif self.kind != self.Kind.SERVICE and not HSN_PATTERN.match(code):
                errors['hsn_code'] = "An HSN code is 4, 6 or 8 digits, e.g. 7318 or 73181500."
        try:
            rate_ok = Decimal(str(self.gst_rate)) in GST_RATE_VALUES
        except InvalidOperation:
            rate_ok = False
        if not rate_ok:
            errors['gst_rate'] = f"Choose a GST rate: {', '.join(r + '%' for r in GST_RATES)}."
        for field in ('purchase_price', 'sale_price', 'reorder_level'):
            value = getattr(self, field)
            if value is not None and value < 0:
                errors[field] = "Can't be negative."
        if errors:
            raise ValidationError(errors)


class Warehouse(models.Model):
    """A place stock is kept: a store, godown or the shop floor. How many an
    organization can have is set by its plan (plans.erp 'warehouses').

    Two system locations, made as needed by QC (quality app), hold stock
    that can't be used: goods waiting for inspection, and goods rejected.
    They don't count toward the plan, can't be edited, and can't be chosen
    for dispatch, production or receipts."""

    class Kind(models.TextChoices):
        STORE = 'store', 'Store'
        QC_HOLD = 'qc_hold', 'Under inspection'
        REJECTED = 'rejected', 'Rejected goods'
        JOB_WORK = 'job_work', 'At job workers'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='warehouses')
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.STORE)
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=20, blank=True)
    address = models.ForeignKey('organizations.Address', on_delete=models.SET_NULL, null=True, blank=True, related_name='warehouses')
    # Where goods go when a document doesn't say. Exactly one per company.
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['company', '-is_default', 'name']
        constraints = [
            models.UniqueConstraint(Lower('name'), 'company', name='warehouse_name_unique_per_company'),
            models.UniqueConstraint(fields=['company'], condition=Q(is_default=True), name='one_default_warehouse'),
        ]

    def __str__(self):
        return self.name


class Batch(models.Model):
    """A batch (or heat) of a batch-tracked item, or one unit of a
    serial-tracked item. Its stock is the ledger entries that carry it."""

    class Origin(models.TextChoices):
        OPENING = 'opening', 'Opening stock'
        RECEIPT = 'receipt', 'Goods receipt'
        PRODUCTION = 'production', 'Production'
        JOB_WORK = 'job_work', 'Job work'
        RETURN = 'return', 'Return'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='batches')
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='batches')
    number = models.CharField(max_length=60)
    # The supplier's own batch or heat number, from their test certificate.
    supplier_batch = models.CharField(max_length=60, blank=True)
    manufactured_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    origin = models.CharField(max_length=12, choices=Origin.choices)
    reference = models.CharField(max_length=40, blank=True)  # the document that first brought it in
    supplier = models.ForeignKey('accounts.Company', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['item', 'expires_on', 'created_at', 'id']
        verbose_name_plural = 'batches'
        constraints = [models.UniqueConstraint(Lower('number'), 'item', name='batch_number_unique_per_item')]

    def __str__(self):
        return f"{self.item.code} {self.number}"

    @property
    def label_code(self):
        """What its barcode label encodes."""
        return f"B{self.pk}"


class StockEntry(models.Model):
    """One line of the stock ledger: a quantity of an item into (+) or out
    of (-) a warehouse. Stock on hand is the sum. Entries are never edited
    once later movements depend on them; opening stock can be corrected
    until then."""

    class Kind(models.TextChoices):
        OPENING = 'opening', 'Opening stock'
        RECEIPT = 'receipt', 'Goods receipt'
        QC_ACCEPTED = 'qc_accepted', 'Passed inspection'
        QC_REJECTED = 'qc_rejected', 'Rejected at inspection'
        DISPATCH = 'dispatch', 'Dispatch'
        SALES_RETURN = 'sales_return', 'Returned by customer'
        PURCHASE_RETURN = 'purchase_return', 'Returned to supplier'
        PRODUCTION_ISSUE = 'production_issue', 'Used in production'
        PRODUCTION_RECEIPT = 'production_receipt', 'Produced'
        JOB_WORK_OUT = 'job_work_out', 'Sent for job work'
        JOB_WORK_IN = 'job_work_in', 'Back from job work'
        SCRAP = 'scrap', 'Scrap from production'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='stock_entries')
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='stock_entries')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='stock_entries')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    # Cost per unit, excluding GST (input GST is credit, not cost).
    rate = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    entry_date = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    # The document that moved the stock, e.g. "GRN-2627-0004" (blank for opening stock).
    reference = models.CharField(max_length=40, blank=True)
    # Set for batch- and serial-tracked items.
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, null=True, blank=True, related_name='entries')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-entry_date', '-id']
        verbose_name_plural = 'stock entries'
        indexes = [models.Index(fields=['company', 'item', 'warehouse'])]
        constraints = [
            models.UniqueConstraint(fields=['item', 'warehouse', 'batch'], condition=Q(kind='opening'), nulls_distinct=False,
                                    name='one_opening_entry_per_item_warehouse_batch'),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.quantity} {self.item.unit} {self.item} at {self.warehouse}"

    @property
    def value(self):
        return self.quantity * self.rate
