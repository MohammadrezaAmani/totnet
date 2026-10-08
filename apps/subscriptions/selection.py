"""Pure catalog facets: every option corresponds to a real cached plan."""

from decimal import Decimal

from .models import SubscriptionPlan


def traffic_key(plan):
    if plan.plan_type == SubscriptionPlan.PlanType.UNLIMITED:
        return "u"
    if plan.traffic_limit_gb is None:
        return "n"
    return "v" + format(Decimal(str(plan.traffic_limit_gb)).normalize(), "f")


def traffic_label(key):
    if key == "u":
        return "نامحدود"
    if key == "n":
        return "بدون حجم مشخص"
    return key[1:] + " گیگابایت"


def traffic_options(plans):
    keys = {traffic_key(plan) for plan in plans}
    return sorted(
        keys,
        key=lambda key: (
            0 if key == "u" else 2 if key == "n" else 1,
            Decimal(key[1:]) if key.startswith("v") else 0,
        ),
    )


def traffic_ranges(keys):
    limits = (
        Decimal(0),
        Decimal(50),
        Decimal(100),
        Decimal(200),
        Decimal(500),
        Decimal("Infinity"),
    )
    labels = (
        "تا ۵۰ گیگ",
        "۵۰ تا ۱۰۰ گیگ",
        "۱۰۰ تا ۲۰۰ گیگ",
        "۲۰۰ تا ۵۰۰ گیگ",
        "بیش از ۵۰۰ گیگ",
    )
    return [
        (
            str(index),
            labels[index],
            [
                key
                for key in keys
                if key.startswith("v")
                and limits[index] < Decimal(key[1:]) <= limits[index + 1]
            ],
        )
        for index in range(5)
        if any(
            key.startswith("v")
            and limits[index] < Decimal(key[1:]) <= limits[index + 1]
            for key in keys
        )
    ]


def duration_key(plan):
    if not plan.duration_value or not plan.duration_unit:
        return "n"
    return f"{plan.duration_unit[0]}{plan.duration_value}"


def duration_label(key):
    if key == "n":
        return "بدون محدودیت زمانی مشخص"
    return f"{key[1:]} { {'d': 'روز', 'w': 'هفته', 'm': 'ماه', 'y': 'سال'}[key[0]] }"


def duration_options(plans):
    return sorted(
        {duration_key(plan) for plan in plans},
        key=lambda key: (
            int(key[1:]) * {"d": 1, "w": 7, "m": 30, "y": 365}[key[0]]
            if key != "n"
            else float("inf"),
            key,
        ),
    )


def matching_plans(plans, picker, *, through="duration"):
    traffic = set(picker.get("traffic", []))
    users = set(picker.get("users", []))
    durations = set(picker.get("durations", []))
    return [
        plan
        for plan in plans
        if (not traffic or traffic_key(plan) in traffic)
        and (through == "traffic" or not users or plan.max_users in users)
        and (through != "duration" or not durations or duration_key(plan) in durations)
    ]


def recommended_plans(plans, limit=4):
    """Rank real purchases, then admin priority; avoid repeating the same bundle."""
    ranked = sorted(
        plans,
        key=lambda plan: (
            -getattr(plan, "purchase_count", 0),
            not plan.is_featured,
            plan.display_order,
            plan.discounted_price,
            plan.pk,
        ),
    )
    chosen, seen = [], set()
    for plan in ranked:
        # Trial freebies are available in filters but do not occupy paid recommendations.
        if plan.discounted_price <= 0:
            continue
        key = (traffic_key(plan), duration_key(plan))
        if key not in seen:
            seen.add(key)
            chosen.append(plan)
        if len(chosen) == limit:
            break
    return chosen


def volume_user_price_is_constant(plans):
    """Compare user variants of the same volume, duration, currency and family."""
    prices = {}
    for plan in plans:
        if not traffic_key(plan).startswith("v"):
            continue
        key = (
            traffic_key(plan),
            duration_key(plan),
            plan.currency,
            plan.upstream_group_id,
        )
        prices.setdefault(key, {}).setdefault(plan.max_users, set()).add(
            plan.discounted_price
        )
    return all(
        len({price for entries in variants.values() for price in entries}) <= 1
        for variants in prices.values()
        if len(variants) > 1
    )
