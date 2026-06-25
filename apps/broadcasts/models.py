"""
Broadcast and Notification Models for Multi-Tenant VPN Platform
Advanced targeting, scheduling, A/B testing, and analytics
"""

import uuid

from django.db import models


class BroadcastCampaign(models.Model):
    """Broadcast campaigns for each brand"""

    class CampaignType(models.TextChoices):
        MARKETING = "marketing", "Marketing Campaign"
        ANNOUNCEMENT = "announcement", "Announcement"
        NOTIFICATION = "notification", "Notification"
        SURVEY = "survey", "Survey"
        PROMOTIONAL = "promotional", "Promotional"
        EMERGENCY = "emergency", "Emergency Alert"

    class CampaignStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    campaign_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="broadcast_campaigns"
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="created_campaigns"
    )

    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    campaign_type = models.CharField(max_length=20, choices=CampaignType.choices)

    subject = models.CharField(max_length=255, null=True, blank=True)
    message = models.TextField()

    image = models.ImageField(upload_to="broadcasts/images/", null=True, blank=True)
    video = models.FileField(upload_to="broadcasts/videos/", null=True, blank=True)
    document = models.FileField(
        upload_to="broadcasts/documents/", null=True, blank=True
    )

    targeting_rules = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20, choices=CampaignStatus.choices, default=CampaignStatus.DRAFT
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    is_ab_test = models.BooleanField(default=False)
    ab_test_percentage = models.PositiveIntegerField(default=50)

    send_immediately = models.BooleanField(default=False)
    priority = models.CharField(
        max_length=20,
        choices=[
            ("low", "Low"),
            ("normal", "Normal"),
            ("high", "High"),
            ("urgent", "Urgent"),
        ],
        default="normal",
    )

    max_retries = models.PositiveIntegerField(default=3)
    retry_delay_minutes = models.PositiveIntegerField(default=5)

    target_audience_count = models.PositiveIntegerField(default=0)
    messages_sent = models.PositiveIntegerField(default=0)
    messages_delivered = models.PositiveIntegerField(default=0)
    messages_failed = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_campaigns"
        indexes = [
            models.Index(fields=["brand", "status"]),
            models.Index(fields=["scheduled_at"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.brand.name} - {self.name}"


class BroadcastMessage(models.Model):
    """Individual broadcast messages sent to users"""

    class MessageStatus(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    campaign = models.ForeignKey(
        BroadcastCampaign, on_delete=models.CASCADE, related_name="messages"
    )
    recipient = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="received_broadcasts"
    )

    content = models.TextField()

    variant = models.CharField(
        max_length=1,
        choices=[("A", "Variant A"), ("B", "Variant B")],
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20, choices=MessageStatus.choices, default=MessageStatus.QUEUED
    )
    platform = models.CharField(max_length=20, default="telegram")

    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)

    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    error_message = models.TextField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)

    clicked = models.BooleanField(default=False)
    clicked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "broadcast_messages"
        unique_together = ["campaign", "recipient"]
        indexes = [
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["recipient", "status"]),
            models.Index(fields=["status", "queued_at"]),
        ]

    def __str__(self):
        return f"{self.campaign.name} -> {self.recipient.username}"


class BroadcastTemplate(models.Model):
    """Reusable broadcast message templates"""

    class TemplateType(models.TextChoices):
        WELCOME = "welcome", "Welcome Message"
        PROMOTION = "promotion", "Promotional Message"
        REMINDER = "reminder", "Reminder Message"
        EXPIRY_WARNING = "expiry_warning", "Expiry Warning"
        RENEWAL_OFFER = "renewal_offer", "Renewal Offer"
        CUSTOM = "custom", "Custom Template"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="broadcast_templates"
    )

    name = models.CharField(max_length=255)
    template_type = models.CharField(max_length=20, choices=TemplateType.choices)
    description = models.TextField(null=True, blank=True)

    subject_template = models.CharField(max_length=255, null=True, blank=True)
    message_template = models.TextField()

    available_variables = models.JSONField(default=list, blank=True)

    default_image = models.ImageField(
        upload_to="broadcast_templates/", null=True, blank=True
    )

    usage_count = models.PositiveIntegerField(default=0)
    last_used = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_templates"
        unique_together = ["brand", "name"]

    def __str__(self):
        return f"{self.brand.name} - {self.name}"


class BroadcastAudience(models.Model):
    """Saved audience segments for targeting"""

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="broadcast_audiences"
    )

    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)

    filters = models.JSONField(default=dict, blank=True)

    user_count = models.PositiveIntegerField(default=0)
    last_calculated = models.DateTimeField(null=True, blank=True)

    auto_update = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_audiences"
        unique_together = ["brand", "name"]

    def __str__(self):
        return f"{self.brand.name} - {self.name}"


