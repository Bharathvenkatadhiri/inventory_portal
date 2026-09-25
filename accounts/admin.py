from django.contrib import admin
from .models import ManufacturingTech, ManufacturerProfile, ConsumerProfile, Company


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('legal_name', 'gstin', 'gst_status', 'verification_status', 'gst_verified_at')
    list_filter = ('verification_status', 'gst_status', 'entity_type')
    search_fields = ('legal_name', 'trade_name', 'gstin', 'cin')
    readonly_fields = ('created_at', 'updated_at', 'gst_verified_at')


admin.site.register(ManufacturerProfile)
admin.site.register(ConsumerProfile)
admin.site.register(ManufacturingTech)
