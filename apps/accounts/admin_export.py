"""Tenant-scoped, filter-preserving emergency customer export."""

from django.contrib import admin
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.utils import timezone

from apps.subscriptions.models import Subscription, SubscriptionPlan
from .models import User
from .xlsx_export import users_xlsx_response


def active_owned_subscriptions(now=None):
    now = now or timezone.now()
    return Subscription.objects.filter(status=Subscription.SubscriptionStatus.ACTIVE).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )


class ActiveSubscriptionFilter(admin.SimpleListFilter):
    title = "اشتراک معتبر در حال حاضر"
    parameter_name = "has_active_subscription"

    def lookups(self, request, model_admin):
        return (("yes", "اشتراک فعال دارد"), ("no", "اشتراک فعال ندارد"))

    def queryset(self, request, queryset):
        if self.value() not in {"yes", "no"}:
            return queryset
        owned = active_owned_subscriptions().filter(
            owner_id=OuterRef("pk"), brand_id=OuterRef("brand_id")
        )
        return queryset.filter(Exists(owned) if self.value() == "yes" else ~Exists(owned))


class ActiveServiceFilter(admin.SimpleListFilter):
    title = "نوع سرویس فعال"
    parameter_name = "active_service"

    def lookups(self, request, model_admin):
        return SubscriptionPlan.ServiceCategory.choices

    def queryset(self, request, queryset):
        if self.value() not in SubscriptionPlan.ServiceCategory.values:
            return queryset
        owned = active_owned_subscriptions().filter(
            owner_id=OuterRef("pk"), brand_id=OuterRef("brand_id"),
            plan__service_category=self.value(),
        )
        return queryset.filter(Exists(owned))


def export_customers(queryset):
    """Export only the selected tenant-scoped customers, without N+1 queries."""
    now = timezone.now()
    owned = active_owned_subscriptions(now).select_related("plan").order_by("expires_at", "pk")
    users = queryset.filter(user_type=User.UserType.CUSTOMER).select_related("brand").prefetch_related(
        Prefetch("owned_subscriptions", queryset=owned, to_attr="export_active_subscriptions")
    )

    def rows():
        for user in users.iterator(chunk_size=300):
            subscriptions = user.export_active_subscriptions
            categories = dict(SubscriptionPlan.ServiceCategory.choices)
            services = ", ".join(sorted({categories.get(sub.plan.service_category, sub.plan.service_category) for sub in subscriptions}))
            expirations = [sub.expires_at for sub in subscriptions if sub.expires_at]
            soonest = min(expirations).strftime("%Y-%m-%d %H:%M") if expirations else ""
            yield (
                user.phone_number or "", str(user.telegram_id or ""), user.full_name or user.get_full_name(),
                user.username, user.brand.name if user.brand else "",
                "دارای اشتراک فعال" if subscriptions else "بدون اشتراک فعال",
                len(subscriptions), services, soonest,
                timezone.localtime(user.created_at).strftime("%Y-%m-%d %H:%M") if user.created_at else "",
            )

    return users_xlsx_response(rows())
