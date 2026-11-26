from django.contrib import admin

from .models import TdsDeposit


@admin.register(TdsDeposit)
class TdsDepositAdmin(admin.ModelAdmin):
    list_display = ('company', 'period', 'section', 'amount', 'deposited_on', 'bsr_code', 'challan_serial')
    list_filter = ('section',)
    search_fields = ('company__name', 'bsr_code', 'challan_serial')
