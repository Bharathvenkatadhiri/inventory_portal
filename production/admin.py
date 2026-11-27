from django.contrib import admin

from .models import BillOfMaterials, ProductionEntry


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(admin.ModelAdmin):
    list_display = ('item', 'name', 'company', 'output_quantity', 'is_default', 'is_active')
    search_fields = ('item__code', 'item__name', 'company__name')


@admin.register(ProductionEntry)
class ProductionEntryAdmin(admin.ModelAdmin):
    """Read-only: entries change only through production.services, which keeps stock in step."""
    list_display = ('number', 'company', 'item', 'entry_date', 'quantity_produced', 'quantity_rejected', 'status')
    list_filter = ('status',)
    search_fields = ('number', 'item__code', 'company__name')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
