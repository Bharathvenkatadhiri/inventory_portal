from django.contrib import admin
from .models import Requirement, RequirementPart, Quote, Order, OrderEvent, MessageThread, Message, NotificationRead, SupplierReview, ExchangeRate, OrderDocument

admin.site.register(Requirement)
admin.site.register(RequirementPart)
admin.site.register(Quote)
admin.site.register(Order)
admin.site.register(OrderEvent)
admin.site.register(MessageThread)
admin.site.register(Message)
admin.site.register(NotificationRead)
admin.site.register(SupplierReview)


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('currency', 'inr_per_unit', 'updated_at')


@admin.register(OrderDocument)
class OrderDocumentAdmin(admin.ModelAdmin):
    """Issued documents are a record of what was sent; view them, don't edit them."""
    list_display = ('number', 'kind', 'order', 'currency', 'total', 'issued_at')
    list_filter = ('kind',)
    search_fields = ('number', 'order__billno')
    readonly_fields = [field.name for field in OrderDocument._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
