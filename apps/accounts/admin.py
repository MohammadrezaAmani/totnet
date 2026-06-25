"""
Admin configuration for accounts app
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.utils import timezone
from django.utils.html import format_html

from .models import AuditLog, Permission, Role, User, UserProfile, UserSession, UserRole


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


# Inline Admins
class UserProfileInline(admin.StackedInline):
    """Inline for user profile"""

    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"
    fk_name = "user"
    fields = (
        "country",
        "city",
        "timezone",
        "notification_enabled",
        "marketing_emails",
        "newsletter_subscription",
        "kyc_status",
    )


class UserSessionInline(admin.TabularInline):
    """Inline for user sessions"""

    model = UserSession
    extra = 0
    readonly_fields = ("session_key", "platform", "ip_address", "created_at", "last_activity", "expires_at")
    fields = ("platform", "ip_address", "is_active", "created_at", "last_activity", "expires_at")
    can_delete = True

    def has_add_permission(self, request, obj=None):
        return False


class UserRoleInline(admin.TabularInline):
    """Inline for user roles"""

    model = UserRole
    extra = 0
    fields = ("role", "brand", "is_active", "assigned_at")
    readonly_fields = ("assigned_at",)
    raw_id_fields = ("role", "brand")


class AuditLogInline(admin.TabularInline):
    """Inline for audit logs (readonly)"""

    model = AuditLog
    extra = 0
    readonly_fields = ("action_type", "object_type", "object_id", "description", "timestamp")
    fields = ("action_type", "object_type", "object_id", "description", "timestamp")
    can_delete = False
    max_num = 10

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).order_by("-timestamp")[:10]


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
    inlines = [UserProfileInline, UserRoleInline, UserSessionInline, AuditLogInline]

    actions = [
        "activate_users",
        "deactivate_users",
        "verify_users",
        "unverify_users",
        "reset_passwords",
        "add_reward_points",
        "clear_wallet",
        "export_users_csv",
    ]

    def activate_users(self, request, queryset):
        """Activate selected users"""
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Successfully activated {updated} users.", messages.SUCCESS)

    activate_users.short_description = "Activate selected users"

    def deactivate_users(self, request, queryset):
        """Deactivate selected users"""
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Successfully deactivated {updated} users.", messages.SUCCESS)

    deactivate_users.short_description = "Deactivate selected users"

    def verify_users(self, request, queryset):
        """Verify selected users"""
        updated = queryset.update(is_verified=True)
        self.message_user(request, f"Successfully verified {updated} users.", messages.SUCCESS)

    verify_users.short_description = "Verify selected users"

    def unverify_users(self, request, queryset):
        """Unverify selected users"""
        updated = queryset.update(is_verified=False)
        self.message_user(request, f"Successfully unverified {updated} users.", messages.SUCCESS)

    unverify_users.short_description = "Unverify selected users"

    def reset_passwords(self, request, queryset):
        """Reset passwords for selected users"""
        count = 0
        for user in queryset:
            user.set_password(None)
            user.save()
            count += 1
        self.message_user(request, f"Successfully reset passwords for {count} users.", messages.SUCCESS)

    reset_passwords.short_description = "Reset passwords for selected users"

    def add_reward_points(self, request, queryset):
        """Add reward points to selected users"""
        from django.contrib.admin.views.decorators import staff_member_required
        from django.shortcuts import render
        from django import forms

        class PointsForm(forms.Form):
            points = forms.IntegerField(min_value=1, label="Points to add")

        form = PointsForm(request.POST) if request.POST.get("points") else None

        if form and form.is_valid():
            points = form.cleaned_data["points"]
            updated = 0
            for user in queryset:
                user.reward_points += points
                user.save()
                updated += 1
            self.message_user(request, f"Added {points} points to {updated} users.", messages.SUCCESS)
            return

        self.message_user(request, "Points added successfully.", messages.SUCCESS)

    add_reward_points.short_description = "Add reward points to selected users"

    def clear_wallet(self, request, queryset):
        """Clear wallet balance for selected users"""
        updated = queryset.update(wallet_balance=0)
        self.message_user(request, f"Cleared wallet for {updated} users.", messages.SUCCESS)

    clear_wallet.short_description = "Clear wallet balance"

    def export_users_csv(self, request, queryset):
        """Export selected users to CSV"""
        import csv
        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="users_export.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Username", "Telegram ID", "Full Name", "User Type", "Brand", "Wallet Balance", "Created At"]
        )
        for user in queryset:
            writer.writerow(
                [
                    user.username,
                    user.telegram_id,
                    user.full_name,
                    user.get_user_type_display(),
                    user.brand.name if user.brand else "",
                    user.wallet_balance,
                    user.created_at,
                ]
            )
        return response

    export_users_csv.short_description = "Export selected users to CSV"

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
    actions = ["terminate_sessions"]

    def terminate_sessions(self, request, queryset):
        """Terminate selected sessions"""
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Terminated {updated} sessions.", messages.SUCCESS)

    terminate_sessions.short_description = "Terminate selected sessions"


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
    actions = ["export_logs_csv"]

    def export_logs_csv(self, request, queryset):
        """Export audit logs to CSV"""
        import csv
        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="audit_logs_export.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["User", "Brand", "Action Type", "Object Type", "Object ID", "Description", "Timestamp"]
        )
        for log in queryset:
            writer.writerow(
                [
                    log.user.username if log.user else "",
                    log.brand.name if log.brand else "",
                    log.get_action_type_display(),
                    log.object_type,
                    log.object_id or "",
                    log.description or "",
                    log.timestamp,
                ]
            )
        return response

    export_logs_csv.short_description = "Export selected logs to CSV"


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
