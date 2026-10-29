"""Short customer-facing plan names and specifications."""

from decimal import Decimal

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
DURATION_CODES = {"days": "d", "weeks": "w", "months": "m", "years": "y"}
DURATION_LABELS = {"days": "روز", "weeks": "هفته", "months": "ماه", "years": "سال"}


def number(value):
    return format(Decimal(str(value)).normalize(), "f")


def plan_gift_label(plan):
    """Gift days from admin or a legacy/upstream `+Nd` suffix."""
    from .policy import plan_gift_days

    days = plan_gift_days(plan)
    return f" +{str(days).translate(PERSIAN_DIGITS)}روز هدیه" if days else ""


def compact_plan_specs(plan):
    details = []
    if plan.plan_type == "unlimited":
        details.append("💉نامحدود")
    elif plan.traffic_limit_gb is not None:
        details.append(f"💉{number(plan.traffic_limit_gb)}گیگ")
    details.append(f"👤{number(plan.max_users)}کاربر")
    if plan.duration_value and plan.duration_unit in DURATION_LABELS:
        details.append(
            f"📅{number(plan.duration_value)}{DURATION_LABELS[plan.duration_unit]}"
        )
    return " ".join(item.translate(PERSIAN_DIGITS) for item in details) + plan_gift_label(plan)


def connectix_service_category(group_name, fallback="normal"):
    normalized = " ".join(
        str(group_name or "").replace("\u200c", " ").casefold().split()
    )
    if normalized in {"", "default", "royal", "رویال", "پیش فرض"}:
        return "royal"
    if normalized in {"iran access", "static ip", "iran ip", "آی پی ایران"}:
        return "iran_ip"
    if normalized in {"economic", "sublink", "normal", "نرمال"}:
        return "normal"
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


CONNECTIX_DELIVERY_INSTRUCTIONS = (
    "نام و رمز را کپی کرده و وارد برنامه ما یعنی کانکتیکس شوید.\n"
    "با آموزش‌های موجود در بخش آکادمی می‌توانید آموزش کامل نصب، "
    "ورود و کار با برنامه را مشاهده نمایید."
)


def connectix_account_message(subscription):
    """Single Telegram HTML template for ready Connectix credentials."""
    from html import escape
    plan = subscription.plan
    services = {"normal": "نرمال", "royal": "رویال", "iran_ip": "آی‌پی ایران"}
    lines = [
        "📱 <b>اطلاعات اشتراک</b>",
        "",
        f"🩺 نوع سرویس: {escape(services.get(plan.service_category, plan.get_service_category_display()))}",
        f"📋 نوع اشتراک: {escape(compact_plan_specs(plan))}",
        f"🏷 نام اشتراک: {escape(subscription.display_name or 'نام‌گذاری نشده')}",
        f"👤 نام کاربری: <code>{escape(subscription.connectix_username or '—')}</code>",
        f"🔑 رمز عبور: <code>{escape(subscription.connectix_password or '—')}</code>",
        "",
        CONNECTIX_DELIVERY_INSTRUCTIONS,
    ]
    return "\n".join(lines)
