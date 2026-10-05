"""Transactional referral attribution and reward operations."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation, localcontext

from django.db import IntegrityError, connection, transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import Order, Payment, Wallet, WalletTransaction
from apps.orders.services import credit_wallet
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.vpn_providers.models import VPNProvider

from .models import (
    Achievement,
    Referral,
    ReferralClick,
    ReferralLink,
    ReferralProgram,
    ReferralReward,
    RewardAccount,
    RewardPointBox,
    RewardPointLedger,
    RewardRedemption,
    RewardService,
    UserAchievement,
)

POINT_QUANTUM = Decimal("0.00000001")


class RewardConfigurationError(Exception):
    """A reward program is missing a required, brand-scoped setting."""


class RewardRedemptionError(Exception):
    """The account cannot redeem the configured service in its current state."""


class AchievementClaimError(Exception):
    """An achievement reward is not claimable or is missing valid configuration."""


def _record_zero_referral_reward(
    *, referral: Referral, order: Order, currency: str, reason: str
) -> None:
    ReferralReward.objects.create(
        referral=referral,
        order=order,
        user_id=referral.referrer_id,
        brand_id=order.brand_id,
        reward_type="points",
        amount=Decimal("0"),
        currency=currency,
        status=ReferralReward.RewardStatus.CANCELLED,
        processed_at=timezone.now(),
        notes=reason,
    )


@transaction.atomic
def track_referral_click(
    *, code: str, brand_id: int, visitor_id: int | None = None
) -> bool:
    """Count one branded bot start per visitor without granting attribution."""
    link = ReferralLink.objects.filter(
        code=code, brand_id=brand_id, is_active=True
    ).first()
    if not link or (visitor_id is not None and link.user_id == visitor_id):
        return False
    if visitor_id is not None:
        if not User.objects.filter(pk=visitor_id, brand_id=brand_id).exists():
            return False
        table = connection.ops.quote_name(ReferralClick._meta.db_table)
        link_column = connection.ops.quote_name(
            ReferralClick._meta.get_field("link").column
        )
        visitor_column = connection.ops.quote_name(
            ReferralClick._meta.get_field("visitor").column
        )
        brand_column = connection.ops.quote_name(
            ReferralClick._meta.get_field("brand").column
        )
        created_column = connection.ops.quote_name(
            ReferralClick._meta.get_field("created_at").column
        )
        constraint = connection.ops.quote_name("uniq_referral_visit_per_user")
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} "
                f"({link_column}, {visitor_column}, {brand_column}, {created_column}) "
                f"VALUES (%s, %s, %s, %s) "
                f"ON CONFLICT ON CONSTRAINT {constraint} DO NOTHING RETURNING id",
                [link.pk, visitor_id, brand_id, timezone.now()],
            )
            if cursor.fetchone() is None:
                # Repeated starts are expected; avoid raising a uniqueness error
                # just to determine that this visitor was already counted.
                return False
    updated = ReferralLink.objects.filter(pk=link.pk).update(
        click_count=F("click_count") + 1
    )
    return bool(updated)


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

    program = (
        ReferralProgram.objects.select_for_update()
        .filter(brand_id=brand_id, is_active=True)
        .first()
    )
    if program:
        if program.require_phone_verification and (
            not user.phone_number or not user.is_verified
        ):
            return False
        now = timezone.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        if (
            program.max_referrals_per_day
            and Referral.objects.filter(
                referrer_id=referral_link.user_id,
                brand_id=brand_id,
                created_at__gte=day_start,
            ).count()
            >= program.max_referrals_per_day
        ):
            return False
        if (
            program.max_referrals_per_month
            and Referral.objects.filter(
                referrer_id=referral_link.user_id,
                brand_id=brand_id,
                created_at__gte=month_start,
            ).count()
            >= program.max_referrals_per_month
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
    value = service.point_value_snapshot
    if value is None:
        value = service.plan.price - service.plan.upstream_cost
    if value <= 0:
        raise RewardConfigurationError("Reward service must have positive profit")
    return value


def validate_reward_service_configuration(service: RewardService) -> Decimal:
    """Validate the monetary and box settings required for a reference service."""
    if not service.is_active or not service.plan.is_active:
        raise RewardConfigurationError("Reference service and plan must be active")
    if not service.plan.currency:
        raise RewardConfigurationError("Reference plan must have a currency")
    if service.free_points <= 0:
        raise RewardConfigurationError("Free point threshold must be positive")
    if not service.box_capacities.exists():
        raise RewardConfigurationError(
            "Reference service must have point-box capacities"
        )
    return _service_value(service)


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
    *,
    user_id: int,
    brand_id: int,
    amount: Decimal,
    idempotency_key: str,
    description: str,
) -> WalletTransaction:
    if amount <= 0:
        raise ValueError("Wallet credit must be positive")
    wallet, _ = Wallet.objects.get_or_create(
        user_id=user_id,
        brand_id=brand_id,
        defaults={
            "currency": Brand.objects.values_list("currency", flat=True).get(
                pk=brand_id
            )
        },
    )
    return credit_wallet(
        wallet_id=wallet.pk,
        amount=amount,
        transaction_type=WalletTransaction.TransactionType.BONUS,
        reference_id=idempotency_key,
        idempotency_key=idempotency_key,
        description=description,
    )


ACHIEVEMENT_METRICS = {
    "referrals",
    "conversions",
    "purchases",
    "lifetime_points",
    "total_spent",
    "wallet_deposits",
}


def _achievement_metrics(*, user_id: int, brand_id: int) -> dict[str, Decimal]:
    paid_orders = Order.objects.filter(
        user_id=user_id,
        brand_id=brand_id,
        final_price__gt=0,
        status__in=(
            Order.OrderStatus.PAID,
            Order.OrderStatus.PROCESSING,
            Order.OrderStatus.COMPLETED,
        ),
    )
    total_spent = paid_orders.aggregate(total=Sum("final_price"))["total"] or Decimal(
        "0"
    )
    account = RewardAccount.objects.filter(user_id=user_id, brand_id=brand_id).first()
    deposits = WalletTransaction.objects.filter(
        wallet__user_id=user_id,
        wallet__brand_id=brand_id,
        transaction_type=WalletTransaction.TransactionType.DEPOSIT,
        amount__gt=0,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return {
        "referrals": Decimal(
            Referral.objects.filter(referrer_id=user_id, brand_id=brand_id).count()
        ),
        "conversions": Decimal(
            Referral.objects.filter(
                referrer_id=user_id,
                brand_id=brand_id,
                status=Referral.ReferralStatus.REWARDED,
            ).count()
        ),
        "purchases": Decimal(paid_orders.count()),
        "lifetime_points": account.lifetime_points if account else Decimal("0"),
        "total_spent": total_spent,
        "wallet_deposits": deposits,
    }


@transaction.atomic
def refresh_user_achievements(*, user_id: int, brand_id: int) -> list[UserAchievement]:
    """Recompute progress from durable account data; safe to call on every view."""
    if not User.objects.filter(pk=user_id, brand_id=brand_id).exists():
        return []
    metrics = _achievement_metrics(user_id=user_id, brand_id=brand_id)
    result = []
    for achievement in Achievement.objects.filter(brand_id=brand_id, is_active=True):
        progress = Decimal("0")
        requirements = achievement.requirements or {}
        valid = bool(requirements) and set(requirements).issubset(ACHIEVEMENT_METRICS)
        targets = {}
        completed = False
        if valid:
            try:
                targets = {
                    key: Decimal(str(value)) for key, value in requirements.items()
                }
                valid = all(
                    value > 0 and value.is_finite() for value in targets.values()
                )
            except InvalidOperation, TypeError, ValueError:
                valid = False
        user_achievement, _ = UserAchievement.objects.get_or_create(
            user_id=user_id, achievement=achievement
        )
        user_achievement = UserAchievement.objects.select_for_update().get(
            pk=user_achievement.pk
        )
        if valid:
            multiplier = (
                user_achievement.claim_count + 1 if achievement.is_repeatable else 1
            )
            scaled_targets = {
                key: target * multiplier for key, target in targets.items()
            }
            progress = min(
                Decimal("100"),
                min(
                    (
                        metrics[key] / target * Decimal("100")
                        for key, target in scaled_targets.items()
                    ),
                    default=Decimal("0"),
                ),
            ).quantize(Decimal("0.01"))
            completed = all(
                metrics[key] >= target for key, target in scaled_targets.items()
            )
        if user_achievement.reward_claimed and not achievement.is_repeatable:
            # Preserve the permanent completion record after a claimed badge.
            user_achievement.is_completed = True
            user_achievement.progress = Decimal("100")
        else:
            user_achievement.is_completed = valid and completed
            user_achievement.progress = (
                Decimal("100") if user_achievement.is_completed else progress
            )
            if user_achievement.is_completed and not user_achievement.completed_at:
                user_achievement.completed_at = timezone.now()
            elif not user_achievement.is_completed:
                user_achievement.completed_at = None
        user_achievement.save(
            update_fields=("progress", "is_completed", "completed_at", "updated_at")
        )
        user_achievement.achievement = achievement
        result.append(user_achievement)
    return result


@transaction.atomic
def claim_user_achievement(*, user_id: int, brand_id: int, achievement_id: int):
    """Grant one completed achievement's configured points and wallet bonus."""
    refresh_user_achievements(user_id=user_id, brand_id=brand_id)
    try:
        user_achievement = (
            UserAchievement.objects.select_for_update()
            .select_related("achievement", "user")
            .get(
                user_id=user_id,
                achievement_id=achievement_id,
                achievement__brand_id=brand_id,
                achievement__is_active=True,
            )
        )
    except UserAchievement.DoesNotExist as exc:
        raise AchievementClaimError("Achievement was not found") from exc
    achievement = user_achievement.achievement
    if not user_achievement.is_completed:
        raise AchievementClaimError("Achievement requirements are not complete")
    if user_achievement.reward_claimed and not achievement.is_repeatable:
        raise AchievementClaimError("Achievement reward was already claimed")
    if achievement.reward_points <= 0 and achievement.reward_amount <= 0:
        raise AchievementClaimError("This achievement has no configured reward")

    claim_number = user_achievement.claim_count + 1
    if achievement.reward_points > 0:
        try:
            program = ReferralProgram.objects.select_for_update().get(
                brand_id=brand_id, is_active=True
            )
        except ReferralProgram.DoesNotExist as exc:
            raise AchievementClaimError("Referral points are not configured") from exc
        service = program.reference_service
        if not service:
            raise AchievementClaimError("Referral points are not configured")
        try:
            validate_reward_service_configuration(service)
        except RewardConfigurationError as exc:
            raise AchievementClaimError("Referral points are not configured") from exc
        account = _locked_reward_account(user_id=user_id, brand_id=brand_id)
        if account.reference_service_id != service.pk:
            _rebase_account(
                account,
                service,
                reason_key=f"achievement-reference-change:{account.pk}:{service.pk}:{account.next_box_cycle}",
            )
        points = Decimal(achievement.reward_points).quantize(POINT_QUANTUM)
        source_key = f"achievement:{user_achievement.pk}:claim:{claim_number}"
        _place_points(account, service, points, source_key=source_key)
        account.lifetime_points += points
        account.save(update_fields=("lifetime_points", "liquid_points", "updated_at"))
        RewardPointLedger.objects.create(
            account=account,
            service=service,
            entry_type=RewardPointLedger.EntryType.ACHIEVEMENT_BONUS,
            points_delta=points,
            value_delta=points * _service_value(service),
            point_value_snapshot=_service_value(service),
            idempotency_key=f"{source_key}:ledger",
        )
    if achievement.reward_amount > 0:
        _credit_wallet_once(
            user_id=user_id,
            brand_id=brand_id,
            amount=achievement.reward_amount,
            idempotency_key=f"achievement-wallet:{user_achievement.pk}:claim:{claim_number}",
            description=f"Reward for achievement: {achievement.name}",
        )

    user_achievement.claim_count = claim_number
    user_achievement.reward_claimed_at = timezone.now()
    if achievement.is_repeatable:
        user_achievement.is_completed = False
        user_achievement.progress = Decimal("0")
        user_achievement.completed_at = None
        user_achievement.reward_claimed = False
    else:
        user_achievement.reward_claimed = True
    user_achievement.save(
        update_fields=(
            "claim_count",
            "reward_claimed",
            "reward_claimed_at",
            "is_completed",
            "progress",
            "completed_at",
            "updated_at",
        )
    )
    return user_achievement


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
        (
            (box.capacity - box.spent_points) * box.point_value_snapshot
            for box in old_boxes
            if box.state == RewardPointBox.State.COMPLETE
        ),
        Decimal("0"),
    )
    incomplete_value = sum(
        (
            box.filled * box.point_value_snapshot
            for box in old_boxes
            if box.state == RewardPointBox.State.OPEN
        ),
        Decimal("0"),
    )
    completed_points = sum(
        (
            box.capacity - box.spent_points
            for box in old_boxes
            if box.state == RewardPointBox.State.COMPLETE
        ),
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
    rebased_value = Decimal("0")
    rounding_remainder = Decimal("0")
    if incomplete_value > 0:
        new_value = _service_value(new_service)
        with localcontext() as context:
            context.prec = 40
            rebased_points = _quantize_points(incomplete_value / new_value)
            rebased_value = rebased_points * new_value
            rounding_remainder = incomplete_value - rebased_value
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
            value_delta=rebased_value,
            point_value_snapshot=_service_value(new_service),
            idempotency_key=f"{reason_key}:new-incomplete",
        )
    if rounding_remainder > 0:
        _credit_wallet_once(
            user_id=account.user_id,
            brand_id=account.brand_id,
            amount=rounding_remainder,
            idempotency_key=f"{reason_key}:fractional-remainder",
            description="Fractional point value preserved after reference service change",
        )
        RewardPointLedger.objects.create(
            account=account,
            service=old_service,
            entry_type=RewardPointLedger.EntryType.CONVERTED_TO_WALLET,
            points_delta=Decimal("0"),
            value_delta=rounding_remainder,
            point_value_snapshot=_service_value(old_service),
            idempotency_key=f"{reason_key}:fractional-remainder-ledger",
        )
    elif not RewardPointBox.objects.filter(
        account=account, service=new_service, state=RewardPointBox.State.OPEN
    ).exists():
        _seed_box_cycle(account, new_service)
    account.save(update_fields=["liquid_points", "updated_at"])


