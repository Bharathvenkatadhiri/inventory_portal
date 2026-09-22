from django.contrib import admin
from .models import Requirement, RequirementPart, Quote, Order, OrderStatusHistory

admin.site.register(Requirement)
admin.site.register(RequirementPart)
admin.site.register(Quote)
admin.site.register(Order)
admin.site.register(OrderStatusHistory)