class NotificationPreference(models.Model):
    """User notification preferences"""

    class NotificationType(models.TextChoices):
        MARKETING = "marketing", "Marketing Messages"
        ANNOUNCEMENTS = "announcements", "Announcements"
        REMINDERS = "reminders", "Reminders"
        EXPIRY_ALERTS = "expiry_alerts", "Expiry Alerts"
        PROMOTIONAL = "promotional", "Promotional Offers"
        SURVEYS = "surveys", "Surveys"
        SUPPORT = "support", "Support Messages"

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    brand = models.ForeignKey(
        "brands.Brand",
        on_delete=models.CASCADE,
        related_name="user_notification_preferences",
    )

    notification_type = models.CharField(
        max_length=20, choices=NotificationType.choices
    )
    is_enabled = models.BooleanField(default=True)

    telegram_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=False)
    sms_enabled = models.BooleanField(default=False)

    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)
    timezone = models.CharField(max_length=50, default="UTC")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notification_preferences"
        unique_together = ["user", "brand", "notification_type"]


class AutomationRule(models.Model):
    """Automated messaging rules based on user behavior"""

    class TriggerType(models.TextChoices):
        USER_REGISTERED = "user_registered", "User Registered"
        FIRST_PURCHASE = "first_purchase", "First Purchase"
        SUBSCRIPTION_EXPIRING = "subscription_expiring", "Subscription Expiring"
        SUBSCRIPTION_EXPIRED = "subscription_expired", "Subscription Expired"
        INACTIVITY = "inactivity", "User Inactivity"
        REFERRAL_MILESTONE = "referral_milestone", "Referral Milestone"
        PURCHASE_MILESTONE = "purchase_milestone", "Purchase Milestone"
        BIRTHDAY = "birthday", "User Birthday"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="automation_rules"
    )

    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)

    trigger_type = models.CharField(max_length=30, choices=TriggerType.choices)
    trigger_conditions = models.JSONField(default=dict, blank=True)

    template = models.ForeignKey(
        BroadcastTemplate, on_delete=models.CASCADE, related_name="automation_rules"
    )
    delay_minutes = models.PositiveIntegerField(default=0)

    max_sends_per_user = models.PositiveIntegerField(default=1)
    cooldown_days = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    total_triggered = models.PositiveIntegerField(default=0)
    total_sent = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "automation_rules"
        unique_together = ["brand", "name"]

    def __str__(self):
        return f"{self.brand.name} - {self.name}"


class AutomationExecution(models.Model):
    """Track automation rule executions"""

    class ExecutionStatus(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        PROCESSING = "processing", "Processing"
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    rule = models.ForeignKey(
        AutomationRule, on_delete=models.CASCADE, related_name="executions"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="automation_executions"
    )

    status = models.CharField(
        max_length=20,
        choices=ExecutionStatus.choices,
        default=ExecutionStatus.SCHEDULED,
    )

    trigger_data = models.JSONField(default=dict, blank=True)
    triggered_at = models.DateTimeField()

    scheduled_for = models.DateTimeField()
    executed_at = models.DateTimeField(null=True, blank=True)

    broadcast_message = models.ForeignKey(
        BroadcastMessage, on_delete=models.SET_NULL, null=True, blank=True
    )

    error_message = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "automation_executions"
        indexes = [
            models.Index(fields=["rule", "status"]),
            models.Index(fields=["scheduled_for", "status"]),
        ]


class BroadcastAnalytics(models.Model):
    """Daily analytics for broadcast campaigns"""

    campaign = models.ForeignKey(
        BroadcastCampaign, on_delete=models.CASCADE, related_name="analytics"
    )

    date = models.DateField()

    messages_sent = models.PositiveIntegerField(default=0)
    messages_delivered = models.PositiveIntegerField(default=0)
    messages_failed = models.PositiveIntegerField(default=0)

    messages_read = models.PositiveIntegerField(default=0)
    messages_clicked = models.PositiveIntegerField(default=0)

    variant_a_sent = models.PositiveIntegerField(default=0)
    variant_b_sent = models.PositiveIntegerField(default=0)
    variant_a_engagement = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )
    variant_b_engagement = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )

    conversions = models.PositiveIntegerField(default=0)
    revenue_generated = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "broadcast_analytics"
        unique_together = ["campaign", "date"]
        ordering = ["-date"]


class PushNotification(models.Model):
    """Push notifications for mobile apps"""

    class NotificationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="push_notifications"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="push_notifications"
    )

    title = models.CharField(max_length=255)
    body = models.TextField()
    icon = models.URLField(null=True, blank=True)
    image = models.URLField(null=True, blank=True)

    action_url = models.URLField(null=True, blank=True)
    action_data = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20,
        choices=NotificationStatus.choices,
        default=NotificationStatus.PENDING,
    )

    device_token = models.CharField(max_length=500, null=True, blank=True)
    platform = models.CharField(max_length=20, null=True, blank=True)

    scheduled_for = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    error_message = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "push_notifications"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["scheduled_for", "status"]),
        ]
