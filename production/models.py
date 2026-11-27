"""Bills of materials and production entries. Changes go through
production.services."""
from django.conf import settings
from django.db import models
from django.db.models import Q

QUANTITY = dict(max_digits=14, decimal_places=3)


class BillOfMaterials(models.Model):
    """What goes into `output_quantity` units of `item` (single level: the
    components are taken from stock, not made from their own BOMs here)."""
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='boms')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='boms')
    name = models.CharField(max_length=80)  # e.g. "Standard" or "Rev B"
    output_quantity = models.DecimalField(**QUANTITY, default=1)
    notes = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company', 'item__code', '-is_default', 'name']
        verbose_name = 'bill of materials'
        verbose_name_plural = 'bills of materials'
        constraints = [
            models.UniqueConstraint(fields=['item'], condition=Q(is_default=True), name='one_default_bom_per_item'),
            models.UniqueConstraint(fields=['item', 'name'], name='bom_name_unique_per_item'),
        ]

    def __str__(self):
        return f"{self.item.code} · {self.name}"


class BomComponent(models.Model):
    bom = models.ForeignKey(BillOfMaterials, on_delete=models.CASCADE, related_name='components')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='used_in_boms')
    quantity = models.DecimalField(**QUANTITY)  # for the BOM's output_quantity
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['bom', 'position', 'id']
        constraints = [models.UniqueConstraint(fields=['bom', 'item'], name='component_once_per_bom')]


class WorkOrder(models.Model):
    """A planned run: make `quantity` of `item` by `due_date` with `bom`.
    Production entries recorded against it use up materials and add output;
    child orders make the sub-assemblies it needs (multi-level)."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        RELEASED = 'released', 'Released'
        IN_PROGRESS = 'in_progress', 'In progress'
        COMPLETED = 'completed', 'Completed'
        CLOSED = 'closed', 'Closed short'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='work_orders')
    number = models.CharField(max_length=30)
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='work_orders')
    bom = models.ForeignKey(BillOfMaterials, on_delete=models.PROTECT, related_name='work_orders')
    quantity = models.DecimalField(**QUANTITY)
    due_date = models.DateField(null=True, blank=True)
    source_warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    target_warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    parent = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='children')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    notes = models.CharField(max_length=300, blank=True)
    close_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    released_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='work_order_number_unique')]

    def __str__(self):
        return self.number


class ProductionEntry(models.Model):
    """Production recorded against a BOM: components out of stock, good
    units into stock at the cost of what went in."""

    class Status(models.TextChoices):
        POSTED = 'posted', 'Posted'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='production_entries')
    number = models.CharField(max_length=30)
    bom = models.ForeignKey(BillOfMaterials, on_delete=models.PROTECT, related_name='entries')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='production_entries')
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, null=True, blank=True, related_name='entries')
    entry_date = models.DateField()
    quantity_produced = models.DecimalField(**QUANTITY)  # good units
    quantity_rejected = models.DecimalField(**QUANTITY, default=0)
    source_warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    target_warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='+')
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # cost of each good unit
    material_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    shift = models.CharField(max_length=20, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    cancel_reason = models.CharField(max_length=300, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-entry_date', '-id']
        verbose_name_plural = 'production entries'
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='production_entry_number_unique')]

    def __str__(self):
        return self.number


class ProductionConsumption(models.Model):
    entry = models.ForeignKey(ProductionEntry, on_delete=models.CASCADE, related_name='consumption')
    item = models.ForeignKey('inventory.Item', on_delete=models.PROTECT, related_name='+')
    planned_quantity = models.DecimalField(**QUANTITY)  # from the BOM
    quantity = models.DecimalField(**QUANTITY)          # actually used
    rate = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ['entry', 'id']
