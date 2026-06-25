"""
Admin configuration for vpn_providers app
Enhanced with Hiddify integration
"""

from asgiref.sync import async_to_sync
from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    HiddifyAdmin,
    VPNProvider,
    VPNProviderHealthCheck,
    VPNProviderInbound,
    VPNProviderLog,
    VPNProviderStats,
    VPNServer,
)
from .services.hiddify import HiddifyProvider


@admin.register(HiddifyAdmin)
class HiddifyAdminAdmin(admin.ModelAdmin):
    """Admin for Hiddify Panel Admins"""

    list_display = (
        "name",
        "mode",
        "provider",
        "telegram_id",
        "can_add_admin",
        "is_synced",
        "linked_user",
        "last_sync",
    )
    list_filter = ("mode", "is_synced", "provider", "can_add_admin")
    search_fields = ("name", "uuid", "telegram_id", "provider__name")
    readonly_fields = ("uuid", "is_synced", "last_sync", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("provider", "uuid", "name", "mode", "lang")}),
        ("Telegram Integration", {"fields": ("telegram_id", "local_user")}),
        (
            "Permissions",
            {
                "fields": (
                    "can_add_admin",
                    "max_users",
                    "max_active_users",
                    "parent_admin",
                )
            },
        ),
        (
            "Sync Status",
            {"fields": ("is_synced", "last_sync", "comment"), "classes": ("collapse",)},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def linked_user(self, obj):
        if obj.local_user:
            url = reverse("admin:accounts_user_change", args=[obj.local_user.id])
            return format_html('<a href="{}">{}</a>', url, obj.local_user.username)
        return "-"

    linked_user.short_description = "Linked User"

    actions = ["sync_with_panel", "create_in_panel", "delete_from_panel"]

    def sync_with_panel(self, request, queryset):
        """Sync selected admins from Hiddify panel"""
        from .signals import sync_hiddify_admins

        for admin in queryset:
            if admin.provider and admin.provider.provider_type == "hiddify":
                try:
                    async_to_sync(sync_hiddify_admins)(admin.provider)
                    messages.success(
                        request, f"Synced admins from {admin.provider.name}"
                    )
                except Exception as e:
                    messages.error(request, f"Error syncing {admin.provider.name}: {e}")
            else:
                messages.warning(
                    request, f"{admin.name} is not linked to a Hiddify provider"
                )

    sync_with_panel.short_description = "Sync admins from Hiddify panel"

    def create_in_panel(self, request, queryset):
        """Create selected admins in Hiddify panel"""
        for admin in queryset:
            if not admin.provider or admin.provider.provider_type != "hiddify":
                messages.warning(
                    request, f"{admin.name} is not linked to a Hiddify provider"
                )
                continue

            try:
                from .services.hiddify import HiddifyAdmin as HiddifyAdminData
                from .services.hiddify import HiddifyAdminMode, HiddifyLanguage

                provider = HiddifyProvider(
                    base_url=admin.provider.base_url,
                    api_key=admin.provider.api_key,
                    proxy_path=admin.provider.proxy_path or "",
                    public_api_key=admin.public_api_key,
                )

                admin_data = HiddifyAdminData(
                    name=admin.name,
                    mode=HiddifyAdminMode(admin.mode),
                    lang=HiddifyLanguage(admin.lang),
                    can_add_admin=admin.can_add_admin,
                    telegram_id=admin.telegram_id,
                    comment=admin.comment,
                )

                created = async_to_sync(provider.create_admin)(admin_data)
                async_to_sync(provider.close)()

                if created and created.uuid:
                    admin.uuid = created.uuid
                    admin.is_synced = True
                    admin.last_sync = timezone.now()
                    admin.save()
                    messages.success(
                        request, f"Created admin {admin.name} in Hiddify panel"
                    )
                else:
                    messages.error(request, f"Failed to create admin {admin.name}")

            except Exception as e:
                messages.error(request, f"Error creating {admin.name}: {e}")

    create_in_panel.short_description = "Create selected admins in Hiddify panel"

    def delete_from_panel(self, request, queryset):
        """Delete selected admins from Hiddify panel"""
        for admin in queryset:
            if not admin.uuid:
                messages.warning(request, f"{admin.name} has no UUID")
                continue

            try:
                provider = HiddifyProvider(
                    base_url=admin.provider.base_url,
                    api_key=admin.provider.api_key,
                    proxy_path=admin.provider.proxy_path or "",
                    public_api_key=admin.public_api_key,
                )

                success = async_to_sync(provider.delete_admin)(admin.uuid)
                async_to_sync(provider.close)()

                if success:
                    admin.delete()
                    messages.success(
                        request, f"Deleted admin {admin.name} from Hiddify panel"
                    )
                else:
                    messages.error(request, f"Failed to delete {admin.name}")

            except Exception as e:
                messages.error(request, f"Error deleting {admin.name}: {e}")

    delete_from_panel.short_description = "Delete selected admins from Hiddify panel"


@admin.register(VPNProvider)
class VPNProviderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "provider_type",
        "brand",
        "base_url",
        "status",
        "health_status",
        "users_display",
        "is_default",
        "priority",
        "last_health_check",
        "hiddify_admins_count",
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

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "provider_type",
                    "brand",
                    "description",
                    "status",
                    "is_default",
                    "priority",
                )
            },
        ),
        (
            "API Configuration",
            {
                "fields": (
                    "base_url",
                    "api_key",
                    "public_api_key",
                    "proxy_path",
                    "default_admin_uuid",
                )
            },
        ),
        ("Capacity", {"fields": ("max_users", "current_users", "total_subscriptions")}),
        (
            "Health Status",
            {
                "fields": ("health_status", "last_health_check", "response_time"),
                "classes": ("collapse",),
            },
        ),
        ("Configuration", {"fields": ("configuration",), "classes": ("collapse",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def users_display(self, obj):
        return f"{obj.current_users}/{obj.max_users}"

    users_display.short_description = "Users"

    def hiddify_admins_count(self, obj):
        if obj.provider_type == "hiddify":
            return obj.hiddify_admins.count()
        return "-"

    hiddify_admins_count.short_description = "Hiddify Admins"

    actions = ["test_connection", "sync_hiddify_admins", "check_health"]

    def test_connection(self, request, queryset):
        """Test connection to selected providers"""
        for provider in queryset:
            try:
                if provider.provider_type == "hiddify":
                    hiddify = HiddifyProvider(
                        base_url=provider.base_url,
                        api_key=provider.api_key,
                        proxy_path=provider.proxy_path or "",
                        public_api_key=provider.public_api_key,
                    )
                    success = async_to_sync(hiddify.test_connection)()
                    async_to_sync(hiddify.close)()

                    if success:
                        messages.success(
                            request, f"✅ Connection successful to {provider.name}"
                        )
                    else:
                        messages.error(
                            request, f"❌ Connection failed to {provider.name}"
                        )
                else:
                    messages.warning(
                        request,
                        f"⚠️ Test not implemented for {provider.get_provider_type_display()}",
                    )
            except Exception as e:
                messages.error(request, f"❌ Error testing {provider.name}: {e}")

    test_connection.short_description = "Test connection"

    def sync_hiddify_admins(self, request, queryset):
        """Sync admins from Hiddify panel"""
        from .signals import sync_hiddify_admins

        for provider in queryset.filter(provider_type="hiddify"):
            try:
                async_to_sync(sync_hiddify_admins)(provider)
                messages.success(request, f"Synced admins from {provider.name}")
            except Exception as e:
                messages.error(request, f"Error syncing {provider.name}: {e}")

    sync_hiddify_admins.short_description = "Sync Hiddify admins"

    def check_health(self, request, queryset):
        """Check health of selected providers"""
        for provider in queryset:
            try:
                if provider.provider_type == "hiddify":
                    hiddify = HiddifyProvider(
                        base_url=provider.base_url,
                        api_key=provider.api_key,
                        proxy_path=provider.proxy_path or "",
                        public_api_key=provider.public_api_key,
                    )

                    status = async_to_sync(hiddify.get_server_status)()
                    info = async_to_sync(hiddify.get_panel_info)()
                    async_to_sync(hiddify.close)()

                    if status and info:
                        provider.health_status = "healthy"
                        provider.status = VPNProvider.ProviderStatus.ACTIVE
                        provider.save()
                        messages.success(
                            request,
                            f"✅ {provider.name} is healthy (v{info.get('version', 'unknown')})",
                        )
                    else:
                        provider.health_status = "unhealthy"
                        provider.save()
                        messages.error(request, f"❌ {provider.name} is unhealthy")
            except Exception as e:
                provider.health_status = "error"
                provider.save()
                messages.error(
                    request, f"❌ Health check failed for {provider.name}: {e}"
                )

    check_health.short_description = "Check health"

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
