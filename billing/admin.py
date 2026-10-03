from django.contrib import admin

from .models import Payment, WebhookEvent


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'subscription', 'kind', 'plan_type', 'billing_cycle', 'amount', 'status', 'applied', 'needs_refund')
    list_filter = ('status', 'kind', 'needs_refund', 'gateway')
    search_fields = ('gateway_order_id', 'gateway_payment_id', 'subscription__user_profile__email')
    readonly_fields = [f.name for f in Payment._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ('received_at', 'gateway', 'event_type', 'event_id', 'processed_at', 'result')
    list_filter = ('gateway', 'event_type')
    search_fields = ('event_id',)
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False