@transaction.atomic
def set_active_reference_service(*, brand_id: int, service_id: int) -> None:
    """Change a brand's reference service while preserving each account's value."""
    program, _ = ReferralProgram.objects.select_for_update().get_or_create(
        brand_id=brand_id,
        defaults={
            "is_active": True,
            "name": "Referral Rewards",
            "require_purchase": True,
            "minimum_purchase_amount": Decimal("0"),
        },
    )
    service = RewardService.objects.select_related("plan").get(
        pk=service_id, brand_id=brand_id, is_active=True
    )
    validate_reward_service_configuration(service)
    accounts = RewardAccount.objects.select_for_update().filter(brand_id=brand_id)
    for account in accounts:
        _rebase_account(
            account,
            service,
            reason_key=f"reference-service-change:{account.pk}:{service.pk}:{account.next_box_cycle}",
        )
    program.reference_service = service
    program.is_active = True
    program.save(update_fields=["reference_service", "is_active", "updated_at"])


@transaction.atomic
def redeem_reward_service(*, user_id: int, brand_id: int, request_key: uuid.UUID):
    """Spend completed points and atomically create a zero-price service order."""
    existing = (
        RewardRedemption.objects.filter(idempotency_key=request_key)
        .select_related("order", "account")
        .first()
    )
    if existing:
        if existing.account.user_id != user_id or existing.account.brand_id != brand_id:
            raise RewardRedemptionError("Reward claim does not belong to this account")
        return existing.order, True

    account = _locked_reward_account(user_id=user_id, brand_id=brand_id)
    if account.redemption_nonce != request_key:
        existing = (
            RewardRedemption.objects.filter(
                idempotency_key=request_key,
                account_id=account.pk,
            )
            .select_related("order")
            .first()
        )
        if existing:
            return existing.order, True
        raise RewardRedemptionError("This reward button has already been used")
    try:
        program = ReferralProgram.objects.select_for_update().get(
            brand_id=brand_id, is_active=True
        )
    except ReferralProgram.DoesNotExist as exc:
        raise RewardRedemptionError("Rewards are not configured") from exc
    service = program.reference_service
    if not service or not service.is_active or service.brand_id != brand_id:
        raise RewardRedemptionError("No active reward service is configured")
    if account.reference_service_id != service.pk:
        _rebase_account(
            account,
            service,
            reason_key=f"lazy-reference-service-change:{account.pk}:{service.pk}:{account.next_box_cycle}",
        )
    required_points = service.free_points
    if required_points <= 0 or account.liquid_points < required_points:
        raise RewardRedemptionError("Not enough completed points")

    plan = SubscriptionPlan.objects.select_related("vpn_provider").get(
        pk=service.plan_id, brand_id=brand_id, is_active=True
    )
    provider = (
        plan.vpn_provider
        if plan.vpn_provider_id
        else VPNProvider.objects.filter(
            brand_id=brand_id,
            status=VPNProvider.ProviderStatus.ACTIVE,
            is_default=True,
        ).first()
    )
    if (
        not provider
        or provider.brand_id != brand_id
        or provider.status != VPNProvider.ProviderStatus.ACTIVE
    ):
        raise RewardRedemptionError("The reward service has no active provider")

    now = timezone.now()
    expires_at = None
    if plan.duration_value:
        days = plan.duration_value
        if plan.duration_unit == SubscriptionPlan.DurationUnit.WEEKS:
            days *= 7
        elif plan.duration_unit == SubscriptionPlan.DurationUnit.MONTHS:
            days *= 30
        elif plan.duration_unit == SubscriptionPlan.DurationUnit.YEARS:
            days *= 365
        expires_at = now + timedelta(days=days)
    order = Order.objects.create(
        brand_id=brand_id,
        user_id=user_id,
        plan=plan,
        order_type=Order.OrderType.REWARD_REDEMPTION,
        status=Order.OrderStatus.PAID,
        original_price=0,
        final_price=0,
        currency=plan.currency,
        notes="Redeemed with completed referral points.",
    )
    Subscription.objects.create(
        brand_id=brand_id,
        user_id=user_id,
        owner_id=user_id,
        plan=plan,
        order=order,
        vpn_provider=provider,
        starts_at=now,
        expires_at=expires_at,
        traffic_limit_gb=plan.traffic_limit_gb,
        status=Subscription.SubscriptionStatus.PENDING,
    )
    redemption = RewardRedemption.objects.create(
        account=account,
        service=service,
        order=order,
        points_spent=required_points,
        idempotency_key=request_key,
    )
    account.liquid_points -= required_points
    _spend_completed_points(account=account, service=service, points=required_points)
    account.redemption_nonce = uuid.uuid4()
    account.save(update_fields=["liquid_points", "redemption_nonce", "updated_at"])
    RewardPointLedger.objects.create(
        account=account,
        service=service,
        order=order,
        entry_type=RewardPointLedger.EntryType.REDEEMED,
        points_delta=-required_points,
        value_delta=-(required_points * _service_value(service)),
        point_value_snapshot=_service_value(service),
        idempotency_key=f"reward-redemption:{redemption.pk}",
    )
    transaction.on_commit(
        lambda order_id=order.pk: _enqueue_order_provisioning(order_id)
    )
    return order, False


