from django.contrib import admin

from .models import InspectionLot


@admin.register(InspectionLot)
class InspectionLotAdmin(admin.ModelAdmin):
    """Read-only: lots change only through quality.services, which keeps stock in step."""
    list_display = ('number', 'company', 'item', 'reference', 'quantity', 'accepted_quantity', 'rejected_quantity', 'status')
    list_filter = ('status', 'source')
    search_fields = ('number', 'reference', 'item__code', 'company__name')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
