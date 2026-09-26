from django.contrib import admin
from .models import Requirement, RequirementPart, Quote, Order, OrderEvent, MessageThread, Message, NotificationRead, SupplierReview, ExchangeRate

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
