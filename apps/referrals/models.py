"""
Referral System Models for Multi-Tenant VPN Platform
Configurable per brand with flexible reward rules
"""

from django.db import models


class ReferralProgram(models.Model):
    """Brand-specific referral program configuration"""

    class RewardType(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage of Purchase"
        FIXED_AMOUNT = "fixed_amount", "Fixed Amount"
        POINTS = "points", "Reward Points"
        FREE_DAYS = "free_days", "Free Days"
        CUSTOM = "custom", "Custom Reward"

    brand = models.OneToOneField(
        "brands.Brand", on_delete=models.CASCADE, related_name="referral_program"
    )

    is_active = models.BooleanField(default=True)
    name = models.CharField(max_length=100, default="Referral Program")
    description = models.TextField(null=True, blank=True)

    referrer_reward_type = models.CharField(
        max_length=20, choices=RewardType.choices, default=RewardType.PERCENTAGE
    )
    referrer_reward_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=10
    )
    referrer_max_reward = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    referee_reward_type = models.CharField(
        max_length=20, choices=RewardType.choices, default=RewardType.PERCENTAGE
    )
    referee_reward_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=5
    )
    referee_max_reward = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    require_purchase = models.BooleanField(default=True)
    minimum_purchase_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )

    enable_level_rewards = models.BooleanField(default=False)

    conversion_window_days = models.PositiveIntegerField(default=30)

    max_referrals_per_day = models.PositiveIntegerField(default=10)
    max_referrals_per_month = models.PositiveIntegerField(default=100)

    same_ip_limit = models.PositiveIntegerField(default=3)
    require_phone_verification = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "referral_programs"

    def __str__(self):
        return f"{self.brand.name} - {self.name}"


class ReferralLevel(models.Model):
    """Referral level configuration for tiered rewards"""

    program = models.ForeignKey(
        ReferralProgram, on_delete=models.CASCADE, related_name="levels"
    )

    level = models.PositiveIntegerField()
    name = models.CharField(max_length=100)

    min_referrals = models.PositiveIntegerField()
    min_conversion_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    reward_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    bonus_reward = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    features = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "referral_levels"
        unique_together = ["program", "level"]
        ordering = ["level"]


class ReferralLink(models.Model):
    """Individual referral links for users"""

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="referral_links"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="referral_links"
    )

    code = models.CharField(max_length=50, unique=True)
    custom_code = models.CharField(max_length=50, null=True, blank=True)

    click_count = models.PositiveIntegerField(default=0)
    conversion_count = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    campaign_name = models.CharField(max_length=100, null=True, blank=True)
    source = models.CharField(max_length=100, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "referral_links"
        unique_together = ["user", "brand"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["user", "brand"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.code}"


class Referral(models.Model):
    """Individual referral records"""

    class ReferralStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CONVERTED = "converted", "Converted"
        REWARDED = "rewarded", "Rewarded"
        EXPIRED = "expired", "Expired"
        REJECTED = "rejected", "Rejected"

    referrer = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="given_referrals"
    )
    referee = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="received_referrals"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="referrals"
    )

    referral_link = models.ForeignKey(
        ReferralLink, on_delete=models.CASCADE, related_name="referrals"
    )
    status = models.CharField(
        max_length=20, choices=ReferralStatus.choices, default=ReferralStatus.PENDING
    )

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    source = models.CharField(max_length=100, null=True, blank=True)

    conversion_order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True
    )
    converted_at = models.DateTimeField(null=True, blank=True)

    referrer_reward_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    referee_reward_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    rewarded_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "referrals"
        unique_together = ["referrer", "referee", "brand"]
        indexes = [
            models.Index(fields=["referrer", "status"]),
            models.Index(fields=["referee", "brand"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.referrer.username} -> {self.referee.username}"


class ReferralReward(models.Model):
    """Track referral rewards given to users"""

    class RewardStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    referral = models.ForeignKey(
        Referral, on_delete=models.CASCADE, related_name="rewards"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="referral_rewards"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="referral_rewards"
    )

    reward_type = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    status = models.CharField(
        max_length=20, choices=RewardStatus.choices, default=RewardStatus.PENDING
    )

    processed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    wallet_transaction = models.ForeignKey(
        "orders.WalletTransaction", on_delete=models.SET_NULL, null=True, blank=True
    )

    notes = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "referral_rewards"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["brand", "created_at"]),
        ]


