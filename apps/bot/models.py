"""
Telegram Bot Models for Multi-Tenant VPN Platform
"""

from django.db import models


class BotState(models.Model):
    """Track bot conversation states for users"""

    class StateType(models.TextChoices):
        MAIN_MENU = "main_menu", "Main Menu"
        PROFILE_SETUP = "profile_setup", "Profile Setup"
        PROFILE_EDIT = "profile_edit", "Profile Edit"
        PURCHASE_FLOW = "purchase_flow", "Purchase Flow"
        PAYMENT_PROCESS = "payment_process", "Payment Process"
        SUPPORT_TICKET = "support_ticket", "Support Ticket"
        REFERRAL_SETUP = "referral_setup", "Referral Setup"
        SUBSCRIPTION_MANAGEMENT = "subscription_management", "Subscription Management"

    user = models.OneToOneField(
        "accounts.User", on_delete=models.CASCADE, related_name="bot_state"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="bot_states"
    )

    current_state = models.CharField(
        max_length=30, choices=StateType.choices, default=StateType.MAIN_MENU
    )

    state_data = models.JSONField(default=dict, blank=True)

    last_message_id = models.BigIntegerField(null=True, blank=True)
    last_inline_message_id = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bot_states"
        unique_together = ["user", "brand"]


class BotMessage(models.Model):
    """Track bot messages for editing and management"""

    class MessageType(models.TextChoices):
        TEXT = "text", "Text Message"
        PHOTO = "photo", "Photo"
        VIDEO = "video", "Video"
        DOCUMENT = "document", "Document"
        INLINE_KEYBOARD = "inline_keyboard", "Inline Keyboard"
        POLL = "poll", "Poll"

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="bot_messages"
    )
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="bot_messages"
    )

    telegram_message_id = models.BigIntegerField()
    message_type = models.CharField(max_length=20, choices=MessageType.choices)
    content = models.TextField()

    handler_name = models.CharField(max_length=100, null=True, blank=True)
    state = models.CharField(max_length=30, null=True, blank=True)

    is_edited = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bot_messages"
        indexes = [
            models.Index(fields=["user", "sent_at"]),
            models.Index(fields=["brand", "sent_at"]),
        ]


class BotKeyboard(models.Model):
    """Reusable keyboard configurations"""

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="bot_keyboards"
    )

    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)

    keyboard_data = models.JSONField()

    context = models.CharField(max_length=100, null=True, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bot_keyboards"
        unique_together = ["brand", "name"]


class BotCommand(models.Model):
    """Bot commands configuration per brand"""

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="bot_commands"
    )

    command = models.CharField(max_length=100)
    description = models.TextField()

    handler_function = models.CharField(max_length=255)

    allowed_user_types = models.JSONField(default=list, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bot_commands"
        unique_together = ["brand", "command"]


class BotAnalytics(models.Model):
    """Bot usage analytics"""

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="bot_analytics"
    )

    date = models.DateField()

    active_users = models.PositiveIntegerField(default=0)
    new_users = models.PositiveIntegerField(default=0)

    messages_received = models.PositiveIntegerField(default=0)
    messages_sent = models.PositiveIntegerField(default=0)

    command_usage = models.JSONField(default=dict, blank=True)

    popular_actions = models.JSONField(default=dict, blank=True)

    errors_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bot_analytics"
        unique_together = ["brand", "date"]
