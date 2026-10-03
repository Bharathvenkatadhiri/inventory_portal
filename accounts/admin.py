from django.contrib import admin
from gst.models import GSTVerification
from .models import ManufacturingTech, ManufacturerProfile, ConsumerProfile, Company

# Written only by gst.services.verification.GSTVerificationService.
GST_FIELDS = (
    'legal_name', 'trade_name', 'gstin', 'gst_verified', 'gst_status', 'gst_registration_date',
    'gst_cancellation_date', 'taxpayer_type', 'business_constitution', 'principal_address', 'city', 'state',
    'state_code', 'pincode', 'gst_verified_at',
)


class GSTVerificationInline(admin.TabularInline):
    model = GSTVerification
    fields = ('created_at', 'status', 'gst_status', 'legal_name', 'provider', 'error_code')
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('legal_name', 'name', 'gstin', 'gst_status', 'gst_verified', 'gst_verified_at')
    list_filter = ('gst_verified', 'gst_status', 'business_constitution')
    search_fields = ('name', 'legal_name', 'trade_name', 'gstin')
    readonly_fields = GST_FIELDS + ('created_at', 'updated_at')
    inlines = [GSTVerificationInline]


admin.site.register(ManufacturerProfile)
admin.site.register(ConsumerProfile)
admin.site.register(ManufacturingTech)
