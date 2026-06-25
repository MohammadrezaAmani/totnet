"""
Admin configuration for vpn_providers app
"""

from django.contrib import admin

from .models import (
    VPNProvider,
    VPNProviderHealthCheck,
    VPNProviderInbound,
    VPNProviderLog,
    VPNProviderStats,
    VPNServer,
)


@admin.register(VPNProvider)
class VPNProviderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "provider_type",
        "brand",
        "base_url",
        "status",
        "health_status",
        "is_default",
        "priority",
        "max_users",
        "current_users",
        "last_health_check",
    )
    list_filter = (
        "provider_type",
        "status",
        "health_status",
        "is_default",
        "brand",
        "created_at",
    )
    search_fields = ("name", "base_url", "brand__name")
    readonly_fields = (
        "current_users",
        "total_subscriptions",
        "last_health_check",
        "health_status",
        "response_time",
        "created_at",
        "updated_at",
    )
    list_editable = ("priority", "is_default", "status")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(VPNServer)
class VPNServerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "provider",
        "location",
        "country_code",
        "ip_address",
        "port",
        "status",
        "max_users",
        "current_users",
        "cpu_usage",
        "memory_usage",
    )
    list_filter = ("status", "location", "country_code", "provider")
    search_fields = ("name", "ip_address", "domain", "provider__name")
    readonly_fields = ("current_users", "created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(provider__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(VPNProviderLog)
class VPNProviderLogAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "level",
        "action_type",
        "message",
        "duration",
        "error_code",
        "created_at",
    )
    list_filter = ("level", "action_type", "provider", "created_at")
    search_fields = ("provider__name", "message", "error_code", "user_email")
    date_hierarchy = "created_at"
    readonly_fields = ("duration", "created_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(provider__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(VPNProviderStats)
class VPNProviderStatsAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "collected_at",
        "total_users",
        "online_users",
        "total_traffic_gb",
        "cpu_usage",
        "memory_usage",
    )
    list_filter = ("provider", "collected_at")
    search_fields = ("provider__name",)
    date_hierarchy = "collected_at"
    readonly_fields = ("collected_at",)

    def total_traffic_gb(self, obj):
        return round((obj.total_upload + obj.total_download) / (1024**3), 2)

    total_traffic_gb.short_description = "Total Traffic (GB)"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(provider__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(VPNProviderHealthCheck)
class VPNProviderHealthCheckAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "check_time",
        "is_healthy",
        "response_time",
        "api_accessible",
        "backend_running",
        "error_message",
    )
    list_filter = ("is_healthy", "api_accessible", "backend_running", "provider")
    search_fields = ("provider__name", "error_message")
    date_hierarchy = "check_time"
    readonly_fields = ("check_time", "response_time")


@admin.register(VPNProviderInbound)
class VPNProviderInboundAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "tag",
        "protocol",
        "port",
        "max_users",
        "current_users",
        "is_active",
    )
    list_filter = ("protocol", "is_active", "provider")
    search_fields = ("tag", "provider__name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(provider__brand__in=request.user.admin_brands.all())
        return qs
