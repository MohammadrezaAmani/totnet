"""
Admin configuration for bot app
"""

from django.contrib import admin

from .models import BehaviorEvent, BotAnalytics, BotCommand, BotKeyboard, BotMessage, BotState


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


@admin.register(BehaviorEvent)
class BehaviorEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "brand", "user", "event", "value", "outcome")
    list_filter = ("brand", "event", "outcome", "created_at")
    search_fields = ("user__username", "value")
    date_hierarchy = "created_at"
    readonly_fields = tuple(field.name for field in BehaviorEvent._meta.fields)
    change_list_template = "admin/bot/behavior_report.html"
    actions = ("export_events",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("user", "brand")
        return (
            qs
            if request.user.is_superuser
            else qs.filter(brand__in=request.user.admin_brands.all())
        )

    def changelist_view(self, request, extra_context=None):
        from apps.brands.models import Brand
        from .analytics import gamification_report

        brands = (
            Brand.objects.all()
            if request.user.is_superuser
            else request.user.admin_brands.all()
        )
        context = dict(extra_context or {})
        context["gamification_reports"] = [
            (brand, gamification_report(brand.pk)) for brand in brands
        ]
        return super().changelist_view(request, context)

    @admin.action(description="دانلود CSV رخدادهای انتخاب‌شده")
    def export_events(self, request, queryset):
        import csv
        import json
        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="behavior-events.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(["time", "user_id", "event", "value", "outcome", "metadata"])
        for event in queryset.iterator():
            writer.writerow(
                [
                    event.created_at.isoformat(),
                    event.user_id,
                    event.event,
                    event.value,
                    event.outcome,
                    json.dumps(event.metadata, ensure_ascii=False),
                ]
            )
        return response
