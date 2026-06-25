"""
User Account Models for Multi-Tenant VPN Platform
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Extended User model with multi-tenant support"""

    class UserType(models.TextChoices):
        PLATFORM_OWNER = "platform_owner", "Platform Owner"
        BRAND_MANAGER = "brand_manager", "Brand Manager"
        BRAND_ADMIN = "brand_admin", "Brand Admin"
        SUPPORT_AGENT = "support_agent", "Support Agent"
        CUSTOMER = "customer", "Customer"

    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    full_name = models.CharField(max_length=255, null=True, blank=True)
    user_type = models.CharField(
        max_length=20, choices=UserType.choices, default=UserType.CUSTOMER
    )

    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    bio = models.TextField(max_length=500, null=True, blank=True)
    birth_date = models.DateField(null=True, blank=True)

    referral_code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    referred_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referrals",
    )
    referral_count = models.PositiveIntegerField(default=0)

    admin_brands = models.ManyToManyField(
        "brands.Brand", blank=True, related_name="admin_users"
    )

    wallet_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    reward_points = models.PositiveIntegerField(default=0)
    level = models.PositiveIntegerField(default=1)
    experience_points = models.PositiveIntegerField(default=0)

    last_activity = models.DateTimeField(default=timezone.now)
    is_verified = models.BooleanField(default=False)
    verification_code = models.CharField(max_length=10, null=True, blank=True)

    brand = models.ForeignKey(
        "brands.Brand",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="users",
    )

    language = models.CharField(max_length=10, default="en")

    total_purchases = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"
        indexes = [
            models.Index(fields=["telegram_id"]),
            models.Index(fields=["referral_code"]),
            models.Index(fields=["brand", "user_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.username} ({self.get_user_type_display()})"

    def save(self, *args, **kwargs):
        if not self.referral_code and self.user_type == self.UserType.CUSTOMER:
            import random
            import string

            self.referral_code = "".join(
                random.choices(string.ascii_uppercase + string.digits, k=8)
            )
        super().save(*args, **kwargs)


class UserProfile(models.Model):
    """Extended profile information for users"""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    country = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    timezone = models.CharField(max_length=50, default="UTC")

    notification_enabled = models.BooleanField(default=True)
    marketing_emails = models.BooleanField(default=True)
    newsletter_subscription = models.BooleanField(default=False)

    national_id = models.CharField(max_length=50, null=True, blank=True)
    id_document = models.FileField(upload_to="documents/", null=True, blank=True)
    kyc_status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="pending",
    )

    preferred_payment_method = models.CharField(max_length=50, null=True, blank=True)
    default_subscription_duration = models.IntegerField(default=30)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profiles"


class UserSession(models.Model):
    """Track user sessions across different platforms"""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")

    session_key = models.CharField(max_length=255, unique=True)
    platform = models.CharField(
        max_length=20,
        choices=[
            ("telegram", "Telegram"),
            ("web", "Web"),
            ("api", "API"),
        ],
    )

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    device_info = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "user_sessions"
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["expires_at"]),
        ]


class AuditLog(models.Model):
    """Comprehensive audit logging for all user actions"""

    class ActionType(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        PURCHASE = "purchase", "Purchase"
        PAYMENT = "payment", "Payment"
        REFERRAL = "referral", "Referral"
        SUPPORT = "support", "Support"
        ADMIN = "admin", "Admin Action"

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, null=True, blank=True
    )

    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100, null=True, blank=True)

    old_data = models.JSONField(null=True, blank=True)
    new_data = models.JSONField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    description = models.TextField(null=True, blank=True)
    extra_data = models.JSONField(default=dict, blank=True)

    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_logs"
        indexes = [
            models.Index(fields=["user", "timestamp"]),
            models.Index(fields=["brand", "timestamp"]),
            models.Index(fields=["action_type", "timestamp"]),
            models.Index(fields=["object_type", "timestamp"]),
        ]
        ordering = ["-timestamp"]


class Permission(models.Model):
    """Custom permission system for multi-tenant access control"""

    class PermissionType(models.TextChoices):
        VIEW = "view", "View"
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        MANAGE = "manage", "Manage"
        ADMIN = "admin", "Admin"

    name = models.CharField(max_length=100)
    codename = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)
    permission_type = models.CharField(max_length=20, choices=PermissionType.choices)

    resource = models.CharField(max_length=100)

    class Meta:
        db_table = "custom_permissions"


class Role(models.Model):
    """Role-based access control"""

    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    permissions = models.ManyToManyField(Permission, blank=True)

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, null=True, blank=True
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "roles"
        unique_together = ["name", "brand"]


class UserRole(models.Model):
    """Assign roles to users within specific brands"""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    brand = models.ForeignKey("brands.Brand", on_delete=models.CASCADE)

    assigned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="role_assignments"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "user_roles"
        unique_together = ["user", "role", "brand"]
