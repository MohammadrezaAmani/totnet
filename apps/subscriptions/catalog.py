"""Cached plan snapshots for bot browsing; checkout always rereads the database."""

import logging
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils.dateparse import parse_datetime

from apps.brands.models import Brand

from .models import SubscriptionPlan
from .presentation import connectix_service_category

logger = logging.getLogger(__name__)
FIELDS = (
    "id",
    "name",
    "service_category",
    "plan_type",
    "price",
    "currency",
    "duration_value",
    "duration_unit",
    "traffic_limit_gb",
    "max_users",
    "gift_days",
    "is_featured",
    "display_order",
    "discount_percentage",
    "offer_expires_at",
    "upstream_group_id",
    "upstream_group_name",
)


def catalog_key(brand_id):
    return f"subscription-plan-catalog:v6:{brand_id}"


def invalidate_plan_catalog(brand_id):
    try:
        cache.delete(catalog_key(brand_id))
    except Exception as exc:
        logger.warning(
            "Plan cache invalidation failed for brand %s (%s)",
            brand_id,
            type(exc).__name__,
        )


def refresh_plan_catalog(brand_id):
    rows = list(
        SubscriptionPlan.objects.filter(
            brand_id=brand_id,
            is_active=True,
            is_visible=True,
        )
        .filter(Q(vpn_provider__isnull=True) | Q(vpn_provider__status="active"))
        .annotate(
            purchase_count=Count(
                "orders",
                filter=Q(orders__brand_id=brand_id, orders__status="completed")
                & ~Q(orders__order_type="reward_redemption"),
            )
        )
        .order_by("display_order", "pk")
        .values(*FIELDS, "purchase_count", "vpn_provider__provider_type")
    )
    for row in rows:
        provider_type = row.pop("vpn_provider__provider_type")
        if provider_type == "connectix" and row["upstream_group_id"]:
            # Older imports stored every provider group as "normal". Resolve the
            # panel's default group when reading too, before category filtering.
            row["service_category"] = connectix_service_category(
                row["upstream_group_name"], row["service_category"]
            )
        for key in ("price", "traffic_limit_gb", "discount_percentage"):
            if row[key] is not None:
                row[key] = str(row[key])
        if row["offer_expires_at"]:
            row["offer_expires_at"] = row["offer_expires_at"].isoformat()
    # Snapshot the brand-wide free-plan switch with the catalogue, so a hot
    # cache read needs no extra DB query. Brand post-save invalidates this key.
    show_free_plans = Brand.objects.filter(pk=brand_id, show_free_plans=True).exists()
    snapshot = {"rows": rows, "show_free_plans": show_free_plans}
    try:
        cache.set(
            catalog_key(brand_id), snapshot, timeout=settings.PLAN_CATALOG_CACHE_TTL_SECONDS
        )
    except Exception as exc:
        logger.warning(
            "Plan cache write failed for brand %s (%s)", brand_id, type(exc).__name__
        )
    return rows


async def get_cached_plans(brand_id, *, refresh=False):
    snapshot = None
    if not refresh:
        try:
            snapshot = await cache.aget(catalog_key(brand_id))
        except Exception as exc:
            logger.warning(
                "Plan cache read failed for brand %s (%s)", brand_id, type(exc).__name__
            )
    if snapshot is None:
        # Refresh only once. Redis may be down; serving the correct live catalogue
        # is preferable to crashing (the next read can retry the cache).
        rows = await sync_to_async(refresh_plan_catalog)(brand_id)
        try:
            snapshot = await cache.aget(catalog_key(brand_id))
        except Exception as exc:
            logger.warning("Plan cache read after refresh failed for brand %s (%s)",
                           brand_id, type(exc).__name__)
        if snapshot is None:
            show_free_plans = await Brand.objects.filter(
                pk=brand_id, show_free_plans=True
            ).aexists()
            snapshot = {"rows": rows, "show_free_plans": show_free_plans}
    rows = snapshot["rows"]
    show_free_plans = snapshot["show_free_plans"]
    plans = []
    for row in rows:
        values = dict(row)
        purchase_count = values.pop("purchase_count", 0)
        for key in ("price", "traffic_limit_gb", "discount_percentage"):
            if values[key] is not None:
                values[key] = Decimal(values[key])
        if values["offer_expires_at"]:
            values["offer_expires_at"] = parse_datetime(values["offer_expires_at"])
        # Detached instances contain scalar fields only; never cache ORM objects.
        plan = SubscriptionPlan(**values)
        plan.purchase_count = purchase_count
        if show_free_plans or plan.discounted_price > 0:
            plans.append(plan)
    return plans


async def is_plan_visible_in_bot(plan, brand_id):
    """Apply free-plan visibility to selections from older bot keyboards too."""
    return plan.discounted_price > 0 or await Brand.objects.filter(
        pk=brand_id, show_free_plans=True
    ).aexists()
