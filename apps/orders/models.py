"""
Order and Payment Models for Multi-Tenant VPN Platform
Supports multiple payment methods: Card Transfer, Online Gateway, Crypto, Telegram Stars, Wallet
"""

import uuid

from django.db import models

from apps.accounts.models import User


class Order(models.Model):
    """Main order model for subscription purchases"""

    class OrderStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting Payment"
        PAID = "paid", "Paid"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"
        FAILED = "failed", "Failed"

    class OrderType(models.TextChoices):
        NEW_SUBSCRIPTION = "new_subscription", "New Subscription"
        RENEWAL = "renewal", "Renewal"
        UPGRADE = "upgrade", "Upgrade"
        GIFT = "gift", "Gift Purchase"

    order_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order_number = models.CharField(max_length=20, unique=True)

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="orders"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="orders"
    )
    plan = models.ForeignKey(
        "subscriptions.SubscriptionPlan",
        on_delete=models.CASCADE,
        related_name="orders",
    )

    order_type = models.CharField(
        max_length=20, choices=OrderType.choices, default=OrderType.NEW_SUBSCRIPTION
    )
    status = models.CharField(
        max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING
    )

    recipient = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="received_orders",
        null=True,
        blank=True,
    )
    recipient_email = models.EmailField(null=True, blank=True)

    original_price = models.DecimalField(max_digits=15, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    final_price = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    coupon_code = models.CharField(max_length=50, null=True, blank=True)
    coupon_discount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    selected_payment_method = models.CharField(max_length=50, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(null=True, blank=True)
    admin_notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "orders"
        indexes = [
            models.Index(fields=["brand", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["order_number"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.order_number} - {self.user.username}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            import random
            from datetime import datetime

            date_str = datetime.now().strftime("%Y%m%d")
            random_part = str(random.randint(10000, 99999))
            self.order_number = f"ORD-{date_str}-{random_part}"
        super().save(*args, **kwargs)


class Payment(models.Model):
    """Payment records for orders"""

    class PaymentMethod(models.TextChoices):
        CARD_TRANSFER = "card_transfer", "Card Transfer"
        ONLINE_GATEWAY = "online_gateway", "Online Gateway"
        CRYPTOCURRENCY = "cryptocurrency", "Cryptocurrency"
        TELEGRAM_STARS = "telegram_stars", "Telegram Stars"
        WALLET = "wallet", "Wallet"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        AWAITING_CONFIRMATION = "awaiting_confirmation", "Awaiting Confirmation"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    payment_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="payments"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")

    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    status = models.CharField(
        max_length=25, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )

    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    gateway_name = models.CharField(max_length=100, null=True, blank=True)
    gateway_transaction_id = models.CharField(max_length=255, null=True, blank=True)
    gateway_response = models.JSONField(default=dict, blank=True)

    receipt_image = models.ImageField(upload_to="receipts/", null=True, blank=True)
    receipt_reference = models.CharField(max_length=255, null=True, blank=True)

    card_number = models.CharField(max_length=20, null=True, blank=True)
    cardholder_name = models.CharField(max_length=100, null=True, blank=True)

    crypto_currency = models.CharField(max_length=10, null=True, blank=True)
    crypto_amount = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True
    )
    crypto_address = models.CharField(max_length=255, null=True, blank=True)
    crypto_txid = models.CharField(max_length=255, null=True, blank=True)

    stars_amount = models.PositiveIntegerField(null=True, blank=True)
    telegram_payment_charge_id = models.CharField(max_length=255, null=True, blank=True)

    verified_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_payments",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(null=True, blank=True)
    receipt_file = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "payments"
        indexes = [
            models.Index(fields=["order", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["gateway_transaction_id"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.payment_id} - {self.get_payment_method_display()}"


class PaymentCard(models.Model):
    """Bank cards for card transfer payments"""

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="payment_cards"
    )

    bank_name = models.CharField(max_length=100)
    card_number = models.CharField(max_length=20)
    cardholder_name = models.CharField(max_length=100)

    card_type = models.CharField(max_length=50, null=True, blank=True)
    card_color = models.CharField(max_length=7, default="#007bff")

    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    total_payments = models.PositiveIntegerField(default=0)
    last_used = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_cards"
        ordering = ["display_order", "bank_name"]


class PaymentGateway(models.Model):
    """Online payment gateway configurations"""

    class GatewayType(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        PAYPAL = "paypal", "PayPal"
        RAZORPAY = "razorpay", "Razorpay"
        ZARINPAL = "zarinpal", "ZarinPal"
        IDPAY = "idpay", "IDPay"
        CUSTOM = "custom", "Custom Gateway"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="payment_gateways"
    )

    name = models.CharField(max_length=100)
    gateway_type = models.CharField(max_length=20, choices=GatewayType.choices)

    api_key = models.CharField(max_length=500)
    secret_key = models.CharField(max_length=500, null=True, blank=True)
    merchant_id = models.CharField(max_length=255, null=True, blank=True)

    is_sandbox = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    supported_currencies = models.JSONField(default=list, blank=True)

    transaction_fee_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )
    fixed_transaction_fee = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )

    callback_url = models.URLField(null=True, blank=True)
    webhook_url = models.URLField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_gateways"
        unique_together = ["brand", "name"]


class CryptoCurrency(models.Model):
    """Supported cryptocurrencies for payments"""

    class NetworkType(models.TextChoices):
        BITCOIN = "bitcoin", "Bitcoin"
        ETHEREUM = "ethereum", "Ethereum"
        TRON = "tron", "Tron"
        BSC = "bsc", "Binance Smart Chain"
        POLYGON = "polygon", "Polygon"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="cryptocurrencies"
    )

    name = models.CharField(max_length=50)
    symbol = models.CharField(max_length=10)
    network = models.CharField(max_length=20, choices=NetworkType.choices)

    wallet_address = models.CharField(max_length=255)
    private_key = models.CharField(max_length=500, null=True, blank=True)

    icon = models.ImageField(upload_to="crypto_icons/", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    auto_convert_to_usd = models.BooleanField(default=True)
    conversion_rate = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    last_rate_update = models.DateTimeField(null=True, blank=True)

    required_confirmations = models.PositiveIntegerField(default=3)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cryptocurrencies"
        unique_together = ["brand", "symbol", "network"]
        ordering = ["display_order", "name"]


class Wallet(models.Model):
    """User wallet for storing balance"""

    user = models.OneToOneField(
        "accounts.User", on_delete=models.CASCADE, related_name="wallet"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="wallets"
    )

    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")

    is_active = models.BooleanField(default=True)
    is_frozen = models.BooleanField(default=False)

    daily_spending_limit = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    monthly_spending_limit = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "wallets"
        unique_together = ["user", "brand"]


class WalletTransaction(models.Model):
    """Wallet transaction history"""

    class TransactionType(models.TextChoices):
        DEPOSIT = "deposit", "Deposit"
        WITHDRAWAL = "withdrawal", "Withdrawal"
        PAYMENT = "payment", "Payment"
        REFUND = "refund", "Refund"
        BONUS = "bonus", "Bonus"
        REFERRAL_REWARD = "referral_reward", "Referral Reward"
        ADMIN_ADJUSTMENT = "admin_adjustment", "Admin Adjustment"

    wallet = models.ForeignKey(
        Wallet, on_delete=models.CASCADE, related_name="transactions"
    )

    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    amount = models.DecimalField(max_digits=15, decimal_places=2)

    balance_before = models.DecimalField(max_digits=15, decimal_places=2)
    balance_after = models.DecimalField(max_digits=15, decimal_places=2)

    reference_id = models.CharField(max_length=255, null=True, blank=True)
    description = models.TextField()

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wallet_transactions"
        indexes = [
            models.Index(fields=["wallet", "created_at"]),
            models.Index(fields=["transaction_type"]),
        ]
        ordering = ["-created_at"]


class Coupon(models.Model):
    """Discount coupons and promo codes"""

    class CouponType(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED_AMOUNT = "fixed_amount", "Fixed Amount"
        FREE_TRIAL = "free_trial", "Free Trial"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="coupons"
    )

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)

    coupon_type = models.CharField(max_length=20, choices=CouponType.choices)
    discount_value = models.DecimalField(max_digits=15, decimal_places=2)
    max_discount_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    is_active = models.BooleanField(default=True)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()

    max_uses = models.PositiveIntegerField(null=True, blank=True)
    max_uses_per_user = models.PositiveIntegerField(default=1)
    current_uses = models.PositiveIntegerField(default=0)

    applicable_plans = models.ManyToManyField(
        "subscriptions.SubscriptionPlan", blank=True
    )
    minimum_order_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    new_users_only = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "coupons"
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["brand", "is_active"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.brand.name}"


class CouponUsage(models.Model):
    """Track coupon usage by users"""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="usages")
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="coupon_usages"
    )
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="coupon_usages"
    )

    discount_amount = models.DecimalField(max_digits=15, decimal_places=2)

    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "coupon_usages"
        unique_together = ["coupon", "user", "order"]
