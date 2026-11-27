"""Inspection lots (what's waiting for QC) and inspections (QC's verdict
on part or all of a lot). Changes go through quality.services."""
from django.conf import settings
from django.db import models

QUANTITY = dict(max_digits=14, decimal_places=3)


class InspectionLot(models.Model):
    class Source(models.TextChoices):
        RECEIPT = 'receipt', 'Goods receipt'
        PRODUCTION = 'production', 'Production'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Waiting for inspection'
        PARTLY = 'partly', 'Partly inspected'
        DONE = 'done', 'Inspected'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='inspection_lots')
    number = models.CharField(max_length=30)
    source = models.CharField(max_length=12, choices=Source.choices)
    reference = models.CharField(max_length=40)  # the GRN or production entry number
    receipt_line = models.ForeignKey('purchase.GoodsReceiptLine', on_delete=models.PROTECT, null=True, blank=True, related_name='inspection_lots')
    production_entry = models.ForeignKey('production.ProductionEntry', on_delete=models.PROTECT, null=True, blank=True, related_name='inspection_lots')
    supplier = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='inspection_lots')
    quantity = models.DecimalField(**QUANTITY)
    accepted_quantity = models.DecimalField(**QUANTITY, default=0)
    rejected_quantity = models.DecimalField(**QUANTITY, default=0)
    rate = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # cost it came in at
    # Where accepted goods go.
    destination = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    received_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['status', '-received_on', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='inspection_lot_number_unique')]

    def __str__(self):
        return self.number

    @property
    def pending_quantity(self):
        return self.quantity - self.accepted_quantity - self.rejected_quantity


class Inspection(models.Model):
    lot = models.ForeignKey(InspectionLot, on_delete=models.CASCADE, related_name='inspections')
    inspected_on = models.DateField()
    accepted = models.DecimalField(**QUANTITY)
    rejected = models.DecimalField(**QUANTITY)
    remarks = models.CharField(max_length=300, blank=True)  # e.g. measured values, defects found
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['lot', 'inspected_on', 'id']
