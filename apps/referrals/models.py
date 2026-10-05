"""
Referral System Models for Multi-Tenant VPN Platform
Configurable per brand with flexible reward rules
"""

import uuid

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
    reference_service = models.ForeignKey(
        "RewardService",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="active_programs",
    )
    lifetime_reference_service = models.ForeignKey(
        "RewardService",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lifetime_programs",
    )
    lifetime_point_value = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
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

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("lifetime_reference_service_id", "lifetime_point_value")
                .first()
            )
            if previous and previous["lifetime_reference_service_id"]:
                self.lifetime_reference_service_id = previous[
                    "lifetime_reference_service_id"
                ]
                self.lifetime_point_value = previous["lifetime_point_value"]
        if self.lifetime_reference_service_id and self.lifetime_point_value is None:
            service = self.lifetime_reference_service
            self.lifetime_point_value = service.point_value
        super().save(*args, **kwargs)

    class Meta:
        db_table = "referral_programs"

    def __str__(self):
        return f"{self.brand.name} - {self.name}"

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        for service_id in (
            self.reference_service_id,
            self.lifetime_reference_service_id,
        ):
            if (
                service_id
                and RewardService.objects.filter(pk=service_id)
                .exclude(brand_id=self.brand_id)
                .exists()
            ):
                raise ValidationError("Reference services must belong to this brand")
        if self.lifetime_point_value is not None and self.lifetime_point_value <= 0:
            raise ValidationError(
                {"lifetime_point_value": "Point value must be positive"}
            )


class ReferralLevel(models.Model):
    """Referral level configuration for tiered rewards"""

    program = models.ForeignKey(
        ReferralProgram, on_delete=models.CASCADE, related_name="levels"
    )

    level = models.PositiveIntegerField()
    name = models.CharField(max_length=100)

    min_referrals = models.PositiveIntegerField()
    min_lifetime_points = models.DecimalField(
        max_digits=20, decimal_places=8, default=0
    )
    badge = models.CharField(max_length=24, blank=True)
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


class ReferralClick(models.Model):
    """Deduplicate referral-link starts by user to keep click stats meaningful."""

    link = models.ForeignKey(
        ReferralLink, on_delete=models.CASCADE, related_name="visits"
    )
    visitor = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="referral_visits"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="referral_visits"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "referral_clicks"
        constraints = [
            models.UniqueConstraint(
                fields=["link", "visitor"], name="uniq_referral_visit_per_user"
            )
        ]


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
        max_digits=20, decimal_places=8, default=0
    )
    referee_reward_amount = models.DecimalField(
        max_digits=20, decimal_places=8, default=0
    )
    rewarded_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "referrals"
        constraints = [
            models.UniqueConstraint(
                fields=["referee", "brand"], name="uniq_referral_attribution_per_brand"
            )
        ]
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
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="referral_rewards",
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="referral_rewards"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="referral_rewards"
    )

    reward_type = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=20, decimal_places=8)
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
        constraints = [
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(order__isnull=False),
                name="uniq_referral_reward_per_order",
            )
        ]
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

    requirements = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Thresholds: referrals, conversions, purchases, lifetime_points, "
            'total_spent, wallet_deposits. Example: {"referrals": 5}.'
        ),
    )

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
    claim_count = models.PositiveIntegerField(default=0)

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


class RewardService(models.Model):
    """A plan with a configured cost/profit basis and point-box layout."""

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="reward_services"
    )
    plan = models.ForeignKey(
        "subscriptions.SubscriptionPlan",
        on_delete=models.PROTECT,
        related_name="reward_service_configs",
    )
    free_points = models.DecimalField(max_digits=20, decimal_places=8, default=10)
    point_value_snapshot = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "reward_services"
        constraints = [
            models.UniqueConstraint(
                fields=["brand", "plan"], name="uniq_reward_service_plan"
            )
        ]

    @property
    def point_value(self):
        if self.point_value_snapshot is not None:
            return self.point_value_snapshot
        return self.plan.price - self.plan.upstream_cost

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("point_value_snapshot", flat=True)
                .first()
            )
            if previous is not None:
                self.point_value_snapshot = previous
        if self.point_value_snapshot is None and self.plan_id:
            self.point_value_snapshot = self.plan.price - self.plan.upstream_cost
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.plan_id and self.brand_id and self.plan.brand_id != self.brand_id:
            from django.core.exceptions import ValidationError

            raise ValidationError("Reward service plan must belong to the same brand")


