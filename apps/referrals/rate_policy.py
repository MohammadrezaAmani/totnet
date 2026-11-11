"""Persisted, brand-scoped referral rates and immutable challenge snapshots."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from .models import ReferralProgram, ServiceRewardRate


def checked_percent(value, *, field="درصد پاداش"):
    try:
        percent = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{field} تنظیم نشده است.") from exc
    if not percent.is_finite() or not Decimal("0") < percent <= Decimal("100"):
        raise ValidationError(f"{field} باید بیشتر از صفر و حداکثر صد باشد.")
    return percent


def challenge_rate_snapshot(brand_id):
    """Capture every active override and the configured base rate once, at activation."""
    program = ReferralProgram.objects.filter(brand_id=brand_id, is_active=True).first()
    if not program:
        raise ValidationError("برنامه معرفی فعال برای این برند تنظیم نشده است.")
    base = checked_percent(program.purchase_reward_percent)
    overrides = list(
        ServiceRewardRate.objects.filter(brand_id=brand_id, is_active=True)
        .values("service_category", "plan_type", "percent")
    )
    return {
        "base_percent": str(base),
        "overrides": [
            {
                "service_category": row["service_category"],
                "plan_type": row["plan_type"],
                "percent": str(checked_percent(row["percent"])),
            }
            for row in overrides
        ],
    }


def snapshot_percent(plan, snapshot):
    """Resolve an exact plan-type override before a category-wide override."""
    if not snapshot or "base_percent" not in snapshot:
        raise ValidationError("نرخ ثبت‌شده چالش موجود نیست.")
    matches = [row for row in snapshot.get("overrides", [])
               if row["service_category"] == plan.service_category]
    exact = next((row for row in matches if row["plan_type"] == plan.plan_type), None)
    category = next((row for row in matches if row["plan_type"] == "all"), None)
    return checked_percent((exact or category or {"percent": snapshot["base_percent"]})["percent"])


def validate_challenge_configuration(program):
    if not program.duration_days or program.duration_days < 1:
        raise ValidationError("مدت چالش باید در تنظیمات ثبت شود.")
    if program.offer_delay_minutes is None:
        raise ValidationError("تأخیر پیشنهاد چالش را تنظیم کنید.")
    if program.success_multiplier is None or program.failure_multiplier is None:
        raise ValidationError("ضرایب موفقیت و شکست را تنظیم کنید.")
    for value, field, zero_allowed in (
        (program.success_multiplier, "ضریب موفقیت", False),
        (program.failure_multiplier, "ضریب شکست", True),
    ):
        if not Decimal(str(value)).is_finite() or (value < 0 if zero_allowed else value <= 0):
            raise ValidationError(f"{field} نامعتبر است.")
    snapshot = challenge_rate_snapshot(program.brand_id)
    rates = [Decimal(snapshot["base_percent"])] + [Decimal(row["percent"]) for row in snapshot["overrides"]]
    if max(rates) * program.success_multiplier > 100 or max(rates) * program.failure_multiplier > 100:
        raise ValidationError("حاصل نرخ پایه و ضریب چالش نباید از صد درصد بیشتر باشد.")
    from .models import ChallengeTier
    if not ChallengeTier.objects.filter(program=program, is_active=True, entry_fee__gt=0).exists():
        raise ValidationError("حداقل یک سطح فعال با مبلغ ورودی معتبر لازم است.")
    return snapshot
