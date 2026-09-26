"""Transactional referral attribution and reward operations."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_DOWN, localcontext

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Order, Payment, Wallet, WalletTransaction

from .models import (
    Referral,
    ReferralLink,
    ReferralProgram,
    ReferralReward,
    RewardAccount,
    RewardBoxCapacity,
    RewardPointBox,
    RewardPointLedger,
    RewardService,
)

POINT_QUANTUM = Decimal("0.00000001")


class RewardConfigurationError(Exception):
    """A reward program is missing a required, brand-scoped setting."""


@transaction.atomic
def attribute_referral(*, user_id: int, brand_id: int, code: str) -> bool:
    """Set a user's first valid referral attribution for a brand once."""
    try:
        user = User.objects.select_for_update().get(pk=user_id, brand_id=brand_id)
    except User.DoesNotExist:
        return False
    if user.referred_by_id:
        return False

    referral_link = (
        ReferralLink.objects.select_for_update()
        .select_related("user")
        .filter(code=code, brand_id=brand_id, is_active=True)
        .first()
    )
    if (
        not referral_link
        or referral_link.user_id == user.pk
        or referral_link.user.brand_id != brand_id
    ):
        return False

    if Referral.objects.filter(referee_id=user.pk, brand_id=brand_id).exists():
        return False

    try:
        with transaction.atomic():
            Referral.objects.create(
                referrer_id=referral_link.user_id,
                referee_id=user.pk,
                brand_id=brand_id,
                referral_link=referral_link,
                status=Referral.ReferralStatus.PENDING,
            )
    except IntegrityError:
        # A concurrent first attribution won the unique referee/brand slot.
        return False

    user.referred_by_id = referral_link.user_id
    user.save(update_fields=["referred_by", "updated_at"])
    User.objects.filter(pk=referral_link.user_id).update(
        referral_count=F("referral_count") + 1
    )
    ReferralLink.objects.filter(pk=referral_link.pk).update(
        conversion_count=F("conversion_count") + 1
    )
    return True


def _service_value(service: RewardService) -> Decimal:
    value = service.plan.price - service.plan.upstream_cost
    if value <= 0:
        raise RewardConfigurationError("Reward service must have positive profit")
    return value


def _quantize_points(value: Decimal) -> Decimal:
    return value.quantize(POINT_QUANTUM, rounding=ROUND_DOWN)


def _seed_box_cycle(account: RewardAccount, service: RewardService) -> None:
    capacities = list(service.box_capacities.order_by("sequence"))
    if not capacities:
        raise RewardConfigurationError("Reward service has no point-box capacities")
    point_value = _service_value(service)
    RewardPointBox.objects.bulk_create(
        [
            RewardPointBox(
                account=account,
                service=service,
                cycle=account.next_box_cycle,
                sequence=capacity.sequence,
                capacity=capacity.capacity,
                point_value_snapshot=point_value,
            )
            for capacity in capacities
        ]
    )
    account.next_box_cycle += 1
    account.save(update_fields=["next_box_cycle", "updated_at"])


def _place_points(
    account: RewardAccount,
    service: RewardService,
    points: Decimal,
    *,
    source_key: str,
) -> None:
    remaining = _quantize_points(points)
    while remaining > 0:
        box = (
            RewardPointBox.objects.select_for_update()
            .filter(
                account=account,
                service=service,
                state=RewardPointBox.State.OPEN,
            )
            .order_by("cycle", "sequence")
            .first()
        )
        if box is None:
            _seed_box_cycle(account, service)
            box = (
                RewardPointBox.objects.select_for_update()
                .filter(
                    account=account,
                    service=service,
                    state=RewardPointBox.State.OPEN,
                )
                .order_by("cycle", "sequence")
                .first()
            )
        if box is None:
            raise RewardConfigurationError("Could not create the next point box")

        added = min(remaining, box.capacity - box.filled)
        box.filled += added
        remaining -= added
        if box.filled == box.capacity:
            box.state = RewardPointBox.State.COMPLETE
            box.completed_at = timezone.now()
            account.liquid_points += box.capacity
            RewardPointLedger.objects.create(
                account=account,
                service=service,
                entry_type=RewardPointLedger.EntryType.BOX_COMPLETED,
                points_delta=Decimal("0"),
                value_delta=Decimal("0"),
                point_value_snapshot=box.point_value_snapshot,
                idempotency_key=f"{source_key}:box:{box.pk or account.next_box_cycle}:{box.sequence}",
            )
        box.save(update_fields=["filled", "state", "completed_at"])