class RewardBoxCapacity(models.Model):
    service = models.ForeignKey(
        RewardService, on_delete=models.CASCADE, related_name="box_capacities"
    )
    sequence = models.PositiveSmallIntegerField()
    capacity = models.DecimalField(max_digits=20, decimal_places=8)

    class Meta:
        db_table = "reward_box_capacities"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["service", "sequence"], name="uniq_reward_box_sequence"
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gt=0), name="reward_box_capacity_positive"
            ),
        ]


class RewardAccount(models.Model):
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="reward_accounts"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="reward_accounts"
    )
    reference_service = models.ForeignKey(
        RewardService,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="accounts",
    )
    liquid_points = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    lifetime_points = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    lifetime_profit = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    next_box_cycle = models.PositiveIntegerField(default=1)
    redemption_nonce = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "reward_accounts"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "brand"], name="uniq_reward_account_user_brand"
            )
        ]

    def clean(self):
        super().clean()
        if self.user_id and self.brand_id and self.user.brand_id != self.brand_id:
            from django.core.exceptions import ValidationError

            raise ValidationError("Reward accounts must match the user's brand")


class RewardPointBox(models.Model):
    class State(models.TextChoices):
        OPEN = "open", "Open"
        COMPLETE = "complete", "Complete"
        CONVERTED = "converted", "Converted"

    account = models.ForeignKey(
        RewardAccount, on_delete=models.CASCADE, related_name="boxes"
    )
    service = models.ForeignKey(
        RewardService, on_delete=models.PROTECT, related_name="point_boxes"
    )
    cycle = models.PositiveIntegerField(default=1)
    sequence = models.PositiveSmallIntegerField()
    capacity = models.DecimalField(max_digits=20, decimal_places=8)
    filled = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    spent_points = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    point_value_snapshot = models.DecimalField(max_digits=20, decimal_places=2)
    state = models.CharField(max_length=16, choices=State.choices, default=State.OPEN)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reward_point_boxes"
        ordering = ["cycle", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "service", "cycle", "sequence"],
                name="uniq_reward_point_box_cycle",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gt=0),
                name="reward_point_box_capacity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(filled__gte=0),
                name="reward_point_box_filled_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(filled__lte=models.F("capacity")),
                name="reward_point_box_filled_within_capacity",
            ),
            models.CheckConstraint(
                condition=models.Q(spent_points__gte=0),
                name="reward_point_box_spent_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(spent_points__lte=models.F("filled")),
                name="reward_point_box_spent_within_filled",
            ),
        ]


class RewardPointLedger(models.Model):
    class EntryType(models.TextChoices):
        EARNED = "earned", "Earned"
        BOX_COMPLETED = "box_completed", "Box Completed"
        SERVICE_REBASE = "service_rebase", "Service Rebase"
        CONVERTED_TO_WALLET = "converted_to_wallet", "Converted to Wallet"
        REDEEMED = "redeemed", "Redeemed"
        ACHIEVEMENT_BONUS = "achievement_bonus", "Achievement Bonus"

    account = models.ForeignKey(
        RewardAccount, on_delete=models.CASCADE, related_name="ledger_entries"
    )
    service = models.ForeignKey(
        RewardService, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, null=True, blank=True
    )
    referral_reward = models.ForeignKey(
        ReferralReward, on_delete=models.PROTECT, null=True, blank=True
    )
    entry_type = models.CharField(max_length=24, choices=EntryType.choices)
    points_delta = models.DecimalField(max_digits=20, decimal_places=8)
    value_delta = models.DecimalField(max_digits=26, decimal_places=10)
    point_value_snapshot = models.DecimalField(max_digits=20, decimal_places=2)
    idempotency_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reward_point_ledger"
        indexes = [models.Index(fields=["account", "created_at"])]


class RewardRedemption(models.Model):
    """An idempotent claim of one configured reward service."""

    account = models.ForeignKey(
        RewardAccount, on_delete=models.PROTECT, related_name="redemptions"
    )
    service = models.ForeignKey(RewardService, on_delete=models.PROTECT)
    order = models.OneToOneField(
        "orders.Order", on_delete=models.PROTECT, related_name="reward_redemption"
    )
    points_spent = models.DecimalField(max_digits=20, decimal_places=8)
    idempotency_key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reward_redemptions"


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
