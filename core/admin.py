from django.contrib import admin
from .models import AuditLogEntry, User

# Register your models here.
admin.site.register(User)


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    """Read-only: an audit trail staff can edit or delete isn't one."""
    list_display = ('created_at', 'actor_email', 'action', 'target_repr', 'ip_address')
    list_filter = ('action', 'target_type')
    search_fields = ('actor_email', 'target_repr', 'target_id', 'detail')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
