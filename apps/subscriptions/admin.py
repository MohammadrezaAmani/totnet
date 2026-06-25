"""
Admin configuration for subscriptions app
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Subscription,
    SubscriptionConfig,
    SubscriptionNotification,
    SubscriptionPlan,
    SubscriptionRenewal,
    SubscriptionTransfer,
    SubscriptionUsage,
)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand",
        "plan_type",
        "price",
        "currency",
        "duration_value",
        "duration_unit",
        "traffic_limit_gb",
        "max_users",
        "is_active",
        "is_visible",
        "is_featured",
        "display_order",
    )
    list_filter = (
        "plan_type",
        "is_active",
        "is_visible",
        "is_featured",
        "brand",
        "created_at",
    )
    search_fields = ("name", "description", "brand__name")
    list_editable = ("is_active", "is_visible", "is_featured", "display_order")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "subscription_id",
        "brand",
        "user",
        "plan",
        "vpn_provider",
        "vpn_user_email",
        "status",
        "owner",
        "is_gift",
        "starts_at",
        "expires_at",
        "days_remaining",
        "traffic_percentage_used",
        "auto_renewal_enabled",
        "created_at",
    )
    list_filter = (
        "status",
        "is_gift",
        "auto_renewal_enabled",
        "brand",
        "vpn_provider",
        "created_at",
    )
    search_fields = (
        "subscription_id",
        "vpn_user_email",
        "user__username",
        "plan__name",
    )
    readonly_fields = ("subscription_id", "created_at", "updated_at")
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Basic Info",
            {"fields": ("subscription_id", "brand", "user", "plan", "order")},
        ),
        ("VPN Provider", {"fields": ("vpn_provider", "vpn_user_email")}),
        ("Ownership", {"fields": ("owner", "is_gift", "gift_message")}),
        ("Status & Dates", {"fields": ("status", "starts_at", "expires_at")}),
        ("Traffic", {"fields": ("traffic_used_gb", "traffic_limit_gb")}),
        (
            "Configuration",
            {
                "fields": (
                    "subscription_url",
                    "connectix_username",
                    "connectix_password",
                )
            },
        ),
        (
            "Auto Renewal",
            {
                "fields": (
                    "auto_renewal_enabled",
                    "expiry_notification_sent",
                    "traffic_warning_sent",
                )
            },
        ),
        (
            "Statistics",
            {
                "fields": ("last_connection", "total_connections"),
                "classes": ("collapse",),
            },
        ),
    )

    def days_remaining(self, obj):
        return obj.days_remaining

    days_remaining.short_description = "Days Remaining"

    def traffic_percentage_used(self, obj):
        if obj.traffic_limit_gb:
            pct = (float(obj.traffic_used_gb) / obj.traffic_limit_gb) * 100
            color = "green" if pct < 80 else ("orange" if pct < 100 else "red")
            return format_html('<span style="color: {};">{:.1f}%</span>', color, pct)
        return "∞"

    traffic_percentage_used.short_description = "Traffic Used"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SubscriptionUsage)
class SubscriptionUsageAdmin(admin.ModelAdmin):
    list_display = (
        "subscription",
        "date",
        "upload_gb",
        "download_gb",
        "total_gb",
        "connection_count",
        "online_duration_minutes",
    )
    list_filter = ("date", "subscription")
    search_fields = ("subscription__subscription_id",)
    date_hierarchy = "date"

    def upload_gb(self, obj):
        return round(obj.upload_bytes / (1024**3), 2)

    upload_gb.short_description = "Upload (GB)"

    def download_gb(self, obj):
        return round(obj.download_bytes / (1024**3), 2)

    download_gb.short_description = "Download (GB)"

    def total_gb(self, obj):
        return round((obj.upload_bytes + obj.download_bytes) / (1024**3), 2)

    total_gb.short_description = "Total (GB)"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(subscription__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SubscriptionRenewal)
class SubscriptionRenewalAdmin(admin.ModelAdmin):
    list_display = (
        "subscription",
        "renewal_type",
        "old_plan",
        "new_plan",
        "amount_paid",
        "old_expires_at",
        "new_expires_at",
        "created_at",
    )
    list_filter = ("renewal_type", "subscription", "created_at")
    search_fields = (
        "subscription__subscription_id",
        "old_plan__name",
        "new_plan__name",
    )
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(subscription__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SubscriptionNotification)
class SubscriptionNotificationAdmin(admin.ModelAdmin):
    list_filter = ("notification_type", "is_sent", "subscription")
    search_fields = ("subscription__subscription_id", "message")
    date_hierarchy = "scheduled_for"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(subscription__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SubscriptionConfig)
class SubscriptionConfigAdmin(admin.ModelAdmin):
    list_display = (
        "subscription",
        "has_vless",
        "has_vmess",
        "has_trojan",
        "has_subscription_url",
        "created_at",
    )
    search_fields = ("subscription__subscription_id",)

    def has_vless(self, obj):
        return bool(obj.vless_config)

    has_vless.boolean = True

    def has_vmess(self, obj):
        return bool(obj.vmess_config)

    has_vmess.boolean = True

    def has_trojan(self, obj):
        return bool(obj.trojan_config)

    has_trojan.boolean = True

    def has_subscription_url(self, obj):
        return bool(obj.subscription_url)

    has_subscription_url.boolean = True

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(subscription__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(SubscriptionTransfer)
class SubscriptionTransferAdmin(admin.ModelAdmin):
    list_display = (
        "subscription",
        "from_user",
        "to_user",
        "status",
        "reason",
        "approved_by",
        "created_at",
    )
    list_filter = ("status", "subscription", "from_user", "to_user")
    search_fields = (
        "subscription__subscription_id",
        "from_user__username",
        "to_user__username",
    )
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(subscription__brand__in=request.user.admin_brands.all())
        return qs
