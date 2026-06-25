"""
Subscription Models for Multi-Tenant VPN Platform
Supports unlimited plans, traffic plans, time plans, and hybrid plans
"""

import uuid

from django.db import models
from django.utils import timezone


class SubscriptionPlan(models.Model):
    """VPN subscription plans for each brand"""

    class PlanType(models.TextChoices):
        UNLIMITED = "unlimited", "Unlimited"
        TRAFFIC_BASED = "traffic_based", "Traffic Based"
        TIME_BASED = "time_based", "Time Based"
        HYBRID = "hybrid", "Hybrid (Traffic + Time)"

    class DurationUnit(models.TextChoices):
        DAYS = "days", "Days"
        WEEKS = "weeks", "Weeks"
        MONTHS = "months", "Months"
        YEARS = "years", "Years"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="subscription_plans"
    )

    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    plan_type = models.CharField(max_length=20, choices=PlanType.choices)

    price = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    duration_value = models.PositiveIntegerField(null=True, blank=True)
    duration_unit = models.CharField(
        max_length=10, choices=DurationUnit.choices, null=True, blank=True
    )

    traffic_limit_gb = models.PositiveIntegerField(null=True, blank=True)

    max_users = models.PositiveIntegerField(default=1)

    features = models.JSONField(default=list, blank=True)

    allowed_protocols = models.JSONField(default=list, blank=True)
    server_locations = models.JSONField(default=list, blank=True)

    is_featured = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    color = models.CharField(max_length=7, default="#007bff")

    is_active = models.BooleanField(default=True)
    is_visible = models.BooleanField(default=True)

    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    offer_expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscription_plans"
        unique_together = ["brand", "name"]
        ordering = ["display_order", "price"]

    def __str__(self):
        return f"{self.brand.name} - {self.name}"

    @property
    def discounted_price(self):
        """Calculate price after discount"""
        if self.discount_percentage > 0:
            discount_amount = (self.price * self.discount_percentage) / 100
            return self.price - discount_amount
        return self.price


class Subscription(models.Model):
    """Individual VPN subscriptions"""

    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"
        PENDING = "pending", "Pending Activation"

    subscription_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="subscriptions"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="subscriptions"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="subscriptions"
    )

    vpn_provider = models.ForeignKey(
        "vpn_providers.VPNProvider",
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    vpn_user_email = models.CharField(max_length=255, null=True, blank=True)

    owner = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="owned_subscriptions"
    )
    is_gift = models.BooleanField(default=False)
    gift_message = models.TextField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.PENDING,
    )

    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)

    traffic_used_gb = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    traffic_limit_gb = models.PositiveIntegerField(null=True, blank=True)

    subscription_url = models.TextField(null=True, blank=True)
    connection_configs = models.JSONField(default=dict, blank=True)
    qr_codes = models.JSONField(default=list, blank=True)

    connectix_username = models.CharField(max_length=100, null=True, blank=True)
    connectix_password = models.CharField(max_length=100, null=True, blank=True)

    auto_renewal_enabled = models.BooleanField(default=False)

    expiry_notification_sent = models.BooleanField(default=False)
    traffic_warning_sent = models.BooleanField(default=False)

    last_connection = models.DateTimeField(null=True, blank=True)
    total_connections = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscriptions"
        indexes = [
            models.Index(fields=["brand", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["vpn_provider", "vpn_user_email"]),
        ]

    def __str__(self):
        return f"{self.subscription_id} - {self.user.username} ({self.plan.name})"

    @property
    def is_expired(self):
        """Check if subscription is expired"""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False

    @property
    def days_remaining(self):
        """Calculate days remaining until expiration"""
        if self.expires_at:
            remaining = self.expires_at - timezone.now()
            return max(0, remaining.days)
        return None

    @property
    def traffic_percentage_used(self):
        """Calculate percentage of traffic used"""
        if self.traffic_limit_gb:
            return min(100, (float(self.traffic_used_gb) / self.traffic_limit_gb) * 100)
        return 0


class SubscriptionUsage(models.Model):
    """Track subscription usage statistics"""

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="usage_stats"
    )

    date = models.DateField()

    upload_bytes = models.BigIntegerField(default=0)
    download_bytes = models.BigIntegerField(default=0)

    connection_count = models.PositiveIntegerField(default=0)
    online_duration_minutes = models.PositiveIntegerField(default=0)

    peak_concurrent_connections = models.PositiveIntegerField(default=0)

    servers_used = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subscription_usage"
        unique_together = ["subscription", "date"]
        ordering = ["-date"]


class SubscriptionRenewal(models.Model):
    """Track subscription renewals and upgrades"""

    class RenewalType(models.TextChoices):
        RENEWAL = "renewal", "Renewal"
        UPGRADE = "upgrade", "Upgrade"
        DOWNGRADE = "downgrade", "Downgrade"
        TRANSFER = "transfer", "Transfer"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="renewals"
    )

    renewal_type = models.CharField(max_length=20, choices=RenewalType.choices)

    old_plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="old_renewals"
    )
    new_plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="new_renewals"
    )

    old_expires_at = models.DateTimeField()
    new_expires_at = models.DateTimeField()

    traffic_added_gb = models.PositiveIntegerField(default=0)

    amount_paid = models.DecimalField(max_digits=15, decimal_places=2)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="renewals"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subscription_renewals"
        ordering = ["-created_at"]


class SubscriptionNotification(models.Model):
    """Subscription-related notifications"""

    class NotificationType(models.TextChoices):
        EXPIRY_WARNING = "expiry_warning", "Expiry Warning"
        EXPIRED = "expired", "Expired"
        TRAFFIC_WARNING = "traffic_warning", "Traffic Warning"
        TRAFFIC_EXHAUSTED = "traffic_exhausted", "Traffic Exhausted"
        RENEWAL_REMINDER = "renewal_reminder", "Renewal Reminder"
        ACTIVATED = "activated", "Activated"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="notifications"
    )

    notification_type = models.CharField(
        max_length=20, choices=NotificationType.choices
    )
    message = models.TextField()

    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivery_method = models.CharField(max_length=20, null=True, blank=True)

    scheduled_for = models.DateTimeField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subscription_notifications"
        indexes = [
            models.Index(fields=["scheduled_for", "is_sent"]),
            models.Index(fields=["subscription", "notification_type"]),
        ]


class SubscriptionConfig(models.Model):
    """VPN configuration details for subscriptions"""

    subscription = models.OneToOneField(
        Subscription, on_delete=models.CASCADE, related_name="config"
    )

    vmess_config = models.JSONField(null=True, blank=True)
    vless_config = models.JSONField(null=True, blank=True)
    trojan_config = models.JSONField(null=True, blank=True)
    shadowsocks_config = models.JSONField(null=True, blank=True)
    wireguard_config = models.JSONField(null=True, blank=True)

    subscription_url = models.TextField(null=True, blank=True)

    qr_code_data = models.JSONField(default=list, blank=True)

    server_info = models.JSONField(default=dict, blank=True)

    clash_config = models.TextField(null=True, blank=True)
    v2ray_config = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscription_configs"


class SubscriptionTransfer(models.Model):
    """Track subscription transfers between users"""

    class TransferStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        REJECTED = "rejected", "Rejected"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="transfers"
    )

    from_user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="outgoing_transfers"
    )
    to_user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="incoming_transfers"
    )

    reason = models.TextField(null=True, blank=True)
    message = models.TextField(null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=TransferStatus.choices, default=TransferStatus.PENDING
    )

    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_transfers",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscription_transfers"
