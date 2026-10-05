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

    class ServiceCategory(models.TextChoices):
        NORMAL = "normal", "Normal service"
        ROYAL = "royal", "Royal service"
        IRAN_IP = "iran_ip", "Iran IP service"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="subscription_plans"
    )
    vpn_provider = models.ForeignKey(
        "vpn_providers.VPNProvider",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="subscription_plans",
    )

    upstream_plan_id = models.CharField(max_length=100, blank=True)
    upstream_group_id = models.CharField(max_length=100, blank=True)
    upstream_group_name = models.CharField(max_length=100, blank=True)
    upstream_plan_name = models.CharField(max_length=200, blank=True)
    upstream_count_of_devices = models.PositiveIntegerField(null=True, blank=True)

    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    service_category = models.CharField(
        max_length=20, choices=ServiceCategory.choices, default=ServiceCategory.NORMAL
    )
    plan_type = models.CharField(max_length=20, choices=PlanType.choices)

    price = models.DecimalField(max_digits=15, decimal_places=2)
    upstream_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")

    duration_value = models.PositiveIntegerField(null=True, blank=True)
    duration_unit = models.CharField(
        max_length=10, choices=DurationUnit.choices, null=True, blank=True
    )

    traffic_limit_gb = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

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

    def clean(self):
        super().clean()
        if (
            self.vpn_provider_id
            and self.brand_id
            and self.vpn_provider.brand_id != self.brand_id
        ):
            from django.core.exceptions import ValidationError

            raise ValidationError(
                {
                    "vpn_provider": "The provider and subscription plan must belong to the same brand."
                }
            )

    @property
    def discounted_price(self):
        """Calculate the currently valid discounted price."""
        discount_is_active = self.discount_percentage > 0 and (
            self.offer_expires_at is None or self.offer_expires_at > timezone.now()
        )
        if discount_is_active:
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
    provisioning_started_at = models.DateTimeField(null=True, blank=True)
    provisioning_attempts = models.PositiveSmallIntegerField(default=0)
    provisioning_state = models.CharField(
        max_length=24,
        choices=[
            ("queued", "Queued"),
            ("in_progress", "In progress"),
            ("retryable_error", "Retryable error"),
            ("needs_review", "Needs review"),
            ("unsupported", "Unsupported"),
            ("active", "Active"),
        ],
        default="queued",
    )
    provisioning_error_code = models.CharField(max_length=80, blank=True)
    provisioning_error = models.TextField(blank=True)
    provisioning_retryable = models.BooleanField(default=False)

    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)

    traffic_used_gb = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    traffic_limit_gb = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    subscription_url = models.TextField(null=True, blank=True)
    connection_configs = models.JSONField(default=dict, blank=True)
    qr_codes = models.JSONField(default=list, blank=True)

    connectix_username = models.CharField(max_length=100, null=True, blank=True)

    auto_renewal_enabled = models.BooleanField(default=False)

    expiry_notification_sent = models.BooleanField(default=False)
    traffic_warning_sent = models.BooleanField(default=False)

    last_connection = models.DateTimeField(null=True, blank=True)
    total_connections = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscriptions"
        constraints = [
            models.UniqueConstraint(
                fields=["order"], name="uniq_subscription_per_order"
            )
        ]
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
            return min(
                100,
                (float(self.traffic_used_gb) / float(self.traffic_limit_gb)) * 100,
            )
        return 0


class ProviderRemoteSubscription(models.Model):
    """Provider-side identity and sync state for one local subscription."""

    class State(models.TextChoices):
        PROVISIONING = "provisioning", "Provisioning"
        ACTIVE = "active", "Active"
        ERROR = "error", "Error"

    subscription = models.OneToOneField(
        Subscription, on_delete=models.CASCADE, related_name="remote_account"
    )
    provider = models.ForeignKey(
        "vpn_providers.VPNProvider",
        on_delete=models.PROTECT,
        related_name="remote_subscriptions",
    )
    remote_id = models.CharField(max_length=128, null=True, blank=True)
    username = models.CharField(max_length=100, blank=True)
    subscription_url = models.TextField(blank=True)
    remote_status = models.CharField(max_length=40, blank=True)
    state = models.CharField(
        max_length=20, choices=State.choices, default=State.PROVISIONING
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "provider_remote_subscriptions"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "remote_id"],
                condition=models.Q(remote_id__isnull=False),
                name="uniq_provider_remote_subscription_id",
            )
        ]
        indexes = [models.Index(fields=["provider", "state"])]


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
