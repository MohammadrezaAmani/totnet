"""Cached plan snapshots for bot browsing; checkout always rereads the database."""

import logging
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils.dateparse import parse_datetime

from .models import SubscriptionPlan

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
    "is_featured",
    "display_order",
    "discount_percentage",
    "offer_expires_at",
    "upstream_group_id",
)


def catalog_key(brand_id):
    return f"subscription-plan-catalog:v2:{brand_id}"


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
        .order_by("display_order", "price", "pk")
        .values(*FIELDS, "purchase_count")
    )
    for row in rows:
        for key in ("price", "traffic_limit_gb", "discount_percentage"):
            if row[key] is not None:
                row[key] = str(row[key])
        if row["offer_expires_at"]:
            row["offer_expires_at"] = row["offer_expires_at"].isoformat()
    try:
        cache.set(
            catalog_key(brand_id), rows, timeout=settings.PLAN_CATALOG_CACHE_TTL_SECONDS
        )
    except Exception as exc:
        logger.warning(
            "Plan cache write failed for brand %s (%s)", brand_id, type(exc).__name__
        )
    return rows


async def get_cached_plans(brand_id):
    try:
        rows = await cache.aget(catalog_key(brand_id))
    except Exception as exc:
        logger.warning(
            "Plan cache read failed for brand %s (%s)", brand_id, type(exc).__name__
        )
        rows = None
    if rows is None:
        rows = await sync_to_async(refresh_plan_catalog)(brand_id)
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
        plans.append(plan)
    return plans