def _spend_completed_points(
    *, account: RewardAccount, service: RewardService, points: Decimal
) -> None:
    """Mark the exact completed-box value consumed by a redemption."""
    remaining = points
    boxes = (
        RewardPointBox.objects.select_for_update()
        .filter(
            account=account,
            service=service,
            state=RewardPointBox.State.COMPLETE,
        )
        .order_by("cycle", "sequence")
    )
    for box in boxes:
        available = box.capacity - box.spent_points
        spent = min(remaining, available)
        if spent <= 0:
            continue
        box.spent_points += spent
        box.save(update_fields=["spent_points"])
        remaining -= spent
        if remaining == 0:
            break
    if remaining > 0:
        raise RewardRedemptionError(
            "Completed reward boxes do not match the liquid balance"
        )


def _enqueue_order_provisioning(order_id: int) -> None:
    from apps.subscriptions.tasks import provision_paid_order

    try:
        provision_paid_order.delay(order_id)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error(
            "Could not queue reward order %s provisioning (%s)",
            order_id,
            type(exc).__name__,
        )


def _qualified_referral_levels(
    *, user_id: int, brand_id: int, lifetime_points: Decimal
) -> list:
    """Return tiers whose points, invite, and conversion requirements are met."""
    referral_count = Referral.objects.filter(
        referrer_id=user_id, brand_id=brand_id
    ).count()
    conversions = Referral.objects.filter(
        referrer_id=user_id,
        brand_id=brand_id,
        status=Referral.ReferralStatus.REWARDED,
    ).count()
    link = ReferralLink.objects.filter(user_id=user_id, brand_id=brand_id).first()
    clicks = link.click_count if link else 0
    conversion_rate = Decimal(conversions * 100) / Decimal(max(clicks, 1))
    program = ReferralProgram.objects.filter(
        brand_id=brand_id, is_active=True, enable_level_rewards=True
    ).first()
    if not program:
        return []
    return list(
        program.levels.filter(
            min_referrals__lte=referral_count,
            min_lifetime_points__lte=lifetime_points,
            min_conversion_rate__lte=conversion_rate,
        ).order_by("level")
    )


