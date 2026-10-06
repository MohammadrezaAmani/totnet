"""Retryable referral rewards and scheduled gamification notifications."""

import logging
import math
from datetime import timedelta
from decimal import Decimal
from html import escape

from celery import shared_task
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Exists, F, Max, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.orders.models import Order, Payment
from apps.orders.services import WalletOperationError
from apps.subscriptions.models import Subscription
from utils.message import broadcast_message

from .gamification import ChallengeError, settle_challenge
from .models import (
    ChallengeProgram,
    GamificationNotification,
    Referral,
    ReferralProgram,
    ReferralReward,
    ServiceSurvey,
    UserChallenge,
)
from .services import RewardConfigurationError, award_level_one_referral_for_payment

logger = logging.getLogger(__name__)


def _queue_once(*, user, brand_id: int, notification_type: str, dedupe_key: str, text: str, buttons_data=None, metadata=None) -> bool:
    """Publish one Telegram notification with a durable, retryable dedupe marker."""
    if not user.telegram_id:
        return False
    try:
        # Keep the row lock until the Redis publish decision is recorded. This
        # prevents concurrent beat/task workers from publishing the same logical
        # notification at the same time while still leaving failed publishes
        # retryable with sent_at=NULL.
        with transaction.atomic():
            notification, created = GamificationNotification.objects.select_for_update().get_or_create(
                dedupe_key=dedupe_key,
                defaults={
                    "user": user,
                    "brand_id": brand_id,
                    "notification_type": notification_type,
                    "metadata": metadata or {},
                },
            )
            if not created and notification.sent_at is not None:
                return False

            published = broadcast_message(
                brand_id=brand_id,
                user_ids=[user.telegram_id],
                text=text,
                buttons_data=buttons_data,
            )
            if not published:
                return False
            notification.sent_at = timezone.now()
            notification.metadata = metadata or notification.metadata
            notification.save(update_fields=["sent_at", "metadata"])
            return True
    except IntegrityError:
        # A concurrent create for the same unique dedupe key won. The next beat
        # run can retry if that winner failed before setting sent_at.
        return False


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def process_referral_reward(self, payment_id: str):
    """Process one confirmed purchase and notify its direct referrer once."""
    try:
        result = award_level_one_referral_for_payment(payment_id=payment_id)
    except (RewardConfigurationError, ChallengeError, WalletOperationError) as exc:
        logger.warning("Referral reward deferred for payment %s: %s", payment_id, exc)
        raise self.retry(exc=exc)

    reward = (
        ReferralReward.objects.select_related("user", "order")
        .filter(order__payments__payment_id=payment_id)
        .first()
    )
    if reward and reward.status in {ReferralReward.RewardStatus.PROCESSED, ReferralReward.RewardStatus.PENDING}:
        _queue_once(
            user=reward.user,
            brand_id=reward.brand_id,
            notification_type=GamificationNotification.NotificationType.REFERRAL_REWARD,
            dedupe_key=f"referral-reward:{reward.pk}",
            text="🎁 یک امتیاز جدید از سیستم معرفی دوستان دریافت کردید.",
            buttons_data=[[{"text": "🏅 مشاهده امتیازات", "callback_data": "rewards"}]],
            metadata={"reward_id": reward.pk, "order_id": reward.order_id},
        )
    return str(result)


@shared_task
def recover_pending_referral_rewards(limit: int = 100):
    """Recover confirmed referred purchases if enqueueing or processing failed."""
    rewarded_orders = ReferralReward.objects.filter(order_id=OuterRef("order_id"))
    eligible_referrals = Referral.objects.filter(
        referee_id=OuterRef("order__user_id"),
        brand_id=OuterRef("order__brand_id"),
        status__in=[
            Referral.ReferralStatus.PENDING,
            Referral.ReferralStatus.CONVERTED,
            Referral.ReferralStatus.REWARDED,
        ],
    )
    active_programs = ReferralProgram.objects.filter(
        brand_id=OuterRef("order__brand_id"), is_active=True
    )
    payments = (
        Payment.objects.filter(
            status=Payment.PaymentStatus.CONFIRMED,
            order__isnull=False,
            order__status__in=[
                Order.OrderStatus.PAID,
                Order.OrderStatus.PROCESSING,
                Order.OrderStatus.COMPLETED,
            ],
        )
        .annotate(has_reward=Exists(rewarded_orders))
        .annotate(has_referral=Exists(eligible_referrals))
        .annotate(has_program=Exists(active_programs))
        .filter(has_reward=False, has_referral=True, has_program=True)
        .order_by("created_at")
        .values_list("payment_id", flat=True)[:limit]
    )
    queued = 0
    for payment_id in payments:
        process_referral_reward.delay(str(payment_id))
        queued += 1
    return queued


