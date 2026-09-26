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
    RewardAccount,
    RewardBoxCapacity,
    RewardPointBox,
    RewardPointLedger,
    RewardRedemption,
    RewardService,
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
    readonly_fields = (
        "reference_service",
        "lifetime_reference_service",
        "lifetime_point_value",
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "brand" and not request.user.is_superuser:
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                pk__in=request.user.admin_brands.values("pk")
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

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

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "program" and not request.user.is_superuser:
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                brand__in=request.user.admin_brands.all()
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

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
    readonly_fields = tuple(field.name for field in ReferralReward._meta.fields)

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


@admin.register(RewardService)
class RewardServiceAdmin(admin.ModelAdmin):
    list_display = ("brand", "plan", "free_points", "point_value", "is_active")
    readonly_fields = ("point_value_snapshot",)
    list_filter = ("brand", "is_active")
    search_fields = ("brand__name", "plan__name")
    actions = ("activate_as_reference_service", "set_as_lifetime_reference_service")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            brands = request.user.admin_brands.all()
            if db_field.name == "brand":
                kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                    pk__in=brands.values("pk")
                )
            elif db_field.name == "plan":
                kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                    brand__in=brands
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.action(description="Use selected service as active points reference")
    def activate_as_reference_service(self, request, queryset):
        services = list(queryset.select_related("brand"))
        brand_ids = {service.brand_id for service in services}
        if len(services) != len(brand_ids):
            self.message_user(request, "Select at most one service per brand.", level="ERROR")
            return
        from .services import RewardConfigurationError, set_active_reference_service

        updated = 0
        for service in services:
            try:
                set_active_reference_service(
                    brand_id=service.brand_id, service_id=service.pk
                )
            except RewardConfigurationError as exc:
                self.message_user(
                    request, f"{service.brand.name}: {exc}", level="ERROR"
                )
                continue
            updated += 1
        self.message_user(request, f"Updated active reference service for {updated} brand(s).")

    @admin.action(description="Set selected service as the lifetime points reference (once)")
    def set_as_lifetime_reference_service(self, request, queryset):
        services = list(queryset.select_related("brand"))
        if len({service.brand_id for service in services}) != len(services):
            self.message_user(request, "Select at most one service per brand.", level="ERROR")
            return
        from django.db import transaction

        updated = 0
        for service in services:
            with transaction.atomic():
                program = ReferralProgram.objects.select_for_update().get(brand_id=service.brand_id)
                if program.lifetime_reference_service_id:
                    self.message_user(
                        request,
                        f"{service.brand.name} already has a lifetime reference service.",
                        level="ERROR",
                    )
                    continue
                if not service.is_active or not service.plan.is_active:
                    self.message_user(request, f"{service.brand.name}: select an active service and plan.", level="ERROR")
                    continue
                if service.plan.currency != service.brand.currency or not service.box_capacities.exists():
                    self.message_user(request, f"{service.brand.name}: currency and point-box capacities must be configured.", level="ERROR")
                    continue
                from .services import RewardConfigurationError, _service_value

                try:
                    value = _service_value(service)
                except RewardConfigurationError:
                    self.message_user(request, f"{service.brand.name}: service profit configuration is invalid.", level="ERROR")
                    continue
                program.lifetime_reference_service = service
                program.lifetime_point_value = value
                program.save(update_fields=("lifetime_reference_service", "lifetime_point_value", "updated_at"))
                updated += 1
        self.message_user(request, f"Set lifetime reference service for {updated} brand(s).")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(RewardBoxCapacity)
class RewardBoxCapacityAdmin(admin.ModelAdmin):
    list_display = ("service", "sequence", "capacity")
    list_filter = ("service__brand", "service")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "service" and not request.user.is_superuser:
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                brand__in=request.user.admin_brands.all()
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(service__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(RewardAccount)
class RewardAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "brand", "reference_service", "liquid_points", "lifetime_points", "lifetime_profit")
    list_filter = ("brand",)
    search_fields = ("user__username", "brand__name")
    readonly_fields = tuple(field.name for field in RewardAccount._meta.fields)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(RewardPointBox)
class RewardPointBoxAdmin(admin.ModelAdmin):
    list_display = ("account", "service", "cycle", "sequence", "filled", "capacity", "state")
    list_filter = ("service__brand", "state", "service")
    readonly_fields = tuple(field.name for field in RewardPointBox._meta.fields)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(account__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(RewardPointLedger)
class RewardPointLedgerAdmin(admin.ModelAdmin):
    list_display = ("account", "entry_type", "points_delta", "value_delta", "order", "created_at")
    list_filter = ("service__brand", "entry_type", "created_at")
    search_fields = ("idempotency_key", "account__user__username")
    readonly_fields = tuple(field.name for field in RewardPointLedger._meta.fields)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(account__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(RewardRedemption)
class RewardRedemptionAdmin(admin.ModelAdmin):
    list_display = ("account", "service", "order", "points_spent", "created_at")
    list_filter = ("service__brand", "created_at")
    search_fields = ("account__user__username", "idempotency_key")
    readonly_fields = tuple(field.name for field in RewardRedemption._meta.fields)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(account__brand__in=request.user.admin_brands.all())
        return qs
