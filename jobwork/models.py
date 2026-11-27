"""Job work challans (goods sent) and receipts (goods back). Changes go
through jobwork.services."""
from django.conf import settings
from django.db import models

QUANTITY = dict(max_digits=14, decimal_places=3)
MONEY = dict(max_digits=14, decimal_places=2, default=0)


class JobWorkChallan(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'With job worker'
        PARTLY = 'partly', 'Partly back'
        CLOSED = 'closed', 'All back'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='job_work_challans')
    number = models.CharField(max_length=30)
    job_worker = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='+')
    challan_date = models.DateField()
    process = models.CharField(max_length=120)  # nature of job work, e.g. "Zinc plating"
    expected_return = models.DateField(null=True, blank=True)
    from_warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    vehicle_number = models.CharField(max_length=20, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-challan_date', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='job_work_challan_number_unique')]

    def __str__(self):
        return self.number


class JobWorkChallanLine(models.Model):
    challan = models.ForeignKey(JobWorkChallan, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='+')
    quantity = models.DecimalField(**QUANTITY)
    rate = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # cost when sent
    # Accounted for so far by receipts: came back processed, or lost in the process.
    returned_quantity = models.DecimalField(**QUANTITY, default=0)
    waste_quantity = models.DecimalField(**QUANTITY, default=0)

    class Meta:
        ordering = ['challan', 'id']

    @property
    def pending_quantity(self):
        return self.quantity - self.returned_quantity - self.waste_quantity


class JobWorkReceipt(models.Model):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='job_work_receipts')
    challan = models.ForeignKey(JobWorkChallan, on_delete=models.PROTECT, related_name='receipts')
    number = models.CharField(max_length=30)
    received_date = models.DateField()
    job_worker_challan = models.CharField(max_length=40, blank=True)  # the job worker's own challan number
    into_warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_date', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='job_work_receipt_number_unique')]


class JobWorkReceiptLine(models.Model):
    receipt = models.ForeignKey(JobWorkReceipt, on_delete=models.CASCADE, related_name='lines')
    challan_line = models.ForeignKey(JobWorkChallanLine, on_delete=models.PROTECT, related_name='receipt_lines')
    input_quantity = models.DecimalField(**QUANTITY)   # of what was sent, accounted for as processed
    waste_quantity = models.DecimalField(**QUANTITY, default=0)
    output_item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='+')
    output_quantity = models.DecimalField(**QUANTITY)
    charge_per_unit = models.DecimalField(**MONEY)     # job work charge added to the output's cost
    rate = models.DecimalField(**MONEY)                # the output's cost per unit
