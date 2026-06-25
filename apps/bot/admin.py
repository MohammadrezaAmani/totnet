"""
Admin configuration for bot app
"""

from django.contrib import admin

from .models import BotAnalytics, BotCommand, BotKeyboard, BotMessage, BotState


@admin.register(BotState)
class BotStateAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "brand",
        "current_state",
        "last_message_id",
        "created_at",
        "updated_at",
    )
    list_filter = ("current_state", "brand")
    search_fields = ("user__username", "brand__name")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BotMessage)
class BotMessageAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "brand",
        "telegram_message_id",
        "message_type",
        "handler_name",
        "is_edited",
        "is_deleted",
        "sent_at",
    )
    list_filter = ("message_type", "is_edited", "is_deleted", "brand")
    search_fields = ("user__username", "brand__name", "telegram_message_id", "content")
    date_hierarchy = "sent_at"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BotKeyboard)
class BotKeyboardAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "context", "is_active", "created_at", "updated_at")
    list_filter = ("is_active", "brand")
    search_fields = ("brand__name", "name", "description")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BotCommand)
class BotCommandAdmin(admin.ModelAdmin):
    list_display = ("brand", "command", "description", "handler_function", "is_active")
    list_filter = ("is_active", "brand")
    search_fields = ("brand__name", "command", "handler_function")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs


@admin.register(BotAnalytics)
class BotAnalyticsAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "date",
        "active_users",
        "new_users",
        "messages_received",
        "messages_sent",
        "errors_count",
    )
    list_filter = ("brand", "date")
    search_fields = ("brand__name",)
    date_hierarchy = "date"
    readonly_fields = ("created_at",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            return qs.filter(brand__in=request.user.admin_brands.all())
        return qs
