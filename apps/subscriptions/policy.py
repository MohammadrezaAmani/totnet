"""Single source of truth for catalog restrictions and customer-facing gifts.

Purchase policy is intentionally enforced at the order entry points as well as
in the keyboard: a pre-existing Telegram callback cannot bypass it.
"""

import re
from decimal import Decimal

from django.conf import settings

from .models import SubscriptionPlan

TEMPORARILY_UNAVAILABLE_TEXT = "🔴 زمان درخواستی موقتاً غیرفعال است. لطفاً اشتراک ۱ ماهه را انتخاب کنید."
_GIFT_PATTERN = re.compile(r"\+\s*([0-9۰-۹٠-٩]{1,3})\s*d\b", flags=re.IGNORECASE)
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def gift_days_from_name(*names):
    """Extract panel's `+3d` convention; reject malformed / enormous values."""
    for name in names:
        match = _GIFT_PATTERN.search(str(name or ""))
        if match:
            days = int(match.group(1).translate(_DIGITS))
            if 1 <= days <= 365:
                return days
    return 0


def plan_gift_days(plan):
    return int(getattr(plan, "gift_days", 0) or 0) or gift_days_from_name(
        getattr(plan, "upstream_plan_name", ""), getattr(plan, "name", "")
    )


def temporary_duration_blocked(plan):
    """2/3 month subscriptions remain browseable, but cannot be ordered.

    Controlled with an env-backed feature flag for re-enabling after catalog QA.
    """
    unit, value = plan.duration_unit, plan.duration_value
    months = value if unit == SubscriptionPlan.DurationUnit.MONTHS else (
        value // 30 if unit == SubscriptionPlan.DurationUnit.DAYS and value in (60, 90)
        else value // 4 if unit == SubscriptionPlan.DurationUnit.WEEKS and value in (8, 12)
        else 0
    )
    if plan.service_category == SubscriptionPlan.ServiceCategory.NORMAL:
        return months == 3
    if plan.service_category == SubscriptionPlan.ServiceCategory.ROYAL:
        return False
    return getattr(settings, "TEMP_DISABLE_2_3_MONTH_PLANS", True) and months in (2, 3)


def blocked_duration_key(key, category=None):
    if category == SubscriptionPlan.ServiceCategory.NORMAL:
        return key in {"m3", "d90", "w12"}
    if category == SubscriptionPlan.ServiceCategory.ROYAL:
        return False
    if not getattr(settings, "TEMP_DISABLE_2_3_MONTH_PLANS", True):
        return False
    return key in {"m2", "m3", "d60", "d90", "w8", "w12"}


def effective_reward_percent(order, configured_percent):
    """Resolve persisted exact-type, category-wide, then referral-program rate."""
    from apps.referrals.models import ServiceRewardRate
    from apps.referrals.rate_policy import checked_percent

    plan = order.plan
    candidates = ServiceRewardRate.objects.filter(
        brand_id=order.brand_id, service_category=plan.service_category,
        plan_type__in=(plan.plan_type, "all"), is_active=True,
    )
    exact = candidates.filter(plan_type=plan.plan_type).first()
    category = candidates.filter(plan_type="all").first()
    if exact or category:
        return checked_percent((exact or category).percent)
    if configured_percent is None:
        raise ValueError("درصد پایه معرفی برای برند تنظیم نشده است.")
    rate = Decimal(str(configured_percent))
    if rate == 0:
        # An absent/disabled referral program must never create a payout.
        return rate
    return checked_percent(rate)
