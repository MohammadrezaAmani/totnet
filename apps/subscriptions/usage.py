"""Render provider usage without assigning an invented unit to unlabelled traffic."""

import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def labelled_traffic_gb(value):
    text = str(value or "").translate(_DIGITS).strip().replace(",", "")
    match = re.fullmatch(
        r"([\d.]+)\s*(bytes?|[kmgt]i?b|گیگ(?:ابایت)?|مگ(?:ابایت)?)", text, re.IGNORECASE
    )
    if not match:
        return None
    try:
        number = Decimal(match[1])
    except InvalidOperation:
        return None
    unit = match[2].lower()
    factors = {
        "byte": Decimal(1) / 1024**3,
        "bytes": Decimal(1) / 1024**3,
        "b": Decimal(1) / 1024**3,
        "kb": Decimal(1) / 1024**2,
        "kib": Decimal(1) / 1024**2,
        "mb": Decimal(1) / 1024,
        "mib": Decimal(1) / 1024,
        "gb": Decimal(1),
        "gib": Decimal(1),
        "tb": Decimal(1024),
        "tib": Decimal(1024),
        "گیگ": Decimal(1),
        "گیگابایت": Decimal(1),
        "مگ": Decimal(1) / 1024,
        "مگابایت": Decimal(1) / 1024,
    }
    return max(Decimal(0), number * factors[unit])


def remaining_usage(subscription):
    remote = getattr(subscription, "remote_account", None)
    metadata = (remote.metadata or {}) if remote else {}
    used = subscription.traffic_used_gb
    connectix = subscription.vpn_provider.provider_type == "connectix"
    if connectix:
        parsed = labelled_traffic_gb(metadata.get("used_traffic_raw"))
        if parsed is not None:
            used = parsed
        elif (
            remote is None
            or "used_traffic_raw" not in metadata
            or str(metadata.get("used_traffic_raw") or "").strip()
            not in {"0", "0.0", "0.00"}
        ):
            used = None
    if subscription.traffic_limit_gb is None:
        traffic = "نامحدود"
    elif used is None:
        traffic = "در انتظار اطلاعات مصرف"
    else:
        traffic = f"{max(Decimal(0), subscription.traffic_limit_gb - used):.2f} گیگ"
    raw_days = str(metadata.get("remains_days") or "").translate(_DIGITS).strip()
    match = re.fullmatch(r"([\d.]+)\s*(?:days?|روز)?", raw_days, re.IGNORECASE)
    if subscription.status == "expired":
        time_left = "منقضی شده"
    elif connectix and match:
        time_left = f"{Decimal(match[1]):g} روز"
    elif connectix and raw_days:
        time_left = raw_days[:80]
    elif subscription.expires_at:
        seconds = max(
            0, int((subscription.expires_at - timezone.now()).total_seconds())
        )
        days, rest = divmod(seconds, 86400)
        time_left = f"{days} روز و {rest // 3600} ساعت" if seconds else "منقضی شده"
    elif subscription.status == "pending":
        time_left = "در انتظار فعال‌سازی"
    elif subscription.plan.duration_value:
        time_left = "پس از اولین اتصال محاسبه می‌شود"
    else:
        time_left = "نامحدود"
    return traffic, time_left
