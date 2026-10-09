"""Short customer-facing plan names and specifications."""

from decimal import Decimal

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
DURATION_CODES = {"days": "d", "weeks": "w", "months": "m", "years": "y"}
DURATION_LABELS = {"days": "روز", "weeks": "هفته", "months": "ماه", "years": "سال"}


def number(value):
    return format(Decimal(str(value)).normalize(), "f")


def compact_plan_specs(plan):
    details = []
    if plan.plan_type == "unlimited":
        details.append("نامحدود")
    elif plan.traffic_limit_gb is not None:
        details.append(f"{number(plan.traffic_limit_gb)}گیگ")
    details.append(f"{number(plan.max_users)}کاربر")
    if plan.duration_value and plan.duration_unit in DURATION_LABELS:
        details.append(
            f"{number(plan.duration_value)}{DURATION_LABELS[plan.duration_unit]}"
        )
    return "".join("💉" + item.translate(PERSIAN_DIGITS) for item in details)


def connectix_service_category(group_name, fallback="normal"):
    if str(group_name or "").strip().casefold() in {"", "default"}:
        return "royal"
    return fallback


def connectix_plan_name(
    *,
    max_users,
    plan_type,
    traffic_limit_gb,
    duration_value,
    duration_unit,
    service_category,
):
    traffic = (
        "unlimited"
        if plan_type == "unlimited"
        else (
            f"{number(traffic_limit_gb)}g" if traffic_limit_gb is not None else "time"
        )
    )
    duration = (
        f"{number(duration_value)}{DURATION_CODES[duration_unit]}"
        if duration_value and duration_unit in DURATION_CODES
        else "open"
    )
    return f"{number(max_users)}×{traffic}_{duration}_{service_category}"
