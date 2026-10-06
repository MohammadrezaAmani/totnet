"""
Admin configuration for subscriptions app
"""

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone
from django.utils.html import format_html
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.services.capabilities import capabilities_for

from .models import (
    ProviderRemoteSubscription,
    Subscription,
    SubscriptionClaim,
    SubscriptionConfig,
    SubscriptionNotification,
    SubscriptionPlan,
    SubscriptionRenewal,
    SubscriptionTransfer,
    SubscriptionUsage,
)


class SubscriptionPlanAdminForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("is_active"):
            return cleaned
        brand = cleaned.get("brand")
        provider = cleaned.get("vpn_provider")
        if provider is None and brand is not None:
            provider = VPNProvider.objects.filter(
                brand=brand,
                status=VPNProvider.ProviderStatus.ACTIVE,
                is_default=True,
            ).first()
        if provider is None or not capabilities_for(provider.provider_type).provision:
            raise forms.ValidationError(
                "An active plan must route to a provider with an implemented provisioning adapter."
            )
        if provider.provider_type == VPNProvider.ProviderType.CONNECTIX and not all(
            cleaned.get(field)
            for field in (
                "upstream_plan_id",
                "upstream_plan_name",
                "upstream_group_id",
                "upstream_group_name",
                "upstream_count_of_devices",
            )
        ):
            raise forms.ValidationError(
                "An active Connectix plan requires mapped plan/group IDs, names, and device count."
            )
        return cleaned


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    form = SubscriptionPlanAdminForm
    list_display = (
        "name",
        "brand",
        "service_category",
        "plan_type",
        "vpn_provider",
        "upstream_plan_name",
        "upstream_plan_id",
        "upstream_group_name",
        "upstream_count_of_devices",
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
        "service_category",
        "plan_type",
        "is_active",
        "is_visible",
        "is_featured",
        "brand",
        "created_at",
    )
    search_fields = ("name", "description", "brand__name", "upstream_plan_id")
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
        "provisioning_state",
        "remote_account_id_display",
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
    readonly_fields = (
        "subscription_id",
        "provisioning_started_at",
        "provisioning_attempts",
        "provisioning_state",
        "provisioning_error_code",
        "provisioning_error",
        "provisioning_retryable",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Basic Info",
            {
                "fields": (
                    "subscription_id",
                    "brand",
                    "user",
                    "plan",
                    "order",
                    "provisioning_started_at",
                    "provisioning_attempts",
                    "provisioning_state",
                    "provisioning_error_code",
                    "provisioning_error",
                    "provisioning_retryable",
                )
            },
        ),
        ("VPN Provider", {"fields": ("vpn_provider", "vpn_user_email")}),
        ("Ownership", {"fields": ("owner", "is_gift", "gift_message")}),
        ("Status & Dates", {"fields": ("status", "starts_at", "expires_at")}),
        ("Traffic", {"fields": ("traffic_used_gb", "traffic_limit_gb")}),
        (
            "Configuration",
            {"fields": ("connectix_username",)},
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
            pct = (float(obj.traffic_used_gb) / float(obj.traffic_limit_gb)) * 100
            color = "green" if pct < 80 else ("orange" if pct < 100 else "red")
            return format_html('<span style="color: {};">{:.1f}%</span>', color, pct)
        return "∞"

    traffic_percentage_used.short_description = "Traffic Used"

    def remote_account_id_display(self, obj):
        remote_account = getattr(obj, "remote_account", None)
        return remote_account.remote_id if remote_account else "-"

    remote_account_id_display.short_description = "Provider Remote ID"

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("remote_account")
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(ProviderRemoteSubscription)
class ProviderRemoteSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "subscription",
        "provider",
        "remote_id",
        "username",
        "state",
        "remote_status",
        "last_synced_at",
    )
    list_filter = ("provider", "state", "remote_status")
    search_fields = ("remote_id", "username", "subscription__subscription_id")
    readonly_fields = tuple(
        field.name for field in ProviderRemoteSubscription._meta.fields
    )
    exclude = ("subscription_url", "metadata")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = (
            super().get_queryset(request).select_related("subscription", "provider")
        )
        if not request.user.is_superuser:
            queryset = queryset.filter(
                subscription__brand__in=request.user.admin_brands.all()
            )
        return queryset


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


@admin.register(SubscriptionClaim)
class SubscriptionClaimAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "user",
        "brand",
        "status",
        "matched_subscription",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )
    list_filter = ("status", "brand", "created_at")
    search_fields = (
        "username",
        "user__username",
        "user__telegram_id",
        "matched_subscription__connectix_username",
        "matched_subscription__vpn_user_email",
    )
    autocomplete_fields = ("user", "matched_subscription")
    readonly_fields = ("created_at", "updated_at", "reviewed_by", "reviewed_at")
    actions = ("approve_and_assign_verified_subscription", "reject_claims")

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "user", "brand", "matched_subscription", "reviewed_by"
        )
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "matched_subscription" and not request.user.is_superuser:
            field.queryset = field.queryset.filter(
                brand__in=request.user.admin_brands.all()
            )
        return field

    @admin.action(description="تأیید و اتصال اشتراک انتخاب‌شده به صاحب درخواست")
    def approve_and_assign_verified_subscription(self, request, queryset):
        approved = 0
        skipped = 0
        for claim_id in queryset.values_list("pk", flat=True):
            with transaction.atomic():
                claim = (
                    SubscriptionClaim.objects.select_for_update()
                    .select_related("matched_subscription")
                    .get(pk=claim_id)
                )
                subscription = claim.matched_subscription
                if subscription is None or subscription.brand_id != claim.brand_id:
                    skipped += 1
                    continue
                # This transfer is deliberately an explicit admin action. The bot
                # never changes ownership from a username alone.
                Subscription.objects.select_for_update().filter(pk=subscription.pk).update(
                    owner_id=claim.user_id
                )
                claim.status = SubscriptionClaim.Status.APPROVED
                claim.reviewed_by = request.user
                claim.reviewed_at = timezone.now()
                claim.save(
                    update_fields=[
                        "status",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
                approved += 1
        if approved:
            self.message_user(
                request, f"{approved} درخواست تأیید و اشتراک آن به کاربر متصل شد.", messages.SUCCESS
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} درخواست بدون اشتراک منطبق یا با برند ناسازگار رد شد؛ ابتدا matched subscription را بررسی کنید.",
                messages.WARNING,
            )

    @admin.action(description="رد کردن درخواست‌های انتخاب‌شده")
    def reject_claims(self, request, queryset):
        now = timezone.now()
        updated = queryset.exclude(status=SubscriptionClaim.Status.APPROVED).update(
            status=SubscriptionClaim.Status.REJECTED,
            reviewed_by=request.user,
            reviewed_at=now,
        )
        self.message_user(request, f"{updated} درخواست رد شد.", messages.SUCCESS)


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
