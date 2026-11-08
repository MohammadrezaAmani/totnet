"""Transactional services for referral cash points and the Propzino challenge."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.brands.models import Brand
from apps.orders.models import Order, Payment, Wallet, WalletTransaction
from apps.orders.services import WalletOperationError, credit_wallet, debit_wallet

from .models import (
    ChallengeProgram,
    ReferralProgram,
    ChallengeReferralEvent,
    ChallengeTier,
    Referral,
    ReferralReward,
    RewardAccount,
    UserChallenge,
)


class ChallengeError(Exception):
    """The requested challenge transition is not valid."""


POINTS_PER_PILL = 3
MONEY_QUANT = Decimal("0.01")


def _money(value: Decimal, currency: str = "USD") -> Decimal:
    quantum = Decimal("1") if currency in {"T", "IRT", "IRR"} else MONEY_QUANT
    return Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)


def _wallet_for(*, user_id: int, brand_id: int) -> Wallet:
    brand_currency = Brand.objects.values_list("currency", flat=True).get(pk=brand_id)
    wallet, _ = Wallet.objects.get_or_create(
        user_id=user_id,
        brand_id=brand_id,
        defaults={"currency": brand_currency},
    )
    if wallet.currency != brand_currency:
        raise ChallengeError("Wallet currency does not match the brand currency")
    return Wallet.objects.select_for_update().get(pk=wallet.pk)


def _update_reward_account(*, user_id: int, brand_id: int, earned_points: Decimal = Decimal("0")) -> RewardAccount:
    account, _ = RewardAccount.objects.select_for_update().get_or_create(
        user_id=user_id, brand_id=brand_id
    )
    if earned_points:
        account.lifetime_points = F("lifetime_points") + earned_points
        account.save(update_fields=["lifetime_points", "updated_at"])
        account.refresh_from_db(fields=["lifetime_points", "liquid_points", "updated_at"])
    outstanding = ReferralReward.objects.filter(
        user_id=user_id,
        brand_id=brand_id,
        reward_type__in=["normal_point", "profile_point"],
        status=ReferralReward.RewardStatus.PROCESSED,
        is_cashed_out=False,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if account.liquid_points != outstanding:
        account.liquid_points = outstanding
        account.save(update_fields=["liquid_points", "updated_at"])
    return account


@transaction.atomic
def cash_out_completed_pills(*, user_id: int, brand_id: int) -> Decimal:
    """Settle full three-pill boxes, preserving each pill's recorded cash value."""
    # Serialize cash-out for all reward sources of the same customer.
    _update_reward_account(user_id=user_id, brand_id=brand_id)
    rewards = list(
        ReferralReward.objects.select_for_update()
        .filter(
            user_id=user_id,
            brand_id=brand_id,
            reward_type__in=["normal_point", "profile_point"],
            status=ReferralReward.RewardStatus.PROCESSED,
            is_cashed_out=False,
        )
        # A challenge pill joins the ordinary box only when it is released.
        # Before release it has a creation time but no processed_at value.
        .order_by(Coalesce("processed_at", "created_at"), "pk")
    )
    complete_count = (len(rewards) // POINTS_PER_PILL) * POINTS_PER_PILL
    if complete_count == 0:
        _update_reward_account(user_id=user_id, brand_id=brand_id)
        return Decimal("0")

    wallet = _wallet_for(user_id=user_id, brand_id=brand_id)
    total_paid = Decimal("0")
    now = timezone.now()
    for offset in range(0, complete_count, POINTS_PER_PILL):
        group = rewards[offset : offset + POINTS_PER_PILL]
        amount = _money(sum((item.cash_value for item in group), Decimal("0")), wallet.currency)
        ids = "-".join(str(item.pk) for item in group)
        key = f"referral-pill:{brand_id}:{user_id}:{ids}"
        if amount > 0:
            credit_wallet(
                wallet_id=wallet.pk,
                amount=amount,
                transaction_type=WalletTransaction.TransactionType.REFERRAL_REWARD,
                reference_id=key,
                idempotency_key=key,
                description="نقد شدن ارزش سه قرص",
                metadata={"reward_ids": [item.pk for item in group], "point_count": 3},
            )
            total_paid += amount
        ReferralReward.objects.filter(pk__in=[item.pk for item in group]).update(
            is_cashed_out=True, cashed_out_at=now
        )
    _update_reward_account(user_id=user_id, brand_id=brand_id)
    return total_paid


@transaction.atomic
def choose_challenge_tier(*, user_id: int, brand_id: int, tier_id: int) -> UserChallenge:
    program = ChallengeProgram.objects.select_for_update().filter(
        brand_id=brand_id, is_active=True
    ).first()
    if not program:
        raise ChallengeError("چالش فعالی برای این برند تعریف نشده است.")
    try:
        tier = ChallengeTier.objects.get(pk=tier_id, program=program, is_active=True)
    except ChallengeTier.DoesNotExist as exc:
        raise ChallengeError("سطح انتخاب‌شده معتبر نیست.") from exc
    try:
        challenge = UserChallenge.objects.select_for_update().get(
            user_id=user_id, brand_id=brand_id, program=program
        )
    except UserChallenge.DoesNotExist as exc:
        raise ChallengeError("این چالش هنوز برای شما فعال نشده است.") from exc
    if challenge.status not in {UserChallenge.Status.OFFERED, UserChallenge.Status.AWAITING_FUNDS}:
        raise ChallengeError("این فرصت چالش دیگر قابل انتخاب نیست.")
    if challenge.entry_payment_id and Payment.objects.filter(
        pk=challenge.entry_payment_id, status__in=["pending", "awaiting_confirmation", "confirmed"]
    ).exists():
        raise ChallengeError("رسید ورودی چالش ثبت شده و در انتظار بررسی است.")
    challenge.tier = tier
    challenge.entry_amount = tier.entry_fee
    challenge.target_referrals = tier.target_referrals
    challenge.reward_percent = program.reward_percent
    challenge.accepted_at = challenge.accepted_at or timezone.now()
    challenge.status = UserChallenge.Status.AWAITING_FUNDS
    challenge.save(
        update_fields=[
            "tier",
            "entry_amount",
            "target_referrals",
            "reward_percent",
            "accepted_at",
            "status",
            "updated_at",
        ]
    )
    return challenge


@transaction.atomic
def decline_challenge(*, user_id: int, brand_id: int) -> UserChallenge:
    try:
        challenge = UserChallenge.objects.select_for_update().select_related("program").get(
            user_id=user_id,
            brand_id=brand_id,
            status=UserChallenge.Status.OFFERED,
        )
    except UserChallenge.DoesNotExist as exc:
        raise ChallengeError("این پیشنهاد دیگر قابل رد کردن نیست.") from exc
    challenge.status = UserChallenge.Status.DECLINED
    challenge.declined_at = timezone.now()
    challenge.save(update_fields=["status", "declined_at", "updated_at"])
    return challenge


@transaction.atomic
def create_challenge_entry_payment(*, user_id: int, brand_id: int, challenge_id: int,
                                   card_id: int, receipt_file: str) -> Payment:
    """A separate, cash-only receipt; no wallet or order balance is involved."""
    from apps.orders.models import PaymentCard

    challenge = (
        UserChallenge.objects.select_for_update(of=("self",))
        .select_related("program", "tier")
        .filter(pk=challenge_id, user_id=user_id, brand_id=brand_id,
                status=UserChallenge.Status.AWAITING_FUNDS)
        .first()
    )
    if not challenge or not challenge.program.is_active or not challenge.tier_id:
        raise ChallengeError("چالش در وضعیت قابل پرداخت نیست.")
    card = PaymentCard.objects.filter(pk=card_id, brand_id=brand_id, is_active=True).first()
    if not card:
        raise ChallengeError("کارت پرداخت انتخاب‌شده فعال نیست.")
    amount = _money(challenge.entry_amount, challenge.program.brand.currency)
    if amount <= 0 or not receipt_file:
        raise ChallengeError("اطلاعات واریز معتبر نیست.")
    if challenge.entry_payment_id:
        previous = Payment.objects.select_for_update().get(pk=challenge.entry_payment_id)
        if previous.status in {Payment.PaymentStatus.PENDING, Payment.PaymentStatus.AWAITING_CONFIRMATION}:
            raise ChallengeError("رسید قبلی در انتظار بررسی است.")
        if previous.status == Payment.PaymentStatus.CONFIRMED:
            raise ChallengeError("این ورودی قبلاً تأیید شده است.")
    payment = Payment.objects.create(
        order=None, wallet=None, brand_id=brand_id, user_id=user_id,
        payment_method=Payment.PaymentMethod.CARD_TRANSFER,
        status=Payment.PaymentStatus.AWAITING_CONFIRMATION,
        amount=amount, currency=challenge.program.brand.currency,
        receipt_file=receipt_file,
        card_number=card.card_number, cardholder_name=card.cardholder_name,
        notes=f"واریز ورودی {challenge.program.name}",
    )
    challenge.entry_payment = payment
    challenge.save(update_fields=["entry_payment", "updated_at"])
    return payment


@transaction.atomic
def activate_challenge_from_payment(*, payment_id: int) -> UserChallenge:
    """Activate once, on verified external cash. Never spend wallet credit."""
    challenge = (
        UserChallenge.objects.select_for_update(of=("self",))
        .select_related("program", "tier", "entry_payment")
        .filter(entry_payment_id=payment_id)
        .first()
    )
    if not challenge:
        raise ChallengeError("ورودی چالش پیدا نشد.")
    if challenge.status == UserChallenge.Status.ACTIVE:
        return challenge
    if challenge.status != UserChallenge.Status.AWAITING_FUNDS or not challenge.tier_id:
        raise ChallengeError("وضعیت چالش برای فعال‌سازی معتبر نیست.")
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if (payment.status != Payment.PaymentStatus.CONFIRMED
            or payment.wallet_id is not None or payment.order_id is not None
            or payment.user_id != challenge.user_id or payment.brand_id != challenge.brand_id
            or payment.payment_method != Payment.PaymentMethod.CARD_TRANSFER
            or not payment.receipt_file
            or payment.currency != challenge.program.brand.currency
            or payment.amount != challenge.entry_amount):
        raise ChallengeError("تراکنش ورودی چالش معتبر نیست.")
    wallet = _wallet_for(user_id=challenge.user_id, brand_id=challenge.brand_id)
    Wallet.objects.filter(pk=wallet.pk).update(
        challenge_frozen_balance=F("challenge_frozen_balance") + payment.amount
    )
    now = payment.verified_at or timezone.now()
    challenge.status = UserChallenge.Status.ACTIVE
    challenge.starts_at = now
    challenge.ends_at = now + timedelta(days=challenge.program.duration_days)
    challenge.require_referral_join_during_challenge = True
    challenge.save(update_fields=["status", "starts_at", "ends_at",
                                  "require_referral_join_during_challenge", "updated_at"])
    from apps.bot.analytics import record_event
    record_event(user_id=challenge.user_id, brand_id=challenge.brand_id,
                 event="challenge_activated", value=challenge.target_referrals,
                 metadata={"challenge_id": challenge.pk, "payment_id": payment.pk},
                 dedupe_key=f"challenge-active:{challenge.pk}")
    def notify():
        from .tasks import _queue_once
        from utils.jalali import jalali_date
        from zoneinfo import ZoneInfo
        from django.utils import timezone as tz
        end = tz.localtime(challenge.ends_at, ZoneInfo(challenge.program.brand.timezone))
        end_label = jalali_date(end, time=True)
        _queue_once(
            user=challenge.user, brand_id=challenge.brand_id,
            notification_type="custom", dedupe_key=f"challenge-start:{challenge.pk}",
            text=(
                f"✅ چالش {challenge.program.name} شروع شد!\n"
                f"زمان چالش {challenge.program.duration_days} روز کامل از الان شروع شد!\n"
                f"زمان پایان: {end_label}\n\n"
                "👨🏾‍⚕️<b>نکته اخلاقی بسیار مهم:</b>\n"
                "🦠این سیستم برای رایگان شدن دائمی خدمات ساخته شده است. "
                "این چالش فقط برای سرعت بخشیدن به این مسیر طراحی شده.\n"
                "<b>لینک معرف خودتان را فقط در اختیار اطرافیان مطمئن قرار دهید.</b>\n\n"
                "👇🏾همین الان شروع کن:"
            ),
            buttons_data=[
                [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                [{"text": "📑 توضیحات کامل چالش", "callback_data": f"challenge_terms_{challenge.pk}"}],
            ],
            metadata={"challenge_id": challenge.pk, "event": "challenge_started"},
        )
    transaction.on_commit(notify)
    return challenge


def _release_frozen_entry(*, challenge: UserChallenge, wallet: Wallet) -> None:
    amount = _money(challenge.entry_amount, wallet.currency)
    if amount <= 0:
        return
    wallet.refresh_from_db(fields=["challenge_frozen_balance", "balance", "is_active", "is_frozen"])
    if wallet.challenge_frozen_balance < amount:
        raise ChallengeError("وجه فیریز شده چالش با مبلغ ثبت‌شده تطابق ندارد.")
    Wallet.objects.filter(pk=wallet.pk).update(
        challenge_frozen_balance=F("challenge_frozen_balance") - amount
    )
    credit_wallet(
        wallet_id=wallet.pk,
        amount=amount,
        transaction_type=WalletTransaction.TransactionType.REFUND,
        reference_id=f"challenge:{challenge.pk}",
        idempotency_key=f"challenge-entry-release:{challenge.pk}",
        description=f"بازگشت وجه ورودی چالش {challenge.program.name}",
        metadata={"challenge_id": challenge.pk, "action": "release_entry"},
    )


def _mark_referral_converted(*, referral: Referral, order: Order) -> None:
    now = timezone.now()
    if referral.status == Referral.ReferralStatus.PENDING:
        referral.status = Referral.ReferralStatus.CONVERTED
    if referral.conversion_order_id is None:
        referral.conversion_order = order
    if referral.converted_at is None:
        referral.converted_at = now
    referral.save(update_fields=["status", "conversion_order", "converted_at"])


def _mark_challenge_rewarded(*, referral: Referral, points: Decimal, rewarded_at) -> None:
    """Finalize a provisional challenge conversion after the challenge succeeds."""
    referral.status = Referral.ReferralStatus.REWARDED
    referral.referrer_reward_amount += points
    referral.rewarded_at = rewarded_at
    referral.save(update_fields=["status", "referrer_reward_amount", "rewarded_at"])


@transaction.atomic
def maybe_record_challenge_referral(*, referral: Referral, order: Order, at=None, challenge_id=None) -> ReferralReward | None:
    """Reserve one purchase for an active challenge, until its selected target is full."""
    now = at or order.payments.filter(status="confirmed").aggregate(paid_at=Max(Coalesce("verified_at", "created_at")))["paid_at"] or timezone.now()
    challenge = (
        UserChallenge.objects.select_for_update(of=("self",))
        .select_related("program", "tier")
        .filter(
            user_id=referral.referrer_id,
            brand_id=order.brand_id,
            status=UserChallenge.Status.ACTIVE,
            starts_at__lte=now,
            ends_at__gte=now,
            tier__isnull=False,
        )
        .first()
    )
    if not challenge or (challenge_id and challenge.pk != challenge_id) or challenge.successful_referrals >= challenge.target_referrals:
        return None
    # The referred customer must have bought the service personally. A gift
    # purchased by the inviter is not that customer's first paid purchase.
    if order.user_id != referral.referee_id:
        return None
    if ChallengeReferralEvent.objects.filter(order=order).exists():
        event = ChallengeReferralEvent.objects.select_related("reward").get(order=order)
        return event.reward

    # Only the first paid purchase of a newly joined unique referral is reserved.
    if ChallengeReferralEvent.objects.filter(challenge=challenge, referral=referral).exists():
        return None
    if (
        challenge.starts_at
        and referral.created_at < challenge.starts_at
    ):
        return None
    if referral.converted_at and challenge.starts_at and referral.converted_at < challenge.starts_at:
        return None
    if challenge.starts_at:
        confirmed_filter = Q(payments__status=Payment.PaymentStatus.CONFIRMED)
        had_prior_completed_purchase = (
            Order.objects.filter(
                user_id=referral.referee_id,
                brand_id=order.brand_id,
                final_price__gt=0,
                status__in=[
                    Order.OrderStatus.PAID,
                    Order.OrderStatus.PROCESSING,
                    Order.OrderStatus.COMPLETED,
                ],
            )
            .exclude(pk=order.pk)
            .annotate(
                confirmed_total=Sum("payments__amount", filter=confirmed_filter),
                paid_at=Max(Coalesce("payments__verified_at", "payments__created_at"), filter=confirmed_filter),
            )
            .filter(
                confirmed_total__gte=F("final_price"),
                paid_at__lte=now,
            )
            .exists()
        )
        if had_prior_completed_purchase:
            return None

    from apps.subscriptions.policy import effective_reward_percent
    program = ReferralProgram.objects.filter(brand_id=order.brand_id, is_active=True).first()
    if program is None:
        raise ChallengeError("برنامه معرفی فعال پیدا نشد.")
    base_percent = effective_reward_percent(order, program.purchase_reward_percent)
    applied_percent = min(Decimal("100"), base_percent * challenge.program.success_multiplier)
    reward_value = _money(order.final_price * applied_percent / Decimal("100"), order.currency)
    fallback_reward_value = _money(
        order.final_price * base_percent * challenge.program.failure_multiplier / Decimal("100"),
        order.currency,
    )
    reward = ReferralReward.objects.create(
        referral=referral,
        order=order,
        user_id=referral.referrer_id,
        brand_id=order.brand_id,
        reward_type="challenge_point",
        amount=Decimal("1"),
        cash_value=reward_value,
        currency=order.currency,
        status=ReferralReward.RewardStatus.PENDING,
        processed_at=None,
        notes=f"Challenge {challenge.program.name}; {applied_percent}% of direct purchase.",
    )
    ChallengeReferralEvent.objects.create(
        challenge=challenge,
        referral=referral,
        order=order,
        reward=reward,
        reward_value=reward_value,
        fallback_reward_value=fallback_reward_value,
    )
    challenge.successful_referrals = F("successful_referrals") + 1
    challenge.save(update_fields=["successful_referrals", "updated_at"])
    challenge.refresh_from_db(fields=["successful_referrals", "status", "updated_at"])
    # Do not increase lifetime/medical-rank points until the challenge actually
    # succeeds. During the active window these rewards are only provisional.
    _mark_referral_converted(referral=referral, order=order)
    if challenge.successful_referrals >= challenge.target_referrals:
        settle_challenge(challenge_id=challenge.pk, force_success=True)
    return reward


@transaction.atomic
def settle_challenge(*, challenge_id: int, force_success: bool = False) -> UserChallenge:
    challenge = (
        UserChallenge.objects.select_for_update(of=("self",))
        .select_related("program", "tier")
        .get(pk=challenge_id)
    )
    if challenge.status in {UserChallenge.Status.SUCCEEDED, UserChallenge.Status.FAILED}:
        return challenge
    if challenge.status != UserChallenge.Status.ACTIVE or not challenge.tier_id:
        raise ChallengeError("چالش در وضعیت قابل تسویه نیست.")
    now = timezone.now()
    success = challenge.successful_referrals >= challenge.target_referrals
    if not success and (not challenge.ends_at or now < challenge.ends_at):
        raise ChallengeError("مهلت چالش هنوز تمام نشده است.")

    wallet = _wallet_for(user_id=challenge.user_id, brand_id=challenge.brand_id)
    _release_frozen_entry(challenge=challenge, wallet=wallet)
    events = list(
        ChallengeReferralEvent.objects.select_related("reward", "referral", "order")
        .filter(challenge=challenge).order_by("created_at", "pk")
    )
    total_value = Decimal("0")
    from apps.subscriptions.policy import effective_reward_percent
    program = ReferralProgram.objects.filter(brand_id=challenge.brand_id, is_active=True).first()
    if not success and any(event.fallback_reward_value is None for event in events) and program is None:
        raise ChallengeError("تنظیمات پاداش معرفی عادی یافت نشد.")
    for event in events:
        reward = ReferralReward.objects.select_for_update().get(pk=event.reward_id)
        if success:
            # Saved at purchase time. Subsequent rate changes cannot reprice it.
            value = event.reward_value
        elif event.fallback_reward_value is not None:
            value = event.fallback_reward_value
        else:
            # Previously recorded events have no fallback snapshot.
            percent = effective_reward_percent(event.order, program.purchase_reward_percent)
            value = _money(event.order.final_price * percent * challenge.program.failure_multiplier
                           / Decimal("100"), event.order.currency)
        reward.reward_type = "normal_point"
        reward.cash_value = value
        reward.status = ReferralReward.RewardStatus.PROCESSED
        reward.processed_at = now
        reward.notes = "Challenge reward settled"
        reward.save(update_fields=["reward_type", "cash_value", "status", "processed_at", "notes"])
        _mark_challenge_rewarded(referral=event.referral, points=Decimal("1"), rewarded_at=now)
        total_value += value
    if events:
        _update_reward_account(
            user_id=challenge.user_id, brand_id=challenge.brand_id,
            earned_points=Decimal(len(events)),
        )
        cash_out_completed_pills(user_id=challenge.user_id, brand_id=challenge.brand_id)
    challenge.status = UserChallenge.Status.SUCCEEDED if success else UserChallenge.Status.FAILED
    challenge.reward_amount = _money(total_value, wallet.currency)
    challenge.settled_at = now
    challenge.save(update_fields=["status", "reward_amount", "settled_at", "updated_at"])
    from apps.bot.analytics import record_event
    record_event(user_id=challenge.user_id, brand_id=challenge.brand_id, event="challenge_settled", value=challenge.status, metadata={"challenge_id": challenge.pk, "target": challenge.target_referrals, "progress": challenge.successful_referrals, "entry_returned": str(challenge.entry_amount), "reward": str(challenge.reward_amount)}, dedupe_key=f"challenge-settled:{challenge.pk}")
    def notify_end():
        from .tasks import _queue_once
        if success:
            result = (f"شما چالش را با موفقیت به اتمام رساندید و "
                      f"{len(events)} قرص پراپزینو دریافت کردید.")
        else:
            result = ("شما موفق به اتمام کامل چالش نشدید و امتیاز عادی "
                      "از معرفی‌های موفق خود دریافت کردید.\n"
                      "شما همچنان می‌توانید با کمی معرفی بیشتر به مرحله "
                      "رایگان شدن دائمی خدمات برسید.")
        _queue_once(
            user=challenge.user, brand_id=challenge.brand_id,
            notification_type="custom", dedupe_key=f"challenge-ended:{challenge.pk}",
            text=(f"چالش {challenge.program.name} شما به اتمام رسید.\n"
                  f"{result}\n"
                  f"مبلغ ورودی فیریز شده چالش {challenge.program.name} "
                  "به امتیازات نقد شما واریز شد."),
            buttons_data=[[{"text": "💊 امتیازات من", "callback_data": "rewards"}]],
            metadata={"challenge_id": challenge.pk, "event": "challenge_settled"},
        )
    transaction.on_commit(notify_end)
    _update_reward_account(user_id=challenge.user_id, brand_id=challenge.brand_id)
    return challenge
