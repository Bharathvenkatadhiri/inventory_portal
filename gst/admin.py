from django.contrib import admin

from .models import GSTVerification


@admin.register(GSTVerification)
class GSTVerificationAdmin(admin.ModelAdmin):
    """Read-only: it's an audit trail."""
    list_display = ('gstin', 'status', 'gst_status', 'legal_name', 'provider', 'company', 'created_at')
    list_filter = ('status', 'provider', 'gst_status')
    search_fields = ('gstin', 'legal_name', 'trade_name')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
