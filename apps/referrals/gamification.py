"""Transactional services for referral cash points and the Propzino challenge."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F, Max, Q, Sum
from django.utils import timezone

from apps.brands.models import Brand
from apps.orders.models import Order, Payment, Wallet, WalletTransaction
from apps.orders.services import WalletOperationError, credit_wallet, debit_wallet

from .models import (
    ChallengeProgram,
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


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _wallet_for(*, user_id: int, brand_id: int) -> Wallet:
    brand_currency = Brand.objects.values_list("currency", flat=True).get(pk=brand_id)
    wallet, _ = Wallet.objects.get_or_create(
        user_id=user_id,
        brand_id=brand_id,
        defaults={"currency": brand_currency},
    )
    if wallet.currency != brand_currency:
        raise ChallengeError("Wallet currency does not match the brand currency")
    return wallet


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
        reward_type="normal_point",
        status=ReferralReward.RewardStatus.PROCESSED,
        is_cashed_out=False,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if account.liquid_points != outstanding:
        account.liquid_points = outstanding
        account.save(update_fields=["liquid_points", "updated_at"])
    return account


@transaction.atomic
def cash_out_completed_pills(*, user_id: int, brand_id: int) -> Decimal:
    """Convert each complete group of three normal referral points to wallet cash."""
    rewards = list(
        ReferralReward.objects.select_for_update()
        .filter(
            user_id=user_id,
            brand_id=brand_id,
            reward_type="normal_point",
            status=ReferralReward.RewardStatus.PROCESSED,
            is_cashed_out=False,
        )
        .order_by("created_at", "pk")
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
        amount = _money(sum((item.cash_value for item in group), Decimal("0")))
        ids = "-".join(str(item.pk) for item in group)
        key = f"referral-pill:{brand_id}:{user_id}:{ids}"
        if amount > 0:
            credit_wallet(
                wallet_id=wallet.pk,
                amount=amount,
                transaction_type=WalletTransaction.TransactionType.REFERRAL_REWARD,
                reference_id=key,
                idempotency_key=key,
                description="تبدیل سه امتیاز معرفی به موجودی نقد کیف پول",
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
def activate_challenge_from_wallet(*, user_id: int, brand_id: int) -> UserChallenge:
    try:
        challenge = (
            UserChallenge.objects.select_for_update(of=("self",))
            .select_related("program", "tier")
            .get(
                user_id=user_id,
                brand_id=brand_id,
                status=UserChallenge.Status.AWAITING_FUNDS,
            )
        )
    except UserChallenge.DoesNotExist as exc:
        raise ChallengeError("چالشی در انتظار پرداخت پیدا نشد.") from exc
    if not challenge.program.is_active or not challenge.tier_id or not challenge.tier.is_active:
        raise ChallengeError("تنظیمات این چالش دیگر فعال نیست.")
    if challenge.target_referrals <= 0 or challenge.reward_percent <= 0:
        raise ChallengeError("تنظیمات ثبت‌شده این چالش معتبر نیست.")
    amount = _money(challenge.entry_amount)
    if amount <= 0:
        raise ChallengeError("مبلغ ورودی چالش معتبر نیست.")
    wallet = _wallet_for(user_id=user_id, brand_id=brand_id)
    try:
        debit_wallet(
            wallet_id=wallet.pk,
            amount=amount,
            transaction_type=WalletTransaction.TransactionType.PAYMENT,
            reference_id=f"challenge:{challenge.pk}",
            idempotency_key=f"challenge-entry:{challenge.pk}",
            description=f"وجه ورودی چالش {challenge.program.name}",
            metadata={"challenge_id": challenge.pk, "action": "freeze_entry"},
        )
    except WalletOperationError as exc:
        raise ChallengeError("موجودی نقد کیف پول برای ورود به چالش کافی نیست.") from exc
    Wallet.objects.filter(pk=wallet.pk).update(
        challenge_frozen_balance=F("challenge_frozen_balance") + amount
    )
    now = timezone.now()
    challenge.status = UserChallenge.Status.ACTIVE
    challenge.starts_at = now
    challenge.ends_at = now + timedelta(days=challenge.program.duration_days)
    challenge.save(update_fields=["status", "starts_at", "ends_at", "updated_at"])
    return challenge


def _release_frozen_entry(*, challenge: UserChallenge, wallet: Wallet) -> None:
    amount = _money(challenge.entry_amount)
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
def maybe_record_challenge_referral(*, referral: Referral, order: Order) -> ReferralReward | None:
    """Reserve one purchase for an active challenge, until its selected target is full."""
    now = timezone.now()
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
    if not challenge or challenge.successful_referrals >= challenge.target_referrals:
        return None
    if ChallengeReferralEvent.objects.filter(order=order).exists():
        event = ChallengeReferralEvent.objects.select_related("reward").get(order=order)
        return event.reward

    # A challenge target represents distinct newly-converted people, not repeated
    # purchases by the same referred user. Existing customers and repeat purchases
    # continue through the normal 8%/three-point rule instead.
    if ChallengeReferralEvent.objects.filter(challenge=challenge, referral=referral).exists():
        return None
    if (
        challenge.program.require_referral_join_during_challenge
        and challenge.starts_at
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
                paid_at=Max("payments__created_at", filter=confirmed_filter),
            )
            .filter(
                confirmed_total__gte=F("final_price"),
                paid_at__lt=challenge.starts_at,
            )
            .exists()
        )
        if had_prior_completed_purchase:
            return None

    reward_value = _money(
        order.final_price * Decimal(challenge.reward_percent) / Decimal("100")
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
        notes=f"Challenge {challenge.program.name}; {challenge.reward_percent}% of direct purchase.",
    )
    ChallengeReferralEvent.objects.create(
        challenge=challenge,
        referral=referral,
        order=order,
        reward=reward,
        reward_value=reward_value,
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
    success = force_success or challenge.successful_referrals >= challenge.target_referrals
    if not success and (not challenge.ends_at or now < challenge.ends_at):
        raise ChallengeError("مهلت چالش هنوز تمام نشده است.")

    wallet = _wallet_for(user_id=challenge.user_id, brand_id=challenge.brand_id)
    _release_frozen_entry(challenge=challenge, wallet=wallet)
    rewards = ReferralReward.objects.select_for_update().filter(
        challenge_event__challenge=challenge
    )
    reward_count = rewards.count()
    reward_amount = _money(rewards.aggregate(total=Sum("cash_value"))["total"] or Decimal("0"))
    if success:
        if reward_amount > 0:
            credit_wallet(
                wallet_id=wallet.pk,
                amount=reward_amount,
                transaction_type=WalletTransaction.TransactionType.REFERRAL_REWARD,
                reference_id=f"challenge:{challenge.pk}",
                idempotency_key=f"challenge-reward:{challenge.pk}",
                description=f"پاداش نقدی چالش {challenge.program.name}",
                metadata={"challenge_id": challenge.pk, "target": challenge.target_referrals},
            )
        rewards.update(
            status=ReferralReward.RewardStatus.PROCESSED,
            processed_at=now,
            is_cashed_out=True,
            cashed_out_at=now,
        )
        if reward_count:
            _update_reward_account(
                user_id=challenge.user_id,
                brand_id=challenge.brand_id,
                earned_points=Decimal(reward_count),
            )
            for event in ChallengeReferralEvent.objects.select_related("referral").filter(
                challenge=challenge
            ):
                _mark_challenge_rewarded(
                    referral=event.referral,
                    points=Decimal("1"),
                    rewarded_at=now,
                )
        challenge.status = UserChallenge.Status.SUCCEEDED
        challenge.reward_amount = reward_amount
    else:
        # Failed challenge points never become usable points. They were kept
        # provisional during the challenge, so there is no account/referral
        # reward counter to undo here.
        rewards.update(
            status=ReferralReward.RewardStatus.CANCELLED,
            processed_at=None,
            is_cashed_out=False,
            cashed_out_at=None,
        )
        challenge.status = UserChallenge.Status.FAILED
        challenge.reward_amount = Decimal("0")
    challenge.settled_at = now
    challenge.save(update_fields=["status", "reward_amount", "settled_at", "updated_at"])
    _update_reward_account(user_id=challenge.user_id, brand_id=challenge.brand_id)
    return challenge
