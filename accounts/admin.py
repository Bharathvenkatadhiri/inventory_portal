from django.contrib import admin
from .models import ManufacturingSector, ManufacturingTech, Supplier_details
from transactions.models import Customer

admin.site.register(Supplier_details)
admin.site.register(Customer)
admin.site.register(ManufacturingSector)
admin.site.register(ManufacturingTech)