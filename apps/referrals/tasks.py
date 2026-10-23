"""Retryable referral rewards and scheduled gamification notifications."""

import logging
import math
from datetime import timedelta
from decimal import Decimal
from html import escape

from asgiref.sync import async_to_sync
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Exists, F, Max, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.orders.models import Order, Payment
from apps.orders.services import WalletOperationError
from apps.subscriptions.models import Subscription

from .gamification import ChallengeError, settle_challenge
from .models import (
    ChallengeProgram,
    GamificationNotification,
    Referral,
    ReferralProgram,
    ReferralReward,
    UserChallenge,
)
from .services import RewardConfigurationError, award_level_one_referral_for_payment

logger = logging.getLogger(__name__)


def _publish_notification(notification_id):
    try:
        deliver_gamification_notification.delay(notification_id)
    except Exception as exc:
        logger.warning(
            "Notification %s enqueue failed (%s)", notification_id, type(exc).__name__
        )


def _queue_delivery(notification):
    metadata = dict(notification.metadata or {})
    if notification.rule_id and not notification.rule.is_active:
        return False
    if notification.sent_at or metadata.get("delivery_abandoned"):
        return False
    queued_at = parse_datetime(str(metadata.get("delivery_queued_at", "")))
    if (
        queued_at
        and timezone.is_aware(queued_at)
        and queued_at > timezone.now() - timedelta(minutes=5)
    ):
        return False
    metadata["delivery_queued_at"] = timezone.now().isoformat()
    notification.metadata = metadata
    notification.save(update_fields=["metadata"])
    transaction.on_commit(lambda: _publish_notification(notification.pk))
    return True


@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
)
def deliver_gamification_notification(self, notification_id):
    """Mark a durable notification sent only after Telegram accepts it."""
    try:
        with transaction.atomic():
            notification = (
                GamificationNotification.objects.select_for_update(of=("self",))
                .select_related("user", "brand")
                .get(pk=notification_id)
            )
            metadata = dict(notification.metadata or {})
            if notification.sent_at or metadata.get("delivery_abandoned"):
                return False
            payload = metadata.get("delivery_payload")
            from apps.accounts.models import UserProfile
            opted_out = UserProfile.objects.filter(user_id=notification.user_id, notification_enabled=False).exists()
            # A purchased gift handover is a transactional fulfillment notice,
            # not opt-in marketing. The buyer must still receive the unique
            # voucher even when promotional notifications are disabled.
            transactional = metadata.get("event") in {"gift_handover", "gift_referral_handover"}
            if (opted_out and not transactional) or not notification.user.is_active:
                return False
            if notification.rule_id:
                if not notification.rule.is_active:
                    return False
                from .scheduling import audience_users
                if not audience_users(notification.brand_id, notification.rule.audience).filter(pk=notification.user_id).exists():
                    return False
            if notification.notification_type == "challenge_offer":
                challenge_id = metadata.get("challenge_id")
                if challenge_id and not UserChallenge.objects.filter(pk=challenge_id, status="offered", program__is_active=True).exists():
                    return False
            if not payload or notification.brand.status != "active":
                return False

            async def send():
                bot = Bot(
                    notification.brand.bot_token,
                    session=AiohttpSession(proxy=settings.SOCKS5_PROXY),
                )
                try:
                    buttons = payload.get("buttons_data")
                    keyboard = (
                        InlineKeyboardMarkup(
                            inline_keyboard=[
                                [InlineKeyboardButton(**button) for button in row]
                                for row in buttons
                            ]
                        )
                        if buttons
                        else None
                    )
                    return await bot.send_message(
                        chat_id=notification.user.telegram_id,
                        text=payload["text"],
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                finally:
                    await bot.session.close()

            try:
                result = async_to_sync(send)()
            except TelegramForbiddenError:
                metadata["delivery_abandoned"] = True
                notification.metadata = metadata
                notification.save(update_fields=["metadata"])
                return False
            metadata["telegram_message_id"] = result.message_id
            notification.metadata = metadata
            notification.sent_at = timezone.now()
            notification.save(update_fields=["sent_at", "metadata"])
            return True
    except GamificationNotification.DoesNotExist:
        return False
    except Exception as exc:
        with transaction.atomic():
            failed = GamificationNotification.objects.select_for_update().filter(pk=notification_id).first()
            if failed and not failed.sent_at:
                metadata = dict(failed.metadata or {})
                metadata["delivery_failures"] = int(metadata.get("delivery_failures", 0)) + 1
                metadata["last_error_type"] = type(exc).__name__
                metadata["last_failed_at"] = timezone.now().isoformat()
                failed.metadata = metadata
                failed.save(update_fields=["metadata"])
        delay = max(1, exc.retry_after) if isinstance(exc, TelegramRetryAfter) else 60
        raise self.retry(
            exc=RuntimeError(type(exc).__name__), countdown=delay
        ) from None


def _queue_once(
    *,
    user,
    brand_id: int,
    notification_type: str,
    dedupe_key: str,
    text: str,
    buttons_data=None,
    metadata=None,
    rule_id=None,
) -> bool:
    """Publish one Telegram notification with a durable, retryable dedupe marker."""
    if not user.telegram_id:
        return False
    try:
        with transaction.atomic():
            notification, created = (
                GamificationNotification.objects.select_for_update().get_or_create(
                    dedupe_key=dedupe_key,
                    defaults={
                        "user": user,
                        "brand_id": brand_id,
                        "notification_type": notification_type,
                        "metadata": metadata or {},
                        "rule_id": rule_id,
                    },
                )
            )
            if not created and notification.sent_at is not None:
                return False

            notification.metadata = {
                **(notification.metadata or {}),
                **(metadata or {}),
                "delivery_payload": {"text": text, "buttons_data": buttons_data},
            }
            return _queue_delivery(notification)
    except IntegrityError:
        # A concurrent create for the same unique dedupe key won. The next beat
        # run can retry if that winner failed before setting sent_at.
        return False



def _confirmed_referral_target(order):
    """The purchaser's inviter, with legacy referred_by fallback (alerts only)."""
    if order.order_type not in (
        Order.OrderType.NEW_SUBSCRIPTION,
        Order.OrderType.RENEWAL,
        Order.OrderType.UPGRADE,
        Order.OrderType.GIFT,
    ):
        return None
    referral = Referral.objects.select_related("referrer").filter(
        brand_id=order.brand_id,
        referee_id=order.user_id,
    ).first()
    if referral:
        if referral.status == Referral.ReferralStatus.REJECTED:
            return None
        user = referral.referrer
    else:
        # Older accounts may only have User.referred_by. Do not create points
        # or a new referral relation as a side effect of notification.
        from apps.accounts.models import User

        inviter_id = User.objects.filter(
            pk=order.user_id, brand_id=order.brand_id
        ).values_list("referred_by_id", flat=True).first()
        user = User.objects.filter(pk=inviter_id, brand_id=order.brand_id).first() if inviter_id else None
    if not user or user.brand_id != order.brand_id or user.pk == order.user_id:
        return None
    return user if user.is_active and user.telegram_id else None


def _claimed_gift_referral_target(order):
    """The gift purchaser, only when a *new* member claims their gift link."""
    if order.order_type != Order.OrderType.GIFT or not order.recipient_id or not order.recipient_claimed_at:
        return None
    from apps.orders.models import GiftVoucher
    from apps.accounts.models import User

    if not GiftVoucher.objects.filter(
        order_id=order.pk, claimed_by_id=order.recipient_id, new_member_at_claim=True
    ).exists():
        return None
    if not Referral.objects.filter(
        brand_id=order.brand_id, referee_id=order.recipient_id,
        referrer_id=order.user_id,
    ).exclude(status=Referral.ReferralStatus.REJECTED).exists():
        return None
    buyer = User.objects.filter(pk=order.user_id, brand_id=order.brand_id).first()
    if not buyer or not buyer.is_active or not buyer.telegram_id:
        return None
    return buyer


def _already_delivered_legacy_reward(order, user_id):
    reward_ids = list(ReferralReward.objects.filter(
        order_id=order.pk, user_id=user_id,
    ).values_list("pk", flat=True))
    return bool(reward_ids) and GamificationNotification.objects.filter(
        dedupe_key__in=[f"referral-reward:{pk}" for pk in reward_ids],
        sent_at__isnull=False,
    ).exists()


def _earned_pill_purchase_message(*, order, referrer):
    """Render the pill alert only for an actually processed point for this order.

    Rank the awarded point in the original reward ledger, not the current open
    pill box: after the third point is cashed out, its historical position must
    still yield zero remaining, even when the notification is recovered later.
    """
    reward = (
        ReferralReward.objects.filter(
            order_id=order.pk,
            user_id=referrer.pk,
            brand_id=order.brand_id,
            reward_type="normal_point",
            status=ReferralReward.RewardStatus.PROCESSED,
            amount=Decimal("1"),
        )
        .order_by("created_at", "pk")
        .first()
    )
    if reward is None:
        return None

    awarded_count = (
        ReferralReward.objects.filter(
            user_id=referrer.pk,
            brand_id=order.brand_id,
            reward_type__in=["normal_point", "profile_point"],
            status=ReferralReward.RewardStatus.PROCESSED,
        )
        .filter(
            Q(created_at__lt=reward.created_at)
            | Q(created_at=reward.created_at, pk__lte=reward.pk)
        )
        .count()
    )
    from .gamification import POINTS_PER_PILL

    remaining = (-awarded_count) % POINTS_PER_PILL
    persian_remaining = str(remaining).translate(
        str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    )
    return (
        "<b>شما یک 💊دریافت کردید</b>"
        "\n\n🦠به واسطه خرید کاربری که با لینک شما عضو شده یک قرص دریافت کردید"
        f"\n\n📦تنها {persian_remaining}قرص دیگر مانده تا نقد شدن کامل یک امتیاز"
    )


def _queue_confirmed_referral_purchase(order):
    """Notify each rightful inviter once per settled order, never on a partial payment.

    Buying a gift counts as a purchase by the buyer. A *separate* notification
    about a newly invited recipient is only sent after the gift is claimed.
    """
    if order.status not in (
        Order.OrderStatus.PAID,
        Order.OrderStatus.PROCESSING,
        Order.OrderStatus.COMPLETED,
    ) or order.final_price <= 0:
        return False
    paid = Payment.objects.filter(
        order_id=order.pk, status=Payment.PaymentStatus.CONFIRMED,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if paid < order.final_price:
        return False

    queued = False
    referrer = _confirmed_referral_target(order)
    if referrer and not _already_delivered_legacy_reward(order, referrer.pk):
        text = _earned_pill_purchase_message(order=order, referrer=referrer)
        if text is not None:
            queued = _queue_once(
                user=referrer,
                brand_id=order.brand_id,
                notification_type=GamificationNotification.NotificationType.REFERRAL_PURCHASE,
                dedupe_key=f"referral-purchase:{order.pk}",
                text=text,
                buttons_data=[[{"text": "🏅 امتیازات معرفی", "callback_data": "rewards"}]],
                metadata={"order_id": order.pk, "referee_id": order.user_id},
            ) or queued

    gift_inviter = _claimed_gift_referral_target(order)
    if gift_inviter and not _already_delivered_legacy_reward(order, gift_inviter.pk):
        queued = _queue_once(
            user=gift_inviter,
            brand_id=order.brand_id,
            notification_type=GamificationNotification.NotificationType.REFERRAL_PURCHASE,
            dedupe_key=f"gift-claim-referral:{order.pk}",
            text=(
                "🎁 کاربری که با لینک هدیه شما عضو شد، هدیه را دریافت کرد."
                "\n\nوضعیت امتیازات معرفی را از بخش امتیازات بررسی کنید."
            ),
            buttons_data=[[{"text": "🏅 امتیازات معرفی", "callback_data": "rewards"}]],
            metadata={"order_id": order.pk, "referee_id": order.recipient_id},
        ) or queued
    return queued


def _queue_referral_purchase_for_payment(payment_id: str):
    payment = Payment.objects.select_related("order").filter(payment_id=payment_id).first()
    if not payment or payment.status != Payment.PaymentStatus.CONFIRMED or not payment.order_id:
        return False
    return _queue_confirmed_referral_purchase(payment.order)


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def process_referral_reward(self, payment_id: str):
    """Process one confirmed purchase and notify its direct referrer once."""
    # Notify after the reward commits. A purchase must not be described as
    # earning a pill if the ledger has no processed pill for that order.
    try:
        result = award_level_one_referral_for_payment(payment_id=payment_id)
    except (RewardConfigurationError, ChallengeError, WalletOperationError) as exc:
        logger.warning("Referral reward deferred for payment %s: %s", payment_id, exc)
        raise self.retry(exc=exc)
    try:
        _queue_referral_purchase_for_payment(payment_id)
    except Exception:
        logger.exception("Unable to queue referred purchase notification for payment %s", payment_id)
    return str(result)


@shared_task
def recover_missing_referrer_join_notifications(limit: int = 200):
    """Backfill recent *real* first-touch referrals; no rewards are created."""
    from .services import _queue_referrer_join_safely
    since = timezone.now() - timedelta(days=7)
    refs = Referral.objects.filter(
        created_at__gte=since, brand__status="active",
        status__in=[Referral.ReferralStatus.PENDING, Referral.ReferralStatus.CONVERTED,
                    Referral.ReferralStatus.REWARDED],
        referrer__is_active=True, referrer__telegram_id__isnull=False,
    ).select_related("referee").order_by("created_at", "pk")[:limit]
    queued = 0
    for ref in refs:
        if _queue_referrer_join_safely(
            referrer_id=ref.referrer_id, referee_id=ref.referee_id,
            brand_id=ref.brand_id,
        ):
            queued += 1
    return queued


@shared_task
def delete_expired_referrer_join_messages(limit: int = 100):
    """Delete inviter-only join notices ten hours after Telegram delivery.

    Scans from the durable notification ledger rather than using a ten-hour
    Celery countdown, so broker restarts cannot permanently lose deletions.
    The claim lease prevents concurrent beat/workers from double-deleting.
    """
    now = timezone.now()
    candidates = (
        GamificationNotification.objects.filter(
            metadata__event="referral_joined",
            sent_at__isnull=False,
            sent_at__lte=now - timedelta(hours=10),
        )
        .exclude(metadata__has_key="auto_deleted_at")
        .exclude(metadata__has_key="auto_delete_terminal_at")
        .order_by("sent_at", "pk")[:limit * 3]
    )
    deleted = 0
    attempted = 0
    for candidate in candidates:
        if attempted >= limit:
            break

        with transaction.atomic():
            notice = (
                GamificationNotification.objects.select_for_update(of=("self",))
                .select_related("brand", "user")
                .get(pk=candidate.pk)
            )
            metadata = dict(notice.metadata or {})
            if metadata.get("auto_deleted_at") or metadata.get("auto_delete_terminal_at"):
                continue
            lease_started = parse_datetime(str(metadata.get("auto_delete_claimed_at", "")))
            if lease_started and timezone.is_aware(lease_started) and lease_started > now - timedelta(minutes=5):
                continue
            metadata["auto_delete_claimed_at"] = now.isoformat()
            notice.metadata = metadata
            notice.save(update_fields=["metadata"])
            try:
                message_id = int(metadata.get("telegram_message_id"))
            except (TypeError, ValueError):
                message_id = None
            chat_id = notice.user.telegram_id
            token = notice.brand.bot_token

        attempted += 1
        terminal = not (message_id and chat_id)
        success = False
        if not terminal and token:
            async def delete_telegram_message():
                bot = Bot(token, session=AiohttpSession(proxy=settings.SOCKS5_PROXY))
                try:
                    return await bot.delete_message(
                        chat_id=chat_id, message_id=message_id,
                    )
                finally:
                    await bot.session.close()

            try:
                success = bool(async_to_sync(delete_telegram_message)())
            except (TelegramBadRequest, TelegramForbiddenError):
                # Message removed by the user, older than Telegram's limit,
                # or inaccessible: further attempts cannot succeed.
                terminal = True
            except Exception:
                logger.exception("Could not delete referral join notification %s", notice.pk)

        with transaction.atomic():
            current = GamificationNotification.objects.select_for_update().get(pk=notice.pk)
            updated = dict(current.metadata or {})
            updated.pop("auto_delete_claimed_at", None)
            if success:
                updated["auto_deleted_at"] = timezone.now().isoformat()
                deleted += 1
            elif terminal:
                updated["auto_delete_terminal_at"] = timezone.now().isoformat()
            else:
                updated["auto_delete_last_failed_at"] = timezone.now().isoformat()
            current.metadata = updated
            current.save(update_fields=["metadata"])

    return deleted


@shared_task
def recover_missing_referrer_purchase_notifications(limit: int = 200):
    """Catch missed queues, including orders whose reward already exists.

    Recovery only examines recently confirmed purchases to avoid retroactively
    messaging users about months-old sales after a deployment.
    """
    since = timezone.now() - timedelta(days=7)
    orders = (
        Order.objects.filter(
            status__in=[
                Order.OrderStatus.PAID,
                Order.OrderStatus.PROCESSING,
                Order.OrderStatus.COMPLETED,
            ],
            final_price__gt=0,
            payments__status=Payment.PaymentStatus.CONFIRMED,
        )
        .filter(
            Q(payments__verified_at__gte=since)
            | Q(payments__created_at__gte=since)
            | Q(recipient_claimed_at__gte=since)
        )
        .distinct().order_by("-pk")[:limit * 4]
    )
    queued = 0
    for order in orders:
        if _queue_confirmed_referral_purchase(order):
            queued += 1
            if queued >= limit:
                break
    return queued


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
        .filter(has_reward=False)
        .filter(
            Q(
                has_referral=True,
                has_program=True,
                order__order_type__in=[
                    Order.OrderType.NEW_SUBSCRIPTION,
                    Order.OrderType.RENEWAL,
                    Order.OrderType.UPGRADE,
                ],
            )
            | Q(
                order__order_type=Order.OrderType.GIFT,
                order__recipient_claimed_at__isnull=False,
            )
        )
        .order_by("created_at")
        .values_list("payment_id", flat=True)[:limit]
    )
    queued = 0
    for payment_id in payments:
        process_referral_reward.delay(str(payment_id))
        queued += 1
    return queued


def _first_purchase_pairs(
    *,
    cutoff,
    limit: int,
    brand_id: int | None = None,
    sent_notification_type: str | None = None,
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
        base_orders.annotate(
            confirmed_total=Coalesce(
                Sum("payments__amount", filter=confirmed_filter),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=25, decimal_places=10),
            ),
            paid_at=Max(
                Coalesce("payments__verified_at", "payments__created_at"),
                filter=confirmed_filter,
            ),
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
    # Recover an interrupted delivery even after its original reminder window.
    pending_ids = (
        GamificationNotification.objects.filter(
            sent_at__isnull=True,
            metadata__has_key="delivery_payload",
        )
        .filter(Q(rule__isnull=True) | Q(rule__is_active=True))
        .filter(
            Q(metadata__delivery_abandoned__isnull=True)
            | Q(metadata__delivery_abandoned=False)
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    for notification_id in list(pending_ids):
        with transaction.atomic():
            pending = (
                GamificationNotification.objects.select_for_update()
                .filter(pk=notification_id)
                .first()
            )
            if pending:
                _queue_delivery(pending)
    counters = {
        "profile_completion": 0,
        "survey": 0,
        "referral_intro": 0,
        "challenge_offer": 0,
        "challenge_reminder": 0,
        "expiry": 0,
        "settled": 0,
    }

    from .scheduling import lifecycle_candidates, run_notification_rules
    counters["rules"] = run_notification_rules(limit=limit)

    for program in ChallengeProgram.objects.filter(is_active=True, brand__status="active").select_related("brand"):
        queued = 0
        for user, occurrence, anchor_at in lifecycle_candidates(
            brand_id=program.brand_id, trigger=program.offer_trigger,
            delay_minutes=program.offer_delay_minutes, limit=limit,
        ):
            challenge, _ = UserChallenge.objects.get_or_create(
                user=user, brand_id=program.brand_id, program=program,
                defaults={"status": UserChallenge.Status.OFFERED, "offered_at": now},
            )
            if challenge.status != UserChallenge.Status.OFFERED:
                continue
            if _queue_once(
                user=user, brand_id=program.brand_id,
                notification_type=GamificationNotification.NotificationType.CHALLENGE_OFFER,
                dedupe_key=f"challenge-offer:{challenge.pk}",
                text=(f"🏆 <b>چالش ویژه {escape(program.name)} برای شما فعال شد</b>\n\n"
                      "امکان شرکت در این چالش فقط و فقط همین یکبار برای شما امکان‌پذیر است.\n"
                      "مایل به دریافت شرایط شرکت در این چالش هستید؟"),
                buttons_data=[[{"text": "✅ بله", "callback_data": f"challenge_accept_{challenge.pk}"},
                               {"text": "❌ خیر", "callback_data": f"challenge_decline_{challenge.pk}"}]],
                metadata={"challenge_id": challenge.pk, "trigger": program.offer_trigger, "anchor_at": anchor_at.isoformat()},
            ):
                queued += 1
                counters["challenge_offer"] += 1
            if queued >= limit:
                break

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
            buttons_data=[
                [{"text": "🏆 مشاهده چالش", "callback_data": "active_challenges"}]
            ],
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
            metadata={
                "subscription_id": subscription.pk,
                "expires_at": subscription.expires_at.isoformat(),
            },
        ):
            counters["expiry"] += 1
            if counters["expiry"] >= limit:
                break

    return counters