@transaction.atomic
def award_level_one_referral_for_payment(*, payment_id: str) -> Decimal:
    """Credit points from one profitable, confirmed level-one referral purchase."""
    payment = (
        Payment.objects.select_for_update(of=("self",))
        .select_related("order")
        .get(payment_id=payment_id)
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
            status__in=[
                Referral.ReferralStatus.PENDING,
                Referral.ReferralStatus.CONVERTED,
                Referral.ReferralStatus.REWARDED,
            ],
        )
    except ReferralProgram.DoesNotExist, Referral.DoesNotExist:
        return Decimal("0")
    now = timezone.now()
    expiry = referral.expires_at or (
        referral.created_at + timedelta(days=program.conversion_window_days)
    )
    if referral.status == Referral.ReferralStatus.PENDING and now > expiry:
        referral.status = Referral.ReferralStatus.EXPIRED
        referral.save(update_fields=["status"])
        return Decimal("0")
    if order.final_price < program.minimum_purchase_amount:
        _record_zero_referral_reward(
            referral=referral,
            order=order,
            currency=order.currency,
            reason="Order did not meet the configured minimum purchase amount.",
        )
        return Decimal("0")
    reference_service = program.reference_service
    lifetime_service = program.lifetime_reference_service or reference_service
    if not reference_service or not lifetime_service:
        raise RewardConfigurationError(
            "Referral point reference services are not configured"
        )
    if (
        reference_service.brand_id != order.brand_id
        or lifetime_service.brand_id != order.brand_id
        or reference_service.plan.currency != order.currency
        or lifetime_service.plan.currency != order.currency
    ):
        raise RewardConfigurationError("Referral point service currencies do not match")
    point_value = _service_value(reference_service)
    lifetime_value = program.lifetime_point_value or _service_value(lifetime_service)
    if lifetime_value <= 0:
        raise RewardConfigurationError("Lifetime point value must be positive")
    profit = order.final_price - order.upstream_cost_snapshot
    if profit <= 0:
        _record_zero_referral_reward(
            referral=referral,
            order=order,
            currency=order.currency,
            reason="Order did not have positive recorded profit.",
        )
        return Decimal("0")
    account = (
        RewardAccount.objects.select_for_update()
        .filter(user_id=referral.referrer_id, brand_id=order.brand_id)
        .first()
    )
    existing_lifetime = account.lifetime_points if account else Decimal("0")
    active_levels = _qualified_referral_levels(
        user_id=referral.referrer_id,
        brand_id=order.brand_id,
        lifetime_points=existing_lifetime,
    )
    multiplier = active_levels[-1].reward_multiplier if active_levels else Decimal("1")
    with localcontext() as context:
        context.prec = 32
        points = _quantize_points(profit / point_value * multiplier)
        lifetime_points = _quantize_points(profit / lifetime_value * multiplier)
    if points <= 0:
        _record_zero_referral_reward(
            referral=referral,
            order=order,
            currency=order.currency,
            reason="Profit converted to less than the smallest point unit.",
        )
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
        value_delta=points * point_value,
        point_value_snapshot=point_value,
        idempotency_key=ledger_key,
    )
    _place_points(account, reference_service, points, source_key=ledger_key)
    account.lifetime_points += lifetime_points
    account.lifetime_profit += profit
    account.save(
        update_fields=[
            "lifetime_points",
            "lifetime_profit",
            "liquid_points",
            "updated_at",
        ]
    )
    if referral.status == Referral.ReferralStatus.PENDING:
        referral.status = Referral.ReferralStatus.REWARDED
        referral.conversion_order = order
        referral.converted_at = now
    referral.referrer_reward_amount += points
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
    qualified_levels = _qualified_referral_levels(
        user_id=referral.referrer_id,
        brand_id=order.brand_id,
        lifetime_points=account.lifetime_points,
    )
    for level in qualified_levels:
        if level.bonus_reward > 0:
            _credit_wallet_once(
                user_id=referral.referrer_id,
                brand_id=order.brand_id,
                amount=level.bonus_reward,
                idempotency_key=f"referral-level-bonus:{account.pk}:{level.pk}",
                description=f"One-time referral level bonus: {level.name}",
            )
    return points
