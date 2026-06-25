"""
Admin configuration for referrals app
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Achievement,
    LoyaltyProgram,
    MarketingMaterial,
    Referral,
    ReferralLevel,
    ReferralLink,
    ReferralProgram,
    ReferralReward,
    ReferralStats,
    UserAchievement,
)


@admin.register(ReferralProgram)
class ReferralProgramAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "is_active",
        "name",
        "referrer_reward_type",
        "referrer_reward_value",
        "referee_reward_type",
        "referee_reward_value",
        "conversion_window_days",
        "created_at",
    )
    search_fields = ("brand__name", "name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(ReferralLevel)
class ReferralLevelAdmin(admin.ModelAdmin):
    list_display = (
        "program",
        "level",
        "name",
        "min_referrals",
        "min_conversion_rate",
        "reward_multiplier",
        "bonus_reward",
    )
    list_filter = ("program", "level")
    search_fields = ("program__brand__name", "name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(program__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(ReferralLink)
class ReferralLinkAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "user",
        "brand",
        "click_count",
        "conversion_count",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "brand")
    search_fields = ("code", "user__username", "brand__name")
    readonly_fields = ("click_count", "conversion_count", "created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = (
        "referrer",
        "referee",
        "brand",
        "referral_link",
        "status",
        "converted_at",
        "referrer_reward_amount",
        "referee_reward_amount",
        "created_at",
    )
    list_filter = ("status", "brand", "created_at")
    search_fields = ("referrer__username", "referee__username", "brand__name")
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = (
        "referral",
        "user",
        "brand",
        "reward_type",
        "amount",
        "currency",
        "status",
        "processed_at",
    )
    list_filter = ("reward_type", "status", "brand")
    search_fields = ("referral__referrer__username", "user__username", "brand__name")
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(ReferralStats)
class ReferralStatsAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "brand",
        "date",
        "clicks",
        "registrations",
        "conversions",
        "total_rewards",
    )
    list_filter = ("brand", "date")
    search_fields = ("user__username", "brand__name")
    date_hierarchy = "date"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "name",
        "achievement_type",
        "icon_preview",
        "is_active",
        "is_repeatable",
        "reward_points",
    )
    list_filter = ("achievement_type", "is_active", "is_repeatable", "brand")
    search_fields = ("brand__name", "name", "description")

    def icon_preview(self, obj):
        if obj.icon:
            return format_html('<img src="{}" width="50" height="50" />', obj.icon.url)
        return ""

    icon_preview.short_description = "Icon"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(UserAchievement)
class UserAchievementAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "achievement",
        "progress",
        "is_completed",
        "completed_at",
        "reward_claimed",
    )
    list_filter = ("is_completed", "reward_claimed", "achievement")
    search_fields = ("user__username", "achievement__name")
    list_editable = ("is_completed", "reward_claimed")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(achievement__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(LoyaltyProgram)
class LoyaltyProgramAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "is_active",
        "name",
        "points_per_dollar",
        "points_per_referral",
        "min_redemption_points",
        "point_value_usd",
        "points_expire_days",
    )
    search_fields = ("brand__name", "name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(MarketingMaterial)
class MarketingMaterialAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "name",
        "material_type",
        "is_active",
        "usage_count",
        "created_at",
    )
    list_filter = ("material_type", "is_active", "brand")
    search_fields = ("brand__name", "name", "description")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs
