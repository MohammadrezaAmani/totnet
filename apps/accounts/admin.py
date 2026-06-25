"""
Admin configuration for accounts app
"""

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import AuditLog, Permission, Role, User, UserProfile, UserRole, UserSession


class UserCreationForm(forms.ModelForm):
    """A form for creating new users"""

    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(
        label="Password confirmation", widget=forms.PasswordInput
    )

    class Meta:
        model = User
        fields = (
            "username",
            "telegram_id",
            "full_name",
            "user_type",
            "brand",
            "language",
        )

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Passwords don't match")
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    """A form for updating users"""

    password = ReadOnlyPasswordHashField()

    class Meta:
        model = User
        fields = (
            "username",
            "password",
            "telegram_id",
            "full_name",
            "user_type",
            "brand",
            "language",
            "is_active",
            "is_staff",
            "is_superuser",
        )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm

    list_display = (
        "username",
        "telegram_id",
        "full_name",
        "user_type",
        "brand",
        "level",
        "wallet_balance",
        "reward_points",
        "is_verified",
        "is_active",
        "created_at",
    )
    list_filter = (
        "user_type",
        "brand",
        "is_active",
        "is_verified",
        "is_staff",
        "is_superuser",
        "created_at",
    )
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            "Personal Info",
            {
                "fields": (
                    "telegram_id",
                    "full_name",
                    "email",
                    "phone_number",
                    "avatar",
                    "bio",
                    "birth_date",
                    
                )
            },
        ),
        ("Brand & Platform", {"fields": ("brand", "admin_brands", "user_type", "language")}),
        (
            "Referral System",
            {"fields": ("referral_code", "referred_by", "referral_count")},
        ),
        (
            "Wallet & Rewards",
            {
                "fields": (
                    "wallet_balance",
                    "reward_points",
                    "level",
                    "experience_points",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Statistics", {"fields": ("total_purchases", "total_spent", "last_activity")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "telegram_id",
                    "password1",
                    "password2",
                    "brand",
                    "user_type",
                ),
            },
        ),
    )
    search_fields = ("username", "telegram_id", "full_name", "email", "phone_number")
    ordering = ("-created_at",)
    list_select_related = ("brand",)
    readonly_fields = (
        "referral_code",
        "referral_count",
        "wallet_balance",
        "reward_points",
        "experience_points",
        "total_purchases",
        "total_spent",
        "last_activity",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "country",
        "city",
        "kyc_status",
        "notification_enabled",
        "created_at",
    )
    list_filter = (
        "kyc_status",
        "notification_enabled",
        "marketing_emails",
        "newsletter_subscription",
        "created_at",
    )
    search_fields = ("user__username", "user__telegram_id", "country", "city")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "platform",
        "ip_address",
        "is_active",
        "created_at",
        "last_activity",
        "expires_at",
    )
    list_filter = ("platform", "is_active", "created_at")
    search_fields = ("user__username", "user__telegram_id", "ip_address")
    readonly_fields = ("created_at", "last_activity", "expires_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "brand",
        "action_type",
        "object_type",
        "object_id",
        "timestamp",
    )
    list_filter = ("action_type", "object_type", "timestamp", "brand")
    search_fields = ("user__username", "brand__name", "object_id", "description")
    date_hierarchy = "timestamp"
    readonly_fields = ("timestamp", "old_data", "new_data", "extra_data")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("name", "codename", "permission_type", "resource")
    list_filter = ("permission_type", "resource")
    search_fields = ("name", "codename")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "is_active", "permissions_count")
    list_filter = ("is_active", "brand")
    search_fields = ("name", "description")
    filter_horizontal = ("permissions",)

    def permissions_count(self, obj):
        return obj.permissions.count()

    permissions_count.short_description = "Permissions"


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "brand", "assigned_by", "is_active", "assigned_at")
    list_filter = ("is_active", "assigned_at", "brand")
    search_fields = ("user__username", "role__name", "brand__name")
    readonly_fields = ("assigned_at",)