def _locked_reward_account(*, user_id: int, brand_id: int) -> RewardAccount:
    try:
        return RewardAccount.objects.select_for_update().get(
            user_id=user_id, brand_id=brand_id
        )
    except RewardAccount.DoesNotExist:
        try:
            with transaction.atomic():
                RewardAccount.objects.create(user_id=user_id, brand_id=brand_id)
        except IntegrityError:
            pass
        return RewardAccount.objects.select_for_update().get(
            user_id=user_id, brand_id=brand_id
        )


def _credit_wallet_once(
    *, user_id: int, brand_id: int, amount: Decimal, idempotency_key: str, description: str
) -> WalletTransaction:
    if amount <= 0:
        raise ValueError("Wallet credit must be positive")
    wallet, _ = Wallet.objects.get_or_create(
        user_id=user_id,
        brand_id=brand_id,
        defaults={"currency": "USD"},
    )
    wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    existing = WalletTransaction.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    balance_before = wallet.balance
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type=WalletTransaction.TransactionType.BONUS,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        reference_id=idempotency_key,
        idempotency_key=idempotency_key,
        description=description,
    )


def _rebase_account(
    account: RewardAccount, new_service: RewardService, *, reason_key: str
) -> None:
    old_service = account.reference_service
    if old_service_id := account.reference_service_id:
        if old_service_id == new_service.pk:
            return
    else:
        account.reference_service = new_service
        account.save(update_fields=["reference_service", "updated_at"])
        _seed_box_cycle(account, new_service)
        return

    old_boxes = list(
        RewardPointBox.objects.select_for_update().filter(
            account=account,
            service=old_service,
            state__in=[RewardPointBox.State.OPEN, RewardPointBox.State.COMPLETE],
        )
    )
    completed_value = sum(
        (box.capacity * box.point_value_snapshot for box in old_boxes if box.state == RewardPointBox.State.COMPLETE),
        Decimal("0"),
    )
    incomplete_value = sum(
        (box.filled * box.point_value_snapshot for box in old_boxes if box.state == RewardPointBox.State.OPEN),
        Decimal("0"),
    )
    completed_points = sum(
        (box.capacity for box in old_boxes if box.state == RewardPointBox.State.COMPLETE),
        Decimal("0"),
    )
    incomplete_points = sum(
        (box.filled for box in old_boxes if box.state == RewardPointBox.State.OPEN),
        Decimal("0"),
    )

    if completed_value > 0:
        _credit_wallet_once(
            user_id=account.user_id,
            brand_id=account.brand_id,
            amount=completed_value,
            idempotency_key=reason_key,
            description="Completed reward points converted after reference service change",
        )
        RewardPointLedger.objects.create(
            account=account,
            service=old_service,
            entry_type=RewardPointLedger.EntryType.CONVERTED_TO_WALLET,
            points_delta=-completed_points,
            value_delta=completed_value,
            point_value_snapshot=_service_value(old_service),
            idempotency_key=f"{reason_key}:completed",
        )
        account.liquid_points = max(
            Decimal("0"), account.liquid_points - completed_points
        )

    for box in old_boxes:
        box.state = RewardPointBox.State.CONVERTED
        box.save(update_fields=["state"])

    rebased_points = Decimal("0")
    if incomplete_value > 0:
        new_value = _service_value(new_service)
        with localcontext() as context:
            context.prec = 32
            rebased_points = _quantize_points(incomplete_value / new_value)
        RewardPointLedger.objects.create(
            account=account,
            service=old_service,
            entry_type=RewardPointLedger.EntryType.SERVICE_REBASE,
            points_delta=-incomplete_points,
            value_delta=-incomplete_value,
            point_value_snapshot=_service_value(old_service),
            idempotency_key=f"{reason_key}:old-incomplete",
        )

    account.reference_service = new_service
    account.save(update_fields=["reference_service", "liquid_points", "updated_at"])
    if rebased_points > 0:
        _seed_box_cycle(account, new_service)
        _place_points(account, new_service, rebased_points, source_key=reason_key)
        RewardPointLedger.objects.create(
            account=account,
            service=new_service,
            entry_type=RewardPointLedger.EntryType.SERVICE_REBASE,
            points_delta=rebased_points,
            value_delta=incomplete_value,
            point_value_snapshot=_service_value(new_service),
            idempotency_key=f"{reason_key}:new-incomplete",
        )
    elif not RewardPointBox.objects.filter(
        account=account, service=new_service, state=RewardPointBox.State.OPEN
    ).exists():
        _seed_box_cycle(account, new_service)


@transaction.atomic
def set_active_reference_service(*, brand_id: int, service_id: int) -> None:
    """Change a brand's reference service while preserving each account's value."""
    program = ReferralProgram.objects.select_for_update().get(brand_id=brand_id)
    service = RewardService.objects.select_related("plan").get(
        pk=service_id, brand_id=brand_id, is_active=True
    )
    _service_value(service)
    accounts = RewardAccount.objects.select_for_update().filter(brand_id=brand_id)
    for account in accounts:
        _rebase_account(
            account,
            service,
            reason_key=f"reference-service-change:{account.pk}:{service.pk}:{account.next_box_cycle}",
        )
    program.reference_service = service
    program.save(update_fields=["reference_service", "updated_at"])


