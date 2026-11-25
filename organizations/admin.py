from django.contrib import admin

from .models import Address, BankAccount, BusinessRelationship, Membership, Permission, Role, Team, TeamMembership


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ('code', 'label', 'module')
    list_filter = ('module',)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'is_system')
    list_filter = ('is_system',)
    search_fields = ('name', 'company__legal_name', 'company__gstin')
    filter_horizontal = ('permissions',)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'company', 'role', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('user__email', 'company__legal_name', 'company__gstin')
    raw_id_fields = ('user', 'company', 'role', 'invited_by')


@admin.register(BusinessRelationship)
class BusinessRelationshipAdmin(admin.ModelAdmin):
    list_display = ('buyer', 'supplier', 'status', 'created_at')
    list_filter = ('status',)
    raw_id_fields = ('buyer', 'supplier', 'initiated_by', 'created_by')


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    raw_id_fields = ('user',)
    extra = 0


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'head')
    search_fields = ('name', 'company__legal_name')
    raw_id_fields = ('company', 'head')
    inlines = [TeamMembershipInline]


admin.site.register(Address)
admin.site.register(BankAccount)
