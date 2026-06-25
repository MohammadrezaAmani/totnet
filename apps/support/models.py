"""
Support System Models for Multi-Tenant VPN Platform
Comprehensive ticket management with SLA tracking and performance metrics
"""

import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class SupportCategory(models.Model):
    """Support ticket categories for each brand"""

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="support_categories"
    )

    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)

    response_time_hours = models.PositiveIntegerField(default=24)
    resolution_time_hours = models.PositiveIntegerField(default=72)

    auto_assign_to = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True
    )
    department = models.CharField(max_length=100, null=True, blank=True)

    default_priority = models.CharField(
        max_length=20,
        choices=[
            ("low", "Low"),
            ("normal", "Normal"),
            ("high", "High"),
            ("urgent", "Urgent"),
        ],
        default="normal",
    )
    display_order = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_categories"
        unique_together = ["brand", "name"]
        ordering = ["display_order", "name"]

    def __str__(self):
        return f"{self.brand.name} - {self.name}"


class SupportTicket(models.Model):
    """Support tickets"""

    class TicketStatus(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        PENDING_CUSTOMER = "pending_customer", "Pending Customer Response"
        PENDING_INTERNAL = "pending_internal", "Pending Internal Review"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    class TicketPriority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class TicketSource(models.TextChoices):
        TELEGRAM = "telegram", "Telegram Bot"
        WEB = "web", "Web Panel"
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        API = "api", "API"

    ticket_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    ticket_number = models.CharField(max_length=20, unique=True)

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="support_tickets"
    )
    customer = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="support_tickets"
    )
    category = models.ForeignKey(
        SupportCategory, on_delete=models.CASCADE, related_name="tickets"
    )

    assigned_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)

    subject = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(
        max_length=20, choices=TicketStatus.choices, default=TicketStatus.OPEN
    )
    priority = models.CharField(
        max_length=20, choices=TicketPriority.choices, default=TicketPriority.NORMAL
    )
    source = models.CharField(
        max_length=20, choices=TicketSource.choices, default=TicketSource.TELEGRAM
    )

    related_subscription = models.ForeignKey(
        "subscriptions.Subscription", on_delete=models.SET_NULL, null=True, blank=True
    )
    related_order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True
    )

    sla_response_due = models.DateTimeField(null=True, blank=True)
    sla_resolution_due = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    response_sla_breached = models.BooleanField(default=False)
    resolution_sla_breached = models.BooleanField(default=False)

    customer_rating = models.PositiveIntegerField(
        null=True, blank=True, choices=[(i, i) for i in range(1, 6)]
    )
    customer_feedback = models.TextField(null=True, blank=True)
    feedback_submitted_at = models.DateTimeField(null=True, blank=True)

    tags = models.JSONField(default=list, blank=True)

    is_escalated = models.BooleanField(default=False)
    escalated_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escalated_tickets",
    )
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalation_reason = models.TextField(null=True, blank=True)

    internal_notes = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "support_tickets"
        indexes = [
            models.Index(fields=["brand", "status"]),
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["ticket_number"]),
            models.Index(fields=["sla_response_due"]),
            models.Index(fields=["sla_resolution_due"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Ticket {self.ticket_number} - {self.subject}"

    def save(self, *args, **kwargs):
        if not self.ticket_number:
            import random
            from datetime import datetime

            date_str = datetime.now().strftime("%Y%m%d")
            random_part = str(random.randint(10000, 99999))
            self.ticket_number = f"TKT-{date_str}-{random_part}"

        if not self.sla_response_due and self.category:
            self.sla_response_due = self.created_at + timedelta(
                hours=self.category.response_time_hours
            )

        if not self.sla_resolution_due and self.category:
            self.sla_resolution_due = self.created_at + timedelta(
                hours=self.category.resolution_time_hours
            )

        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        """Check if ticket is overdue for response or resolution"""
        now = timezone.now()
        if self.status == self.TicketStatus.OPEN and self.sla_response_due:
            return now > self.sla_response_due
        elif self.status in [
            self.TicketStatus.IN_PROGRESS,
            self.TicketStatus.PENDING_INTERNAL,
        ]:
            return now > self.sla_resolution_due if self.sla_resolution_due else False
        return False

    @property
    def response_time_minutes(self):
        """Calculate response time in minutes"""
        if self.first_response_at:
            delta = self.first_response_at - self.created_at
            return int(delta.total_seconds() / 60)
        return None

    @property
    def resolution_time_minutes(self):
        """Calculate resolution time in minutes"""
        if self.resolved_at:
            delta = self.resolved_at - self.created_at
            return int(delta.total_seconds() / 60)
        return None


class SupportMessage(models.Model):
    """Messages within support tickets"""

    class MessageType(models.TextChoices):
        CUSTOMER = "customer", "Customer Message"
        AGENT = "agent", "Agent Reply"
        SYSTEM = "system", "System Message"
        INTERNAL = "internal", "Internal Note"

    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="messages"
    )

    message_type = models.CharField(max_length=20, choices=MessageType.choices)
    sender = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="support_messages"
    )
    content = models.TextField()

    attachments = models.JSONField(default=list, blank=True)

    is_public = models.BooleanField(default=True)

    is_read_by_customer = models.BooleanField(default=False)
    is_read_by_agent = models.BooleanField(default=False)

    telegram_message_id = models.BigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_messages"
        indexes = [
            models.Index(fields=["ticket", "created_at"]),
            models.Index(fields=["sender", "created_at"]),
        ]
        ordering = ["created_at"]


class SupportAttachment(models.Model):
    """File attachments for support messages"""

    message = models.ForeignKey(
        SupportMessage, on_delete=models.CASCADE, related_name="file_attachments"
    )

    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="support_attachments/")
    file_size = models.PositiveIntegerField()
    mime_type = models.CharField(max_length=100)

    is_scanned = models.BooleanField(default=False)
    scan_result = models.CharField(max_length=50, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_attachments"


class SupportTemplate(models.Model):
    """Pre-defined response templates for agents"""

    class TemplateType(models.TextChoices):
        QUICK_RESPONSE = "quick_response", "Quick Response"
        RESOLUTION = "resolution", "Resolution Template"
        ESCALATION = "escalation", "Escalation Template"
        CLOSING = "closing", "Closing Template"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="support_templates"
    )

    name = models.CharField(max_length=100)
    template_type = models.CharField(max_length=20, choices=TemplateType.choices)
    subject_template = models.CharField(max_length=255, null=True, blank=True)
    content_template = models.TextField()

    category = models.ForeignKey(
        SupportCategory, on_delete=models.SET_NULL, null=True, blank=True
    )
    usage_count = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "support_templates"
        unique_together = ["brand", "name"]


class SupportKnowledgeBase(models.Model):
    """Knowledge base articles for self-service support"""

    class ArticleStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="knowledge_articles"
    )

    title = models.CharField(max_length=255)
    content = models.TextField()
    summary = models.TextField(null=True, blank=True)

    category = models.ForeignKey(
        SupportCategory, on_delete=models.SET_NULL, null=True, blank=True
    )
    tags = models.JSONField(default=list, blank=True)

    slug = models.SlugField(max_length=255)
    meta_description = models.TextField(null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=ArticleStatus.choices, default=ArticleStatus.DRAFT
    )
    is_featured = models.BooleanField(default=False)

    author = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="authored_articles"
    )
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_articles",
    )

    view_count = models.PositiveIntegerField(default=0)
    helpful_votes = models.PositiveIntegerField(default=0)
    not_helpful_votes = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "support_knowledge_base"
        unique_together = ["brand", "slug"]
        indexes = [
            models.Index(fields=["brand", "status"]),
            models.Index(fields=["category", "status"]),
        ]


class SupportMetrics(models.Model):
    """Daily support metrics for performance tracking"""

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="support_metrics"
    )
    agent = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="support_metrics",
        null=True,
        blank=True,
    )

    date = models.DateField()

    tickets_created = models.PositiveIntegerField(default=0)
    tickets_resolved = models.PositiveIntegerField(default=0)
    tickets_closed = models.PositiveIntegerField(default=0)

    avg_first_response_time_minutes = models.PositiveIntegerField(default=0)
    avg_resolution_time_minutes = models.PositiveIntegerField(default=0)

    sla_response_compliance_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )
    sla_resolution_compliance_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )

    avg_customer_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    total_ratings = models.PositiveIntegerField(default=0)

    active_tickets = models.PositiveIntegerField(default=0)
    messages_sent = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_metrics"
        unique_together = ["brand", "agent", "date"]
        ordering = ["-date"]


class SupportEscalation(models.Model):
    """Track ticket escalations"""

    class EscalationType(models.TextChoices):
        SLA_BREACH = "sla_breach", "SLA Breach"
        CUSTOMER_REQUEST = "customer_request", "Customer Request"
        AGENT_ESCALATION = "agent_escalation", "Agent Escalation"
        SUPERVISOR_REVIEW = "supervisor_review", "Supervisor Review"
        TECHNICAL_COMPLEXITY = "technical_complexity", "Technical Complexity"

    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="escalations"
    )

    escalation_type = models.CharField(max_length=20, choices=EscalationType.choices)
    escalated_from = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="escalations_made"
    )
    escalated_to = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="escalations_received"
    )

    reason = models.TextField()
    resolution = models.TextField(null=True, blank=True)

    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_escalations"
        ordering = ["-created_at"]


class SupportAutomation(models.Model):
    """Automated support rules and triggers"""

    class TriggerType(models.TextChoices):
        TICKET_CREATED = "ticket_created", "Ticket Created"
        TICKET_UPDATED = "ticket_updated", "Ticket Updated"
        MESSAGE_RECEIVED = "message_received", "Message Received"
        SLA_WARNING = "sla_warning", "SLA Warning"
        CUSTOMER_RATING = "customer_rating", "Customer Rating"

    class ActionType(models.TextChoices):
        AUTO_ASSIGN = "auto_assign", "Auto Assign"
        SEND_TEMPLATE = "send_template", "Send Template"
        ESCALATE = "escalate", "Escalate Ticket"
        ADD_TAG = "add_tag", "Add Tag"
        CHANGE_PRIORITY = "change_priority", "Change Priority"
        NOTIFY_MANAGER = "notify_manager", "Notify Manager"

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="support_automations"
    )

    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)

    trigger_type = models.CharField(max_length=20, choices=TriggerType.choices)
    trigger_conditions = models.JSONField(default=dict, blank=True)

    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    action_parameters = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)

    execution_count = models.PositiveIntegerField(default=0)
    last_executed = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "support_automations"
        unique_together = ["brand", "name"]