@transaction.atomic
def award_level_one_referral_for_payment(*, payment_id: str) -> Decimal:
    """Credit points from one profitable, confirmed level-one referral purchase."""
    payment = Payment.objects.select_for_update().select_related("order").get(
        payment_id=payment_id
    )
    if payment.status != Payment.PaymentStatus.CONFIRMED or not payment.order_id:
        return Decimal("0")
    order = (
        Order.objects.select_for_update()
        .select_related("brand", "user", "plan")
        .get(pk=payment.order_id)
    )
    if ReferralReward.objects.filter(order=order).exists():
        return Decimal("0")
    try:
        program = ReferralProgram.objects.select_for_update().get(
            brand_id=order.brand_id, is_active=True
        )
        referral = Referral.objects.select_for_update().get(
            referee_id=order.user_id,
            brand_id=order.brand_id,
            status=Referral.ReferralStatus.PENDING,
        )
    except (ReferralProgram.DoesNotExist, Referral.DoesNotExist):
        return Decimal("0")
    now = timezone.now()
    expiry = referral.expires_at or (
        referral.created_at + timedelta(days=program.conversion_window_days)
    )
    if now > expiry:
        referral.status = Referral.ReferralStatus.EXPIRED
        referral.save(update_fields=["status"])
        return Decimal("0")
    if program.require_purchase and order.final_price < program.minimum_purchase_amount:
        return Decimal("0")
    reference_service = program.reference_service
    lifetime_service = program.lifetime_reference_service or reference_service
    if not reference_service or not lifetime_service:
        raise RewardConfigurationError("Referral point reference services are not configured")
    if (
        reference_service.brand_id != order.brand_id
        or lifetime_service.brand_id != order.brand_id
        or reference_service.plan.currency != order.currency
        or lifetime_service.plan.currency != order.currency
    ):
        raise RewardConfigurationError("Referral point service currencies do not match")
    point_value = _service_value(reference_service)
    lifetime_value = _service_value(lifetime_service)
    profit = order.final_price - order.plan.upstream_cost
    if profit <= 0:
        return Decimal("0")
    with localcontext() as context:
        context.prec = 32
        points = _quantize_points(profit / point_value)
        lifetime_points = _quantize_points(profit / lifetime_value)
    if points <= 0:
        return Decimal("0")

    reward = ReferralReward.objects.create(
        referral=referral,
        order=order,
        user_id=referral.referrer_id,
        brand_id=order.brand_id,
        reward_type="points",
        amount=points,
        currency=order.currency,
        status=ReferralReward.RewardStatus.PROCESSED,
        processed_at=now,
        notes="Level-one referral profit converted using the active reference service.",
    )
    account = _locked_reward_account(
        user_id=referral.referrer_id, brand_id=order.brand_id
    )
    if account.reference_service_id != reference_service.pk:
        _rebase_account(
            account,
            reference_service,
            reason_key=f"lazy-reference-service-change:{account.pk}:{reference_service.pk}:{account.next_box_cycle}",
        )
    if not RewardPointBox.objects.filter(
        account=account, service=reference_service, state=RewardPointBox.State.OPEN
    ).exists():
        _seed_box_cycle(account, reference_service)
    ledger_key = f"payment-referral-points:{payment.payment_id}"
    RewardPointLedger.objects.create(
        account=account,
        service=reference_service,
        order=order,
        referral_reward=reward,
        entry_type=RewardPointLedger.EntryType.EARNED,
        points_delta=points,
        value_delta=profit,
        point_value_snapshot=point_value,
        idempotency_key=ledger_key,
    )
    _place_points(account, reference_service, points, source_key=ledger_key)
    account.lifetime_points += lifetime_points
    account.lifetime_profit += profit
    account.save(
        update_fields=["lifetime_points", "lifetime_profit", "liquid_points", "updated_at"]
    )
    referral.status = Referral.ReferralStatus.REWARDED
    referral.conversion_order = order
    referral.converted_at = now
    referral.referrer_reward_amount = points
    referral.rewarded_at = now
    referral.save(
        update_fields=[
            "status",
            "conversion_order",
            "converted_at",
            "referrer_reward_amount",
            "rewarded_at",
        ]
    )
    current_level = (
        program.levels.filter(min_lifetime_points__lte=account.lifetime_points)
        .order_by("-min_lifetime_points", "-level")
        .first()
    )
    if current_level:
        User.objects.filter(pk=account.user_id).update(level=current_level.level)
    return points
