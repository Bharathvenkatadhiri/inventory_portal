from django.contrib import admin

from .models import GoodsReceipt, PurchaseBill, PurchaseOrder


class ReadOnlyDocumentAdmin(admin.ModelAdmin):
    """Purchase documents change only through the app (purchase.services),
    which keeps stock and totals in step; the admin is for looking."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ReadOnlyDocumentAdmin):
    list_display = ('number', 'company', 'supplier', 'status', 'order_date', 'total')
    list_filter = ('status',)
    search_fields = ('number', 'company__name', 'supplier__name')


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(ReadOnlyDocumentAdmin):
    list_display = ('number', 'company', 'order', 'warehouse', 'received_date')
    search_fields = ('number', 'company__name', 'order__number')


@admin.register(PurchaseBill)
class PurchaseBillAdmin(ReadOnlyDocumentAdmin):
    list_display = ('number', 'company', 'supplier', 'supplier_invoice_number', 'status', 'invoice_date', 'total')
    list_filter = ('status',)
    search_fields = ('number', 'supplier_invoice_number', 'company__name', 'supplier__name')
