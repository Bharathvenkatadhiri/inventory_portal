"""E-invoices (IRN from the Invoice Registration Portal) and e-way bills,
through a GST Suvidha Provider (einvoice.providers). Changes go through
einvoice.services."""
from django.conf import settings
from django.db import models
from django.db.models import Q


class EInvoice(models.Model):
    """An IRN for a B2B tax invoice or credit/debit note."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Registered'
        CANCELLED = 'cancelled', 'Cancelled'

    class CancelReason(models.TextChoices):
        DUPLICATE = '1', 'Duplicate'
        DATA_ENTRY = '2', 'Data entry mistake'
        ORDER_CANCELLED = '3', 'Order cancelled'
        OTHER = '4', 'Other'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='einvoices')
    sales_invoice = models.ForeignKey('sales.SalesInvoice', on_delete=models.PROTECT, null=True, blank=True, related_name='einvoices')
    note = models.ForeignKey('adjustments.CreditDebitNote', on_delete=models.PROTECT, null=True, blank=True, related_name='einvoices')
    document_type = models.CharField(max_length=3)  # INV, CRN or DBN
    document_number = models.CharField(max_length=16)
    irn = models.CharField(max_length=64)
    ack_number = models.CharField(max_length=20)
    ack_date = models.DateTimeField()
    signed_qr = models.TextField()  # the IRP's signed QR code content (JWT)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    cancel_reason = models.CharField(max_length=1, choices=CancelReason.choices, blank=True)
    cancel_remark = models.CharField(max_length=100, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=20)
    request = models.JSONField(default=dict)   # what was sent (INV-01 schema)
    response = models.JSONField(default=dict)  # what came back, for the audit trail
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ack_date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['sales_invoice'], condition=Q(status='active'), name='one_active_irn_per_invoice'),
            models.UniqueConstraint(fields=['note'], condition=Q(status='active'), name='one_active_irn_per_note'),
            models.UniqueConstraint(fields=['irn'], name='irn_unique'),
        ]

    def __str__(self):
        return self.irn


class EwayBill(models.Model):
    """An e-way bill for goods moving on a delivery challan or a job work challan."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        CANCELLED = 'cancelled', 'Cancelled'

    class Mode(models.TextChoices):
        ROAD = '1', 'Road'
        RAIL = '2', 'Rail'
        AIR = '3', 'Air'
        SHIP = '4', 'Ship'

    class VehicleType(models.TextChoices):
        REGULAR = 'R', 'Regular'
        ODC = 'O', 'Over-dimensional cargo'

    class CancelReason(models.TextChoices):
        DUPLICATE = '1', 'Duplicate'
        ORDER_CANCELLED = '2', 'Order cancelled'
        DATA_ENTRY = '3', 'Data entry mistake'
        OTHER = '4', 'Other'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='eway_bills')
    dispatch = models.ForeignKey('sales.Dispatch', on_delete=models.PROTECT, null=True, blank=True, related_name='eway_bills')
    job_work_challan = models.ForeignKey('jobwork.JobWorkChallan', on_delete=models.PROTECT, null=True, blank=True, related_name='eway_bills')
    number = models.CharField(max_length=12)
    generated_at = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)  # none until Part B (the vehicle) is filled in
    document_type = models.CharField(max_length=3)  # CHL (challan) or INV
    document_number = models.CharField(max_length=30)
    supply_sub_type = models.CharField(max_length=2)  # 1 supply, 4 job work
    value = models.DecimalField(max_digits=16, decimal_places=2)  # goods value with tax
    from_pincode = models.CharField(max_length=6)
    to_pincode = models.CharField(max_length=6)
    distance_km = models.PositiveIntegerField()
    transport_mode = models.CharField(max_length=1, choices=Mode.choices, default=Mode.ROAD)
    vehicle_type = models.CharField(max_length=1, choices=VehicleType.choices, default=VehicleType.REGULAR)
    vehicle_number = models.CharField(max_length=15, blank=True)
    transport_document = models.CharField(max_length=15, blank=True)  # RR, airway bill or bill of lading no.
    transport_document_date = models.DateField(null=True, blank=True)
    transporter_id = models.CharField(max_length=15, blank=True)  # transporter's GSTIN or TRANSIN
    transporter_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    cancel_reason = models.CharField(max_length=1, choices=CancelReason.choices, blank=True)
    cancel_remark = models.CharField(max_length=100, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=20)
    request = models.JSONField(default=dict)
    response = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-generated_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['dispatch'], condition=Q(status='active'), name='one_active_ewb_per_dispatch'),
            models.UniqueConstraint(fields=['job_work_challan'], condition=Q(status='active'), name='one_active_ewb_per_jw_challan'),
            models.UniqueConstraint(fields=['number'], name='ewb_number_unique'),
        ]

    def __str__(self):
        return self.number


class EwayBillVehicle(models.Model):
    """Part B: the vehicle carrying the goods, and every change to it on the way."""

    class Reason(models.TextChoices):
        FIRST = '0', 'First entry'
        BREAKDOWN = '1', 'Breakdown'
        TRANSSHIPMENT = '2', 'Transshipment'
        OTHER = '3', 'Other'

    eway_bill = models.ForeignKey(EwayBill, on_delete=models.CASCADE, related_name='vehicles')
    vehicle_number = models.CharField(max_length=15)
    from_place = models.CharField(max_length=50, blank=True)
    reason = models.CharField(max_length=1, choices=Reason.choices, default=Reason.FIRST)
    remark = models.CharField(max_length=50, blank=True)
    updated_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        ordering = ['eway_bill', 'updated_at', 'id']