def _first_purchase_pairs(
    *, cutoff, limit: int, brand_id: int | None = None, sent_notification_type: str | None = None
):
    """Return user/brand pairs whose first *fully-paid subscription* purchase is old enough.

    A single confirmed partial wallet payment is not a completed purchase. The
    notification clock therefore starts at the final confirmed payment that
    brings a positive-price subscription order to at least ``final_price``.
    """
    subscription_order_types = (
        Order.OrderType.NEW_SUBSCRIPTION,
        Order.OrderType.RENEWAL,
        Order.OrderType.UPGRADE,
        Order.OrderType.GIFT,
    )
    paid_statuses = (
        Order.OrderStatus.PAID,
        Order.OrderStatus.PROCESSING,
        Order.OrderStatus.COMPLETED,
    )
    confirmed_filter = Q(payments__status=Payment.PaymentStatus.CONFIRMED)
    base_orders = Order.objects.filter(
        order_type__in=subscription_order_types,
        status__in=paid_statuses,
        final_price__gt=0,
        user__telegram_id__isnull=False,
    ).exclude(user__telegram_id=0)
    if brand_id is not None:
        base_orders = base_orders.filter(brand_id=brand_id)
    if sent_notification_type is not None:
        sent_notification = GamificationNotification.objects.filter(
            user_id=OuterRef("user_id"),
            brand_id=OuterRef("brand_id"),
            notification_type=sent_notification_type,
            sent_at__isnull=False,
        )
        base_orders = base_orders.annotate(
            already_notified=Exists(sent_notification)
        ).filter(already_notified=False)

    completed_orders = (
        base_orders
        .annotate(
            confirmed_total=Coalesce(
                Sum("payments__amount", filter=confirmed_filter),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=25, decimal_places=10),
            ),
            paid_at=Max("payments__created_at", filter=confirmed_filter),
        )
        .filter(confirmed_total__gte=F("final_price"), paid_at__lte=cutoff)
        .select_related("user")
        .order_by("user_id", "brand_id", "paid_at", "pk")
    )

    seen = set()
    yielded = 0
    for order in completed_orders.iterator(chunk_size=200):
        pair = (order.user_id, order.brand_id)
        if pair in seen:
            continue
        seen.add(pair)
        final_payment = (
            Payment.objects.filter(
                order_id=order.pk,
                status=Payment.PaymentStatus.CONFIRMED,
                created_at__lte=order.paid_at,
            )
            .select_related("user")
            .order_by("-created_at", "-pk")
            .first()
        )
        if final_payment is None:
            continue
        yield order.user_id, order.brand_id, final_payment
        yielded += 1
        if yielded >= limit:
            break


