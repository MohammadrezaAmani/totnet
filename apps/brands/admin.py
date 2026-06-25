"""
Admin configuration for brands app
"""

from django.contrib import admin

from .models import (
    Brand,
    BrandAnalytics,
    BrandApiKey,
    BrandConfiguration,
    BrandPaymentMethod,
    BrandSocialMedia,
    BrandTheme,
    BrandWebhook,
)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "domain",
        "bot_username",
        "status",
        "is_verified",
        "commission_rate",
        "created_at",
    )
    list_filter = ("status", "is_verified", "created_at")
    search_fields = ("name", "slug", "domain", "contact_email", "support_email")
    readonly_fields = ("created_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}

    fieldsets = (
        ("Basic Information", {"fields": ("name", "slug", "domain", "description")}),
        ("Branding", {"fields": ("logo", "primary_color", "secondary_color")}),
        (
            "Contact & Bot",
            {
                "fields": (
                    "contact_email",
                    "support_email",
                    "phone",
                    "address",
                    "bot_token",
                    "bot_username",
                    "webhook_url",
                )
            },
        ),
        ("Social Media", {"fields": ("telegram_channel", "telegram_group", "website")}),
        (
            "Business Settings",
            {"fields": ("currency", "timezone", "language", "status", "is_verified")},
        ),
        ("Platform Settings", {"fields": ("commission_rate",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(
                id__in=request.user.admin_brands.values_list("id", flat=True)
            )
        return qs


@admin.register(BrandConfiguration)
class BrandConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "referral_system_enabled",
        "wallet_system_enabled",
        "support_system_enabled",
        "analytics_enabled",
    )
    list_filter = (
        "referral_system_enabled",
        "wallet_system_enabled",
        "support_system_enabled",
        "analytics_enabled",
    )
    search_fields = ("brand__name",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BrandTheme)
class BrandThemeAdmin(admin.ModelAdmin):
    list_display = ("brand", "button_style", "menu_style", "font_family")
    search_fields = ("brand__name",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BrandPaymentMethod)
class BrandPaymentMethodAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "payment_type",
        "name",
        "is_enabled",
        "display_order",
        "min_amount",
        "max_amount",
    )
    list_filter = ("payment_type", "is_enabled", "brand")
    search_fields = ("brand__name", "name")
    ordering = ("display_order",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BrandSocialMedia)
class BrandSocialMediaAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "platform",
        "username",
        "url",
        "is_primary",
        "is_public",
        "follower_count",
    )
    list_filter = ("platform", "is_primary", "is_public", "brand")
    search_fields = ("brand__name", "username")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BrandAnalytics)
class BrandAnalyticsAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "date",
        "new_users",
        "active_users",
        "total_users",
        "revenue",
        "orders_count",
        "new_referrals",
    )
    list_filter = ("brand", "date")
    search_fields = ("brand__name",)
    date_hierarchy = "date"
    readonly_fields = ("created_at",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BrandWebhook)
class BrandWebhookAdmin(admin.ModelAdmin):
    list_display = ("brand", "name", "url", "is_active", "max_retries", "created_at")
    list_filter = ("is_active", "brand")
    search_fields = ("brand__name", "name", "url")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BrandApiKey)
class BrandApiKeyAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "name",
        "key_prefix",
        "is_active",
        "rate_limit_per_hour",
        "last_used",
        "expires_at",
    )
    list_filter = ("is_active", "brand")
    search_fields = ("brand__name", "name")
    readonly_fields = ("key", "last_used", "created_at")

    def key_prefix(self, obj):
        return f"{obj.key[:20]}..." if obj.key else ""

    key_prefix.short_description = "API Key"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs
