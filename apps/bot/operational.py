"""Brand-scoped informational audit trail for the optional Telegram operations channel."""
from django.db import transaction
from apps.accounts.models import User
from apps.brands.models import BrandConfiguration
from .models import OperationalNotice


def queue_operational_event(*, brand_id: int, dedupe_key: str, text: str) -> bool:
    configured = BrandConfiguration.objects.filter(brand_id=brand_id).values_list(
        "operations_channel_id", flat=True,
    ).first()
    if not configured:
        return False
    with transaction.atomic():
        notice, created = OperationalNotice.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={"brand_id": brand_id, "text": text},
        )
        if notice.sent_at:
            return False
        def enqueue():
            try:
                from .tasks import send_operational_notice
                send_operational_notice.delay(notice.pk)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Operations notice enqueue failed")
        transaction.on_commit(enqueue)
    return created


def queue_joined_event(*, brand_id: int, user_id: int, referral_origin_user_id: int | None = None):
    user = User.objects.filter(pk=user_id, brand_id=brand_id).values(
        "telegram_id", "referred_by__telegram_id",
    ).first()
    if not user:
        return False
    referrer = user["referred_by__telegram_id"]
    if referrer is None and referral_origin_user_id is not None:
        referrer = User.objects.filter(
            pk=referral_origin_user_id, brand_id=brand_id
        ).values_list("telegram_id", flat=True).first()
    referrer = referrer or "ندارد"
    return queue_operational_event(
        brand_id=brand_id, dedupe_key=f"join:{brand_id}:{user_id}",
        text=("👤 ورود کاربر جدید\n"
              f"آیدی عددی کاربر: {user['telegram_id']}\n"
              f"آیدی عددی معرف: {referrer}"),
    )


def queue_confirmed_payment_event(*, payment_id: int):
    from apps.orders.models import Payment
    from apps.referrals.models import UserChallenge
    payment = Payment.objects.filter(pk=payment_id, status=Payment.PaymentStatus.CONFIRMED).values(
        "brand_id", "user__telegram_id", "user__referred_by__telegram_id", "amount", "currency",
        "order_id", "wallet_id", "payment_id",
    ).first()
    if payment is None:
        return False
    challenge = UserChallenge.objects.filter(entry_payment_id=payment_id).values_list("program__name", flat=True).first()
    kind = (f"واریز ورودی {challenge}" if challenge else
            "خرید اشتراک" if payment["order_id"] else "شارژ کیف پول" if payment["wallet_id"] else "واریز")
    return queue_operational_event(
        brand_id=payment["brand_id"],
        dedupe_key=f"payment:{payment_id}:confirmed",
        text=(f"✅ {kind} تأیید شد\n"
              f"مبلغ: {payment['amount']:g} {payment['currency']}\n"
              f"آیدی عددی کاربر: {payment['user__telegram_id']}\n"
              f"آیدی عددی معرف: {payment['user__referred_by__telegram_id'] or 'ندارد'}\n"
              f"شناسه پرداخت: {payment['payment_id']}"),
    )
