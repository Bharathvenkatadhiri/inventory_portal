from django.contrib import admin

from .models import PortalFeedback


@admin.register(PortalFeedback)
class PortalFeedbackAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'rating', 'allow_public', 'is_featured', 'is_hidden', 'updated_at')
    list_filter = ('role', 'rating', 'allow_public', 'is_featured', 'is_hidden')
    search_fields = ('user__email', 'comment', 'improvement_note')
