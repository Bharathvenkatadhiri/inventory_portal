from django.contrib import admin
from .models import Requirement, RequirementPart, Quote, Order, OrderEvent, MessageThread, Message, NotificationRead

admin.site.register(Requirement)
admin.site.register(RequirementPart)
admin.site.register(Quote)
admin.site.register(Order)
admin.site.register(OrderEvent)
admin.site.register(MessageThread)
admin.site.register(Message)
admin.site.register(NotificationRead)
