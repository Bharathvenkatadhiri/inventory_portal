from django.contrib import admin
from .models import ManufacturingSector, ManufacturingTech, ManufacturerProfile, ConsumerProfile

admin.site.register(ManufacturerProfile)
admin.site.register(ConsumerProfile)
admin.site.register(ManufacturingSector)
admin.site.register(ManufacturingTech)
