"""
Admin configuration for broadcasts app
"""

from django.contrib import admin

from .models import (
    AutomationExecution,
    AutomationRule,
    BroadcastAnalytics,
    BroadcastAudience,
    BroadcastCampaign,
    BroadcastMessage,
    BroadcastTemplate,
    NotificationPreference,
    PushNotification,
)


@admin.register(BroadcastCampaign)
class BroadcastCampaignAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand",
        "campaign_type",
        "status",
        "campaign_id",
        "target_audience_count",
        "messages_sent",
        "messages_delivered",
        "scheduled_at",
        "started_at",
        "completed_at",
    )
    list_filter = ("campaign_type", "status", "is_ab_test", "brand")
    search_fields = ("name", "description", "subject", "brand__name")
    readonly_fields = (
        "campaign_id",
        "target_audience_count",
        "messages_sent",
        "messages_delivered",
        "messages_failed",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "scheduled_at"

    fieldsets = (
        (
            "Basic Info",
            {
                "fields": (
                    "name",
                    "description",
                    "brand",
                    "campaign_type",
                    "campaign_id",
                )
            },
        ),
        ("Content", {"fields": ("subject", "message", "image", "video", "document")}),
        ("Targeting", {"fields": ("targeting_rules", "target_audience_count")}),
        (
            "Scheduling",
            {"fields": ("status", "scheduled_at", "started_at", "completed_at")},
        ),
        (
            "A/B Testing",
            {"fields": ("is_ab_test", "ab_test_percentage"), "classes": ("collapse",)},
        ),
        (
            "Settings",
            {
                "fields": (
                    "send_immediately",
                    "priority",
                    "max_retries",
                    "retry_delay_minutes",
                )
            },
        ),
        (
            "Statistics",
            {
                "fields": ("messages_sent", "messages_delivered", "messages_failed"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BroadcastMessage)
class BroadcastMessageAdmin(admin.ModelAdmin):
    list_display = (
        "campaign",
        "recipient",
        "status",
        "variant",
        "platform",
        "sent_at",
        "delivered_at",
        "read_at",
        "clicked",
    )
    list_filter = ("status", "variant", "platform", "campaign")
    search_fields = ("campaign__name", "recipient__username")
    date_hierarchy = "queued_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(campaign__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BroadcastTemplate)
class BroadcastTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand",
        "template_type",
        "usage_count",
        "is_active",
        "last_used",
    )
    list_filter = ("template_type", "is_active", "brand")
    search_fields = ("brand__name", "name", "subject_template", "message_template")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BroadcastAudience)
class BroadcastAudienceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand",
        "user_count",
        "last_calculated",
        "auto_update",
        "created_at",
    )
    list_filter = ("auto_update", "brand")
    search_fields = ("brand__name", "name", "description")
    readonly_fields = ("user_count", "last_calculated", "created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "brand",
        "notification_type",
        "is_enabled",
        "telegram_enabled",
        "email_enabled",
        "quiet_hours_start",
        "quiet_hours_end",
    )
    list_filter = (
        "is_enabled",
        "telegram_enabled",
        "email_enabled",
        "notification_type",
        "brand",
    )
    search_fields = ("user__username", "brand__name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand",
        "trigger_type",
        "delay_minutes",
        "max_sends_per_user",
        "is_active",
        "total_triggered",
        "total_sent",
        "created_at",
    )
    list_filter = ("trigger_type", "is_active", "brand")
    search_fields = ("brand__name", "name", "description")
    readonly_fields = ("total_triggered", "total_sent", "created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(AutomationExecution)
class AutomationExecutionAdmin(admin.ModelAdmin):
    list_display = (
        "rule",
        "user",
        "status",
        "triggered_at",
        "scheduled_for",
        "executed_at",
        "error_message",
    )
    list_filter = ("status", "rule", "scheduled_for")
    search_fields = ("rule__name", "user__username", "error_message")
    date_hierarchy = "scheduled_for"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(rule__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BroadcastAnalytics)
class BroadcastAnalyticsAdmin(admin.ModelAdmin):
    list_display = (
        "campaign",
        "date",
        "messages_sent",
        "messages_delivered",
        "messages_read",
        "messages_clicked",
        "conversions",
        "revenue_generated",
    )
    list_filter = ("campaign", "date")
    search_fields = ("campaign__name",)
    date_hierarchy = "date"
    readonly_fields = ("created_at",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(campaign__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(PushNotification)
class PushNotificationAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "user",
        "title",
        "body",
        "status",
        "platform",
        "scheduled_for",
        "sent_at",
        "delivered_at",
    )
    list_filter = ("status", "platform", "brand")
    search_fields = ("brand__name", "user__username", "title", "body")
    date_hierarchy = "scheduled_for"
