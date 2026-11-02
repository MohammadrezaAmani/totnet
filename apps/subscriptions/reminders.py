"""One lifecycle warning per subscription term: six days or ten-percent traffic."""

import hashlib
import math
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from apps.referrals.models import GamificationNotification
from apps.referrals.tasks import _queue_once

from .models import Subscription
from .usage import consumed_traffic_gb

REMINDER_DAYS = 6
REMAINING_TRAFFIC_PERCENT = Decimal("10")
_FARSI_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def warning_cycle(subscription):
    """Stable throughout a plan term; changes when a known expiry is renewed."""
    remote = getattr(subscription, "remote_account", None)
    metadata = (remote.metadata or {}) if remote else {}
    remote_expiry = (
        str(metadata.get("expire_date") or "").strip()
        if subscription.vpn_provider.provider_type == "connectix" else ""
    )
    if remote_expiry:
        anchor = f"remote:{remote_expiry}"
    elif subscription.expires_at:
        anchor = subscription.expires_at.isoformat()
    else:
        # Without an expiry date, a renewal cannot be reliably detected.
        anchor = f"{subscription.starts_at.isoformat()}:{subscription.plan_id}"
    raw = f"{subscription.pk}:{anchor}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def warning_key(subscription):
    # Same key for TIME and TRAFFIC; only the first condition can send.
    return f"subscription-low:v1:{subscription.pk}:{warning_cycle(subscription)}"


def _remote_days_left(subscription, *, now):
    remote = getattr(subscription, "remote_account", None)
    if not remote or not remote.last_synced_at or remote.last_synced_at < now - timedelta(minutes=30):
        return None
    raw = str((remote.metadata or {}).get("remains_days") or "").translate(_FARSI_DIGITS).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:days?|روز)?", raw, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return Decimal(match[1])
    except InvalidOperation:
        return None


def warning_reason(subscription, *, now=None):
    """Return (reason, text) or None. Time wins ties in one scan."""
    now = now or timezone.now()
    if subscription.status != Subscription.SubscriptionStatus.ACTIVE:
        return None
    remote = getattr(subscription, "remote_account", None)
    if remote and remote.remote_status in {"expired", "inactive"}:
        return None

    remote_days = (
        _remote_days_left(subscription, now=now)
        if subscription.vpn_provider.provider_type == "connectix" else None
    )
    # Provider-reported remaining days take precedence over a stale local
    # expiry timestamp after an upstream renewal.
    if remote_days is not None and remote_days <= 0:
        return None
    if remote_days is None and subscription.expires_at and subscription.expires_at <= now:
        return None
    if remote_days is not None:
        days_left = remote_days
    elif subscription.expires_at:
        days_left = Decimal(str((subscription.expires_at - now).total_seconds())) / Decimal(86400)
    else:
        days_left = None
    if days_left is not None and Decimal(0) < days_left <= REMINDER_DAYS:
        # Avoid telling a subscriber there are six days remaining when the
        # scheduler first sees an account whose expiry is much nearer.
        days_rounded = max(1, math.ceil(days_left))
        return "time", f"⏰ از تایم اشتراک شما {str(days_rounded).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))} روز مانده است!"

    limit = subscription.traffic_limit_gb
    if limit is None or limit <= 0:
        return None
    used = consumed_traffic_gb(subscription)
    if used is None:
        return None
    if used >= (limit * (Decimal(100) - REMAINING_TRAFFIC_PERCENT) / Decimal(100)):
        return "traffic", "📉 تنها ۱۰٪ یا کمتر از حجم اشتراک شما باقی مانده است!"
    return None


def _legacy_five_day_sent(subscription):
    """Avoid a second message to people who already received the old alert."""
    if not subscription.expires_at:
        return False
    return GamificationNotification.objects.filter(
        notification_type=GamificationNotification.NotificationType.EXPIRY_5D,
        sent_at__isnull=False,
        metadata__subscription_id=subscription.pk,
        metadata__expires_at=subscription.expires_at.isoformat(),
    ).exists()


def scan_subscription_warnings(*, limit=500, now=None):
    """Queue due messages with a persistent deduplication key, never send inline."""
    now = now or timezone.now()
    queued = 0
    subscriptions = (
        Subscription.objects.filter(
            status=Subscription.SubscriptionStatus.ACTIVE,
            brand__status="active",
            owner__is_active=True,
            owner__telegram_id__isnull=False,
        )
        .exclude(owner__telegram_id=0)
        .select_related("owner", "vpn_provider", "remote_account")
        .order_by("pk")
    )
    for subscription in subscriptions.iterator(chunk_size=200):
        reason = warning_reason(subscription, now=now)
        if not reason or _legacy_five_day_sent(subscription):
            continue
        kind, first_line = reason
        key = warning_key(subscription)
        buttons = [
            [{"text": "👥 معرفی دوستان", "callback_data": "referral_system", "style": "success"}],
            [{"text": "🎯 نحوه کسب امتیاز", "callback_data": "how_to_earn"}],
        ]
        if _queue_once(
            user=subscription.owner,
            brand_id=subscription.brand_id,
            notification_type=GamificationNotification.NotificationType.SUBSCRIPTION_LOW,
            dedupe_key=key,
            text=(first_line + "\n\nهمین الان با معرفی فقط چند نفر در این سیستم "
                  "برای همیشه بدون هزینه اشتراک دریافت کنید!"),
            buttons_data=buttons,
            metadata={
                "event": "subscription_low",
                "subscription_id": subscription.pk,
                "warning_cycle": warning_cycle(subscription),
                "reason": kind,
            },
        ):
            queued += 1
            if queued >= limit:
                break
    return queued
