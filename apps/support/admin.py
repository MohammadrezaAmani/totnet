"""
Admin configuration for support app
"""

from django.contrib import admin

from .models import (
    SupportAttachment,
    SupportAutomation,
    SupportCategory,
    SupportEscalation,
    SupportKnowledgeBase,
    SupportMessage,
    SupportMetrics,
    SupportTemplate,
    SupportTicket,
)


@admin.register(SupportCategory)
class SupportCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand",
        "response_time_hours",
        "resolution_time_hours",
        "default_priority",
        "is_active",
        "display_order",
    )
    list_filter = ("default_priority", "is_active", "brand")
    search_fields = ("name", "description", "brand__name")
    list_editable = ("is_active", "display_order")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = (
        "ticket_number",
        "brand",
        "customer",
        "category",
        "subject",
        "status",
        "priority",
        "source",
        "assigned_to",
        "sla_response_due",
        "sla_resolution_due",
        "response_sla_breached",
        "resolution_sla_breached",
        "created_at",
    )
    list_filter = (
        "status",
        "priority",
        "source",
        "brand",
        "is_escalated",
        "created_at",
    )
    search_fields = ("ticket_number", "subject", "description", "customer__username")
    date_hierarchy = "created_at"
    list_editable = ("status", "priority", "assigned_to")

    fieldsets = (
        (
            "Basic Info",
            {
                "fields": (
                    "ticket_number",
                    "brand",
                    "customer",
                    "category",
                    "subject",
                    "description",
                )
            },
        ),
        ("Assignment", {"fields": ("assigned_to", "assigned_at")}),
        ("Status & Priority", {"fields": ("status", "priority", "source")}),
        ("Related Objects", {"fields": ("related_subscription", "related_order")}),
        (
            "SLA Info",
            {
                "fields": (
                    "sla_response_due",
                    "sla_resolution_due",
                    "first_response_at",
                    "resolved_at",
                    "closed_at",
                    "response_sla_breached",
                    "resolution_sla_breached",
                )
            },
        ),
        (
            "Customer Feedback",
            {
                "fields": (
                    "customer_rating",
                    "customer_feedback",
                    "feedback_submitted_at",
                )
            },
        ),
        (
            "Escalation",
            {
                "fields": (
                    "is_escalated",
                    "escalated_to",
                    "escalated_at",
                    "escalation_reason",
                )
            },
        ),
        ("Tags", {"fields": ("tags",), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("ticket", "message_type", "sender", "is_public", "created_at")
    list_filter = ("message_type", "is_public", "ticket", "created_at")
    search_fields = ("ticket__ticket_number", "sender__username", "content")
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(ticket__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SupportAttachment)
class SupportAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        "message",
        "original_filename",
        "file_size_kb",
        "mime_type",
        "is_scanned",
        "created_at",
    )
    list_filter = ("is_scanned", "message")
    search_fields = ("original_filename", "message__ticket__ticket_number")

    def file_size_kb(self, obj):
        return round(obj.file_size / 1024, 2)

    file_size_kb.short_description = "File Size (KB)"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(message__ticket__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SupportTemplate)
class SupportTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "name",
        "template_type",
        "category",
        "usage_count",
        "is_active",
        "requires_approval",
    )
    list_filter = ("template_type", "is_active", "requires_approval", "brand")
    search_fields = ("brand__name", "name", "subject_template", "content_template")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SupportKnowledgeBase)
class SupportKnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "title",
        "category",
        "status",
        "is_featured",
        "view_count",
        "helpful_votes",
        "not_helpful_votes",
        "author",
        "created_at",
    )
    list_filter = ("status", "is_featured", "brand")
    search_fields = ("brand__name", "title", "content", "slug")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SupportMetrics)
class SupportMetricsAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "agent",
        "date",
        "tickets_created",
        "tickets_resolved",
        "avg_first_response_time_minutes",
        "sla_response_compliance_rate",
        "avg_customer_rating",
        "total_ratings",
    )
    list_filter = ("brand", "date")
    search_fields = ("brand__name", "agent__username")
    date_hierarchy = "date"
    readonly_fields = ("created_at",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SupportEscalation)
class SupportEscalationAdmin(admin.ModelAdmin):
    list_display = (
        "ticket",
        "escalation_type",
        "escalated_from",
        "escalated_to",
        "reason",
        "is_resolved",
        "resolved_at",
        "created_at",
    )
    list_filter = ("escalation_type", "is_resolved", "ticket")
    search_fields = ("ticket__ticket_number", "reason", "resolution")
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(ticket__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SupportAutomation)
class SupportAutomationAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "name",
        "trigger_type",
        "action_type",
        "is_active",
        "execution_count",
        "last_executed",
    )
    list_filter = ("trigger_type", "action_type", "is_active", "brand")
    search_fields = ("brand__name", "name", "description")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs
