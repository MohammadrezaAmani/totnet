"""
Admin configuration for orders app
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Coupon,
    CouponUsage,
    CryptoCurrency,
    Order,
    Payment,
    PaymentCard,
    PaymentGateway,
    Wallet,
    WalletTransaction,
)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
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
    readonly_fields = ("order_id", "order_number", "created_at", "updated_at")
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Basic Info",
            {"fields": ("order_id", "order_number", "brand", "user", "plan")},
        ),
        ("Order Type", {"fields": ("order_type", "status", "notes", "admin_notes")}),
        ("Recipient", {"fields": ("recipient", "recipient_email")}),
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
        "display_order",
        "total_payments",
        "last_used",
    )
    list_filter = ("is_active", "brand")
    search_fields = ("brand__name", "bank_name", "cardholder_name")
    list_editable = ("is_active", "display_order")

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
    list_display = (
        "user",
        "brand",
        "balance",
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
