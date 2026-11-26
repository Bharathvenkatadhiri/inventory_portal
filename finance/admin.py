from django.contrib import admin

from .models import Payment, PaymentAllocation


class AllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    can_delete = False
    readonly_fields = ('sales_invoice', 'purchase_bill', 'amount')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Read-only: payments change only through finance.services."""
    list_display = ('number', 'company', 'direction', 'party', 'payment_date', 'amount', 'mode', 'status')
    list_filter = ('direction', 'status', 'mode')
    search_fields = ('number', 'reference', 'company__name', 'party__name')
    inlines = [AllocationInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
