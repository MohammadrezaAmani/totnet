"""
Admin configuration for orders app
"""

from django import forms
from django.contrib import admin
from django.db.models import Sum
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    Coupon,
    CouponUsage,
    CryptoCurrency,
    Order,
    Payment,
    PaymentCard,
    CardTransferQuote,
    GiftVoucher,
    PaymentGateway,
    Wallet,
    WalletTransaction,
)


class OrderAdminForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        paid_states = {Order.OrderStatus.PAID, Order.OrderStatus.PROCESSING, Order.OrderStatus.COMPLETED}
        price = cleaned.get("final_price", self.instance.final_price)
        if cleaned.get("status") in paid_states and price and price > 0:
            total = 0
            if self.instance.pk:
                total = self.instance.payments.filter(status=Payment.PaymentStatus.CONFIRMED).aggregate(total=Sum("amount"))["total"] or 0
            if total < price:
                self.add_error("status", "برای ثبت این وضعیت، کل مبلغ سفارش باید با پرداخت تأییدشده تسویه شده باشد.")
        return cleaned


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
    list_display = (
        "order_number",
        "brand",
        "user",
        "plan",
        "order_type",
        "status",
        "final_price",
        "currency",
        "recipient",
        "created_at",
        "expires_at",
    )
    list_filter = ("order_type", "status", "brand", "created_at")
    search_fields = ("order_number", "user__username", "plan__name")
    readonly_fields = ("order_id", "order_number", "created_at", "updated_at", "recipient_claimed_at", "gift_reward_percent", "gift_reward_challenge")
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Basic Info",
            {"fields": ("order_id", "order_number", "brand", "user", "plan")},
        ),
        ("Order Type", {"fields": ("order_type", "status", "notes", "admin_notes")}),
        ("Recipient", {"fields": ("recipient", "recipient_email", "recipient_telegram_username", "recipient_claimed_at", "gift_reward_percent", "gift_reward_challenge")}),
        (
            "Pricing",
            {
                "fields": (
                    "original_price",
                    "discount_amount",
                    "tax_amount",
                    "final_price",
                    "currency",
                )
            },
        ),
        ("Payment", {"fields": ("selected_payment_method", "expires_at")}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        previous = self.get_object(request, str(obj.pk)) if change else None
        if obj.status == Payment.PaymentStatus.CONFIRMED and (
            previous is None or previous.status != Payment.PaymentStatus.CONFIRMED
        ):
            obj.verified_at = timezone.now()
            obj.verified_by = request.user
        super().save_model(request, obj, form, change)

    list_display = (
        "payment_id",
        "order",
        "brand",
        "user",
        "payment_method",
        "status",
        "amount",
        "currency",
        "gateway_transaction_id",
        "created_at",
    )
    list_filter = ("payment_method", "status", "brand", "created_at")
    search_fields = (
        "payment_id",
        "order__order_number",
        "user__username",
        "gateway_transaction_id",
    )
    readonly_fields = ("payment_id", "created_at", "updated_at")
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Basic Info",
            {
                "fields": (
                    "payment_id",
                    "order",
                    "brand",
                    "user",
                    "payment_method",
                    "status",
                )
            },
        ),
        ("Amount", {"fields": ("amount", "currency")}),
        (
            "Gateway Info",
            {"fields": ("gateway_name", "gateway_transaction_id", "gateway_response")},
        ),
        ("Card Transfer", {"fields": ("card_number", "cardholder_name")}),
        (
            "Crypto",
            {
                "fields": (
                    "crypto_currency",
                    "crypto_amount",
                    "crypto_address",
                    "crypto_txid",
                )
            },
        ),
        ("Telegram Stars", {"fields": ("stars_amount", "telegram_payment_charge_id")}),
        ("Receipt", {"fields": ("receipt_image", "receipt_reference")}),
        ("Verification", {"fields": ("verified_by", "verified_at")}),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at", "expires_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(PaymentCard)
class PaymentCardAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "bank_name",
        "card_number",
        "cardholder_name",
        "card_type",
        "is_active",
        "unique_amount_enabled",
        "unique_amount_max_extra",
        "display_order",
        "card_color",
        "total_payments",
        "last_used",
    )
    list_filter = ("is_active", "brand")
    search_fields = ("brand__name", "bank_name", "cardholder_name")
    list_editable = ("is_active", "unique_amount_enabled", "display_order", "card_color")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(PaymentGateway)
class PaymentGatewayAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "name",
        "gateway_type",
        "is_sandbox",
        "is_active",
        "transaction_fee_percentage",
        "created_at",
    )
    list_filter = ("gateway_type", "is_sandbox", "is_active", "brand")
    search_fields = ("brand__name", "name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(CryptoCurrency)
class CryptoCurrencyAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "name",
        "symbol",
        "network",
        "is_active",
        "display_order",
        "auto_convert_to_usd",
        "required_confirmations",
    )
    list_filter = ("network", "is_active", "auto_convert_to_usd", "brand")
    search_fields = ("brand__name", "name", "symbol")
    list_editable = ("is_active", "display_order")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    readonly_fields = ("balance", "challenge_frozen_balance")
    list_display = (
        "user",
        "brand",
        "balance",
        "challenge_frozen_balance",
        "currency",
        "is_active",
        "is_frozen",
        "daily_spending_limit",
        "monthly_spending_limit",
    )
    list_filter = ("is_active", "is_frozen", "brand")
    search_fields = ("user__username", "brand__name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "wallet",
        "transaction_type",
        "amount",
        "balance_before",
        "balance_after",
        "description",
        "created_at",
    )
    list_filter = ("transaction_type", "wallet", "created_at")
    search_fields = ("wallet__user__username", "description", "reference_id")
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(wallet__brand__in=request.user.admin_brands.all())
        return qs


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "brand",
        "name",
        "coupon_type",
        "discount_value",
        "max_discount_amount",
        "is_active",
        "max_uses",
        "current_uses",
        "used_percentage",
        "valid_until",
    )
    list_filter = ("coupon_type", "is_active", "new_users_only", "brand")
    search_fields = ("code", "name", "brand__name")
    list_editable = ("is_active",)

    def used_percentage(self, obj):
        if obj.max_uses:
            pct = (obj.current_uses / obj.max_uses) * 100
            color = "green" if pct < 80 else ("orange" if pct < 100 else "red")
            return format_html('<span style="color: {};">{:.1f}%</span>', color, pct)
        return "∞"

    used_percentage.short_description = "Usage %"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ("coupon", "user", "order", "discount_amount", "used_at")
    list_filter = ("coupon", "user")
    search_fields = ("coupon__code", "user__username")
    date_hierarchy = "used_at"


@admin.register(CardTransferQuote)
class CardTransferQuoteAdmin(admin.ModelAdmin):
    list_display = ("order", "card", "base_amount", "expected_amount", "expires_at", "created_at")
    readonly_fields = ("order", "card", "base_amount", "expected_amount", "expires_at", "created_at")
    list_filter = ("card__brand", "created_at")
    search_fields = ("order__order_number", "card__card_number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(card__brand__in=request.user.admin_brands.all())


@admin.register(GiftVoucher)
class GiftVoucherAdmin(admin.ModelAdmin):
    list_display = ("order", "expires_at", "claimed_by", "claimed_at", "new_member_at_claim", "created_at")
    readonly_fields = ("order", "gift_link", "expires_at", "claimed_by", "claimed_at", "new_member_at_claim", "created_at")
    search_fields = ("order__order_number",)

    @admin.display(description='لینک هدیه')
    def gift_link(self, obj):
        from apps.orders.checkout_links import paid_gift_link
        link = paid_gift_link(obj) if obj and obj.pk else None
        return link or 'نام کاربری ربات در تنظیمات برند ثبت نشده است.'
    list_filter = ("order__brand", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(order__brand__in=request.user.admin_brands.all())
