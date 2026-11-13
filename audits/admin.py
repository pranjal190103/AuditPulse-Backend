from django.contrib import admin

from .models import Audit, AuditModule, Evidence, Finding


class AuditModuleInline(admin.TabularInline):
    model = AuditModule
    extra = 0
    readonly_fields = ("key", "title", "category", "required", "position")


@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("client", "audit_type", "owner", "status", "updated_at")
    list_filter = ("status", "audit_type", "created_at")
    search_fields = ("client", "owner__email", "goal")
    inlines = [AuditModuleInline]


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("label", "audit", "module", "added_by", "created_at")
    search_fields = ("label", "audit__client")


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("title", "audit", "priority", "status", "owner_name")
    list_filter = ("priority", "status")
    search_fields = ("title", "audit__client", "recommendation")
