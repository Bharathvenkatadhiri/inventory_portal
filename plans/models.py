from django.contrib.contenttypes.models import ContentType
from django.db import models


class StoredFile(models.Model):
    """One file a company uploaded, and its size: what the plan's storage
    limit counts (plans.storage). Kept in step with the uploads by
    plans.signals, so checking usage is a sum, not a call per file to the
    storage backend. Generated documents (POs, invoices) aren't counted."""
    buyer = models.ForeignKey('accounts.ConsumerProfile', on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    supplier = models.ForeignKey('accounts.ManufacturerProfile', on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    field = models.CharField(max_length=50)
    name = models.CharField(max_length=500)
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['content_type', 'object_id', 'field'], name='storedfile_one_per_field'),
        ]
        indexes = [models.Index(fields=['buyer']), models.Index(fields=['supplier'])]

    def __str__(self):
        return f"{self.name} ({self.size} bytes)"


class RFQReceipt(models.Model):
    """An RFQ delivered to a supplier company: shown in its inbox and
    alerts. A supplier plan includes so many each month (plans.catalog's
    rfqs_received_per_month); plans.rfq_inbox hands them out, best match
    first."""
    supplier = models.ForeignKey('accounts.ManufacturerProfile', on_delete=models.CASCADE, related_name='rfq_receipts')
    requirement = models.ForeignKey('marketplace.Requirement', on_delete=models.CASCADE, related_name='receipts')
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['supplier', 'requirement'], name='rfqreceipt_once_per_supplier'),
        ]
        indexes = [models.Index(fields=['supplier', 'received_at'])]

    def __str__(self):
        return f"RFQ-{self.requirement_id} to supplier #{self.supplier_id}"
