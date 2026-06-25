"""
Brand Models for Multi-Tenant VPN Platform
Each brand operates independently with its own configuration
"""

from django.db import models


class Brand(models.Model):
    """Core brand model - represents a VPN business on the platform"""

    class BrandStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        PENDING = "pending", "Pending Approval"

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    domain = models.URLField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)

    logo = models.ImageField(upload_to="brands/logos/", null=True, blank=True)
    primary_color = models.CharField(max_length=7, default="#007bff")
    secondary_color = models.CharField(max_length=7, default="#6c757d")

    contact_email = models.EmailField()
    support_email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    address = models.TextField(null=True, blank=True)

    bot_token = models.CharField(max_length=255, unique=True)
    bot_username = models.CharField(max_length=100, null=True, blank=True)
    webhook_url = models.URLField(null=True, blank=True)

    telegram_channel = models.CharField(max_length=100, null=True, blank=True)
    telegram_group = models.CharField(max_length=100, null=True, blank=True)
    website = models.URLField(null=True, blank=True)

    currency = models.CharField(max_length=3, default="USD")
    timezone = models.CharField(max_length=50, default="UTC")
    language = models.CharField(max_length=10, default="en")

    status = models.CharField(
        max_length=20, choices=BrandStatus.choices, default=BrandStatus.PENDING
    )
    is_verified = models.BooleanField(default=False)

    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=10.00)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brands"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.name


class BrandConfiguration(models.Model):
    """Brand-specific configuration settings"""

    brand = models.OneToOneField(
        Brand, on_delete=models.CASCADE, related_name="configuration"
    )

    welcome_message = models.TextField(default="Welcome to our VPN service!")
    help_message = models.TextField(null=True, blank=True)

    referral_system_enabled = models.BooleanField(default=True)
    wallet_system_enabled = models.BooleanField(default=True)
    support_system_enabled = models.BooleanField(default=True)
    analytics_enabled = models.BooleanField(default=True)

    max_subscriptions_per_user = models.PositiveIntegerField(default=10)
    max_referrals_per_day = models.PositiveIntegerField(default=100)

    custom_fields = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brand_configurations"


class BrandTheme(models.Model):
    """Brand visual theme and UI customization"""

    brand = models.OneToOneField(Brand, on_delete=models.CASCADE, related_name="theme")

    button_style = models.CharField(
        max_length=20,
        choices=[
            ("rounded", "Rounded"),
            ("square", "Square"),
            ("pill", "Pill"),
        ],
        default="rounded",
    )

    menu_style = models.CharField(
        max_length=20,
        choices=[
            ("grid", "Grid"),
            ("list", "List"),
            ("carousel", "Carousel"),
        ],
        default="grid",
    )

    background_image = models.ImageField(
        upload_to="brands/backgrounds/", null=True, blank=True
    )
    banner_image = models.ImageField(upload_to="brands/banners/", null=True, blank=True)

    font_family = models.CharField(max_length=50, default="Arial")

    custom_css = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brand_themes"


class BrandPaymentMethod(models.Model):
    """Payment methods available for each brand"""

    class PaymentType(models.TextChoices):
        CARD_TRANSFER = "card_transfer", "Card Transfer"
        ONLINE_GATEWAY = "online_gateway", "Online Gateway"
        CRYPTOCURRENCY = "cryptocurrency", "Cryptocurrency"
        TELEGRAM_STARS = "telegram_stars", "Telegram Stars"
        WALLET = "wallet", "Wallet"

    brand = models.ForeignKey(
        Brand, on_delete=models.CASCADE, related_name="payment_methods"
    )

    payment_type = models.CharField(max_length=20, choices=PaymentType.choices)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)

    is_enabled = models.BooleanField(default=True)
    configuration = models.JSONField(default=dict, blank=True)

    icon = models.ImageField(upload_to="payment_icons/", null=True, blank=True)
    display_order = models.PositiveIntegerField(default=0)

    min_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    max_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brand_payment_methods"
        ordering = ["display_order", "name"]


class BrandSocialMedia(models.Model):
    """Social media accounts for brands"""

    class PlatformType(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        INSTAGRAM = "instagram", "Instagram"
        TWITTER = "twitter", "Twitter"
        FACEBOOK = "facebook", "Facebook"
        YOUTUBE = "youtube", "YouTube"
        LINKEDIN = "linkedin", "LinkedIn"
        TIKTOK = "tiktok", "TikTok"

    brand = models.ForeignKey(
        Brand, on_delete=models.CASCADE, related_name="social_media"
    )

    platform = models.CharField(max_length=20, choices=PlatformType.choices)
    username = models.CharField(max_length=100)
    url = models.URLField()

    is_primary = models.BooleanField(default=False)
    is_public = models.BooleanField(default=True)

    follower_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brand_social_media"
        unique_together = ["brand", "platform", "username"]


class BrandAnalytics(models.Model):
    """Daily analytics for each brand"""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="analytics")

    date = models.DateField()

    new_users = models.PositiveIntegerField(default=0)
    active_users = models.PositiveIntegerField(default=0)
    total_users = models.PositiveIntegerField(default=0)

    revenue = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    orders_count = models.PositiveIntegerField(default=0)
    avg_order_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    new_referrals = models.PositiveIntegerField(default=0)
    referral_conversion_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )

    support_tickets = models.PositiveIntegerField(default=0)
    resolved_tickets = models.PositiveIntegerField(default=0)
    avg_response_time = models.DurationField(null=True, blank=True)

    total_traffic_gb = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    peak_concurrent_users = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "brand_analytics"
        unique_together = ["brand", "date"]
        ordering = ["-date"]


class BrandWebhook(models.Model):
    """Webhook configuration for brands"""

    class EventType(models.TextChoices):
        USER_REGISTERED = "user_registered", "User Registered"
        USER_PURCHASED = "user_purchased", "User Purchased"
        USER_RENEWED = "user_renewed", "User Renewed"
        USER_EXPIRED = "user_expired", "User Expired"
        PAYMENT_RECEIVED = "payment_received", "Payment Received"
        SUPPORT_TICKET = "support_ticket", "Support Ticket"

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="webhooks")

    name = models.CharField(max_length=100)
    url = models.URLField()
    events = models.JSONField(default=list)

    is_active = models.BooleanField(default=True)
    secret = models.CharField(max_length=255, null=True, blank=True)

    headers = models.JSONField(default=dict, blank=True)

    max_retries = models.PositiveIntegerField(default=3)
    retry_delay = models.PositiveIntegerField(default=300)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brand_webhooks"


class BrandApiKey(models.Model):
    """API keys for brand integrations"""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="api_keys")

    name = models.CharField(max_length=100)
    key = models.CharField(max_length=255, unique=True)

    permissions = models.JSONField(default=list)

    rate_limit_per_hour = models.PositiveIntegerField(default=1000)

    is_active = models.BooleanField(default=True)
    last_used = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "brand_api_keys"
