from django.contrib import admin

from .models import Item, StockEntry, Warehouse


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'company', 'kind', 'unit', 'hsn_code', 'gst_rate', 'is_active')
    list_filter = ('kind', 'is_active')
    search_fields = ('code', 'name', 'hsn_code', 'company__name')
    raw_id_fields = ('company', 'created_by')


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'code', 'is_default', 'is_active')
    search_fields = ('name', 'company__name')
    raw_id_fields = ('company', 'address')


@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ('entry_date', 'kind', 'item', 'warehouse', 'quantity', 'rate', 'company')
    list_filter = ('kind',)
    search_fields = ('item__code', 'item__name', 'company__name')
    raw_id_fields = ('company', 'item', 'warehouse', 'created_by')