@shared_task
def run_gamification_notifications(limit: int = 500):
    """Drive survey/referral/challenge/expiry notifications and challenge settlement."""
    now = timezone.now()
    counters = {
        "survey": 0,
        "referral_intro": 0,
        "challenge_offer": 0,
        "challenge_reminder": 0,
        "expiry": 0,
        "settled": 0,
    }

    # 24 hours after the first confirmed purchase: three-part survey.
    for user_id, brand_id, first in _first_purchase_pairs(
        cutoff=now - timedelta(hours=24),
        limit=limit,
        sent_notification_type=GamificationNotification.NotificationType.SURVEY,
    ):
        user = first.user
        ServiceSurvey.objects.get_or_create(user_id=user_id, brand_id=brand_id)
        if _queue_once(
            user=user,
            brand_id=brand_id,
            notification_type=GamificationNotification.NotificationType.SURVEY,
            dedupe_key=f"first-purchase-survey:{brand_id}:{user_id}",
            text=(
                "📝 <b>نظرسنجی کوتاه سرویس</b>\n\n"
                "برای بهتر شدن سرویس، لطفاً به سه بخش کیفیت اتصال، کار با برنامه و پشتیبانی از ۱ تا ۵ امتیاز بدهید."
            ),
            buttons_data=[[{"text": "⭐ شروع نظرسنجی", "callback_data": "service_survey_start"}]],
            metadata={"first_payment_id": str(first.payment_id)},
        ):
            counters["survey"] += 1

    # 72 hours after the first purchase: explain the referral/community system.
    for user_id, brand_id, first in _first_purchase_pairs(
        cutoff=now - timedelta(hours=72),
        limit=limit,
        sent_notification_type=GamificationNotification.NotificationType.REFERRAL_INTRO,
    ):
        user = first.user
        if _queue_once(
            user=user,
            brand_id=brand_id,
            notification_type=GamificationNotification.NotificationType.REFERRAL_INTRO,
            dedupe_key=f"referral-intro:{brand_id}:{user_id}",
            text=(
                "👨🏽‍⚕️ این سیستم راه‌اندازی شده تا افراد به بهترین کیفیت اینترنت اما بدون هزینه دسترسی داشته باشند.\n\n"
                "فعالیت و معرفی بیشتر توسط شما ضمن کاهش محسوس و دائمی هزینه‌های شما، منجر به احقاق این حق برای افراد بیشتری می‌شود.\n"
                "شما عضو اصلی این تیم پزشکی هستید نه یک بیمار ساده!"
            ),
            buttons_data=[[{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}]],
            metadata={"first_payment_id": str(first.payment_id)},
        ):
            counters["referral_intro"] += 1

    # One-time challenge offer after the configured delay from first purchase.
    for program in ChallengeProgram.objects.filter(is_active=True).select_related("brand"):
        cutoff = now - timedelta(days=program.offer_delay_days)
        for user_id, brand_id, first in _first_purchase_pairs(
            cutoff=cutoff,
            limit=limit,
            brand_id=program.brand_id,
            sent_notification_type=GamificationNotification.NotificationType.CHALLENGE_OFFER,
        ):
            challenge, _created = UserChallenge.objects.get_or_create(
                user_id=user_id,
                brand_id=brand_id,
                program=program,
                defaults={"status": UserChallenge.Status.OFFERED, "offered_at": now},
            )
            if challenge.status != UserChallenge.Status.OFFERED:
                continue
            # Retry an unsent offer on later beat runs; _queue_once deduplicates
            # successfully-published offers by challenge id.
            if _queue_once(
                user=first.user,
                brand_id=brand_id,
                notification_type=GamificationNotification.NotificationType.CHALLENGE_OFFER,
                dedupe_key=f"challenge-offer:{challenge.pk}",
                text=(
                    f"🏆 <b>چالش ویژه {escape(program.name)} برای شما فعال شد</b>\n\n"
                    "امکان شرکت در این چالش فقط و فقط همین یکبار برای شما امکان‌پذیر است.\n"
                    "مایل به دریافت شرایط شرکت در این چالش هستید؟"
                ),
                buttons_data=[
                    [
                        {"text": "✅ بله", "callback_data": f"challenge_accept_{challenge.pk}"},
                        {"text": "❌ خیر", "callback_data": f"challenge_decline_{challenge.pk}"},
                    ]
                ],
                metadata={"challenge_id": challenge.pk},
            ):
                counters["challenge_offer"] += 1

    # Settle expired challenges before sending any more reminders.
    for challenge in UserChallenge.objects.filter(
        status=UserChallenge.Status.ACTIVE, ends_at__lte=now
    ).values_list("pk", flat=True)[:limit]:
        try:
            settle_challenge(challenge_id=challenge)
            counters["settled"] += 1
        except ChallengeError:
            logger.exception("Could not settle expired challenge %s", challenge)

    # 5/3/1 day reminders while active.
    active = UserChallenge.objects.filter(
        status=UserChallenge.Status.ACTIVE, ends_at__gt=now
    ).select_related("user", "program", "tier")
    for challenge in active.iterator(chunk_size=200):
        remaining_seconds = (challenge.ends_at - now).total_seconds()
        days_left = max(1, math.ceil(remaining_seconds / 86400))
        if days_left not in {5, 3, 1}:
            continue
        if _queue_once(
            user=challenge.user,
            brand_id=challenge.brand_id,
            notification_type=GamificationNotification.NotificationType.CHALLENGE_REMINDER,
            dedupe_key=f"challenge-reminder:{challenge.pk}:{days_left}",
            text=(
                f"⏳ فقط {days_left} روز تا تکمیل چالش ویژه {escape(challenge.program.name)} "
                "و دریافت امتیاز بزرگ آن فرصت دارید."
            ),
            buttons_data=[[{"text": "🏆 مشاهده چالش", "callback_data": "active_challenges"}]],
            metadata={"challenge_id": challenge.pk, "days_left": days_left},
        ):
            counters["challenge_reminder"] += 1
            if counters["challenge_reminder"] >= limit:
                break

    # Five-day subscription expiry reminder, once per concrete expiry timestamp.
    subscriptions = (
        Subscription.objects.filter(
            status=Subscription.SubscriptionStatus.ACTIVE,
            expires_at__gt=now + timedelta(days=4),
            expires_at__lte=now + timedelta(days=5),
            owner__telegram_id__isnull=False,
        )
        .exclude(owner__telegram_id=0)
        .select_related("owner")
    )
    for subscription in subscriptions.iterator(chunk_size=200):
        key = f"expiry-5d:{subscription.pk}:{subscription.expires_at.isoformat()}"
        if _queue_once(
            user=subscription.owner,
            brand_id=subscription.brand_id,
            notification_type=GamificationNotification.NotificationType.EXPIRY_5D,
            dedupe_key=key,
            text=(
                "⏰ از اشتراک شما ۵ روز باقیمانده است، با معرفی فقط چند نفر "
                "کاهش دائمی در هزینه‌های اینترنتتون خواهید داشت."
            ),
            buttons_data=[
                [
                    {"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"},
                    {"text": "👥 معرفی دوستان", "callback_data": "referral_system"},
                ]
            ],
            metadata={"subscription_id": subscription.pk, "expires_at": subscription.expires_at.isoformat()},
        ):
            counters["expiry"] += 1
            if counters["expiry"] >= limit:
                break

    return counters