class ReferralStats(models.Model):
    """Daily referral statistics for users"""

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="referral_stats"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="referral_stats"
    )

    date = models.DateField()

    clicks = models.PositiveIntegerField(default=0)
    registrations = models.PositiveIntegerField(default=0)
    conversions = models.PositiveIntegerField(default=0)

    total_rewards = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "referral_stats"
        unique_together = ["user", "brand", "date"]
        ordering = ["-date"]


class Achievement(models.Model):
    """Achievements and badges for gamification"""

    class AchievementType(models.TextChoices):
        REFERRAL = "referral", "Referral Achievement"
        PURCHASE = "purchase", "Purchase Achievement"
        LOYALTY = "loyalty", "Loyalty Achievement"
        SOCIAL = "social", "Social Achievement"
        MILESTONE = "milestone", "Milestone Achievement"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="achievements"
    )

    name = models.CharField(max_length=100)
    description = models.TextField()
    achievement_type = models.CharField(max_length=20, choices=AchievementType.choices)

    icon = models.ImageField(upload_to="achievements/", null=True, blank=True)
    color = models.CharField(max_length=7, default="#ffd700")

    requirements = models.JSONField(default=dict, blank=True)

    reward_points = models.PositiveIntegerField(default=0)
    reward_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    is_active = models.BooleanField(default=True)
    is_repeatable = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "achievements"
        unique_together = ["brand", "name"]

    def __str__(self):
        return f"{self.brand.name} - {self.name}"


class UserAchievement(models.Model):
    """Track user achievements"""

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="achievements"
    )
    achievement = models.ForeignKey(
        Achievement, on_delete=models.CASCADE, related_name="user_achievements"
    )

    progress = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_completed = models.BooleanField(default=False)

    completed_at = models.DateTimeField(null=True, blank=True)
    reward_claimed = models.BooleanField(default=False)
    reward_claimed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_achievements"
        unique_together = ["user", "achievement"]


class LoyaltyProgram(models.Model):
    """Brand loyalty program configuration"""

    brand = models.OneToOneField(
        "brands.Brand", on_delete=models.CASCADE, related_name="loyalty_program"
    )

    is_active = models.BooleanField(default=True)
    name = models.CharField(max_length=100, default="Loyalty Program")

    points_per_dollar = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    points_per_referral = models.PositiveIntegerField(default=100)

    min_redemption_points = models.PositiveIntegerField(default=100)
    point_value_usd = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)

    points_expire_days = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "loyalty_programs"


class MarketingMaterial(models.Model):
    """Marketing materials for referrers"""

    class MaterialType(models.TextChoices):
        BANNER = "banner", "Banner"
        TEXT_TEMPLATE = "text_template", "Text Template"
        VIDEO = "video", "Video"
        SOCIAL_POST = "social_post", "Social Media Post"
        EMAIL_TEMPLATE = "email_template", "Email Template"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="marketing_materials"
    )

    name = models.CharField(max_length=100)
    material_type = models.CharField(max_length=20, choices=MaterialType.choices)
    description = models.TextField(null=True, blank=True)

    content = models.TextField(null=True, blank=True)
    image = models.ImageField(upload_to="marketing/", null=True, blank=True)
    video = models.FileField(upload_to="marketing/videos/", null=True, blank=True)

    target_audience = models.JSONField(default=list, blank=True)

    is_active = models.BooleanField(default=True)
    usage_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "marketing_materials"
        ordering = ["name"]
