"""Transactional order and wallet operations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.orders.models import (
    Coupon,
    CouponUsage,
    Order,
    Payment,
    Wallet,
    WalletTransaction,
)


class WalletCheckoutError(Exception):
    """A wallet payment could not be completed."""


class WalletOperationError(Exception):
    """A wallet ledger operation could not be completed."""


class WalletCouponError(Exception):
    """A coupon cannot be redeemed as wallet credit."""


@dataclass(frozen=True)
class WalletCheckoutResult:
    payment_id: str
    already_paid: bool


@transaction.atomic
def pay_order_with_wallet(
    *, order_id: str, user_id: int, brand_id: int
) -> WalletCheckoutResult:
    """Charge a wallet and confirm an order exactly once under row locks."""
    try:
        order = (
            Order.objects.select_for_update(of=("self",))
            .select_related("brand", "user", "plan", "recipient")
            .get(order_id=order_id, user_id=user_id, brand_id=brand_id)
        )
    except Order.DoesNotExist as exc:
        raise WalletCheckoutError("Order was not found") from exc

    confirmed = Payment.objects.filter(
        order=order, status=Payment.PaymentStatus.CONFIRMED
    ).first()
    if confirmed:
        return WalletCheckoutResult(str(confirmed.payment_id), already_paid=True)
    if order.status not in (
        Order.OrderStatus.PENDING,
        Order.OrderStatus.AWAITING_PAYMENT,
    ):
        raise WalletCheckoutError("Order cannot be paid in its current state")
    if order.final_price <= Decimal("0"):
        raise WalletCheckoutError("Order amount must be positive")

    try:
        wallet = Wallet.objects.select_for_update().get(
            user_id=user_id, brand_id=brand_id
        )
    except Wallet.DoesNotExist as exc:
        raise WalletCheckoutError("Wallet was not found") from exc

    if wallet.currency != order.currency:
        raise WalletCheckoutError("Wallet currency does not match order currency")

    idempotency_key = f"order-wallet-payment:{order.order_id}"
    existing_transaction = WalletTransaction.objects.filter(
        idempotency_key=idempotency_key
    ).first()
    if existing_transaction:
        raise WalletCheckoutError("Wallet transaction exists without confirmed payment")

    try:
        debit_wallet(
            wallet_id=wallet.pk,
            amount=order.final_price,
            transaction_type=WalletTransaction.TransactionType.PAYMENT,
            description=f"Wallet payment for order {order.order_number}",
            reference_id=str(order.order_id),
            idempotency_key=idempotency_key,
        )
    except WalletOperationError as exc:
        raise WalletCheckoutError(str(exc)) from exc
    payment = Payment.objects.create(
        order=order,
        wallet=wallet,
        brand_id=brand_id,
        user_id=user_id,
        payment_method=Payment.PaymentMethod.WALLET,
        status=Payment.PaymentStatus.CONFIRMED,
        amount=order.final_price,
        currency=order.currency,
        verified_at=timezone.now(),
    )
    return WalletCheckoutResult(str(payment.payment_id), already_paid=False)


@transaction.atomic
def credit_wallet(
    *,
    wallet_id: int,
    amount: Decimal,
    transaction_type: str,
    description: str,
    reference_id: str | None = None,
    metadata: dict | None = None,
    idempotency_key: str | None = None,
) -> WalletTransaction:
    """Apply one positive wallet credit with a locked, auditable ledger row."""
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WalletOperationError("Invalid wallet amount") from exc
    allowed_types = {
        WalletTransaction.TransactionType.DEPOSIT,
        WalletTransaction.TransactionType.REFUND,
        WalletTransaction.TransactionType.BONUS,
        WalletTransaction.TransactionType.REFERRAL_REWARD,
        WalletTransaction.TransactionType.ADMIN_ADJUSTMENT,
    }
    if not amount.is_finite() or amount <= 0 or transaction_type not in allowed_types:
        raise WalletOperationError("Wallet credit must use a positive amount and credit type")

    wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
    if idempotency_key:
        existing = WalletTransaction.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            if existing.wallet_id != wallet.pk or existing.amount != amount:
                raise WalletOperationError("Idempotency key was already used")
            return existing
    if not wallet.is_active or wallet.is_frozen:
        raise WalletOperationError("Wallet is unavailable")

    return WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type=transaction_type,
        amount=amount,
        balance_before=wallet.balance,
        balance_after=wallet.balance + amount,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
        description=description,
        metadata=metadata or {},
    )


@transaction.atomic
def debit_wallet(
    *,
    wallet_id: int,
    amount: Decimal,
    transaction_type: str,
    description: str,
    reference_id: str | None = None,
    metadata: dict | None = None,
    idempotency_key: str | None = None,
) -> WalletTransaction:
    """Apply one positive wallet debit with balance, freeze, and spend-limit checks."""
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WalletOperationError("Invalid wallet amount") from exc
    allowed_types = {
        WalletTransaction.TransactionType.WITHDRAWAL,
        WalletTransaction.TransactionType.PAYMENT,
    }
    if not amount.is_finite() or amount <= 0 or transaction_type not in allowed_types:
        raise WalletOperationError("Wallet debit must use a positive amount and debit type")

    wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
    if idempotency_key:
        existing = WalletTransaction.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            if existing.wallet_id != wallet.pk or existing.amount != amount:
                raise WalletOperationError("Idempotency key was already used")
            return existing
    if not wallet.is_active or wallet.is_frozen:
        raise WalletOperationError("Wallet is unavailable")
    if wallet.balance < amount:
        raise WalletOperationError("Insufficient wallet balance")

    now = timezone.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    daily_spent = WalletTransaction.objects.filter(
        wallet=wallet,
        transaction_type__in=allowed_types,
        created_at__gte=day_start,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    monthly_spent = WalletTransaction.objects.filter(
        wallet=wallet,
        transaction_type__in=allowed_types,
        created_at__gte=month_start,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if wallet.daily_spending_limit is not None and daily_spent + amount > wallet.daily_spending_limit:
        raise WalletOperationError("Daily wallet spending limit exceeded")
    if wallet.monthly_spending_limit is not None and monthly_spent + amount > wallet.monthly_spending_limit:
        raise WalletOperationError("Monthly wallet spending limit exceeded")

    return WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type=transaction_type,
        amount=amount,
        balance_before=wallet.balance,
        balance_after=wallet.balance - amount,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
        description=description,
        metadata=metadata or {},
    )


@transaction.atomic
def redeem_wallet_coupon(*, user_id: int, brand_id: int, code: str):
    """Redeem a fixed-value coupon as wallet credit without raceable usage limits."""
    try:
        coupon = Coupon.objects.select_for_update().get(
            brand_id=brand_id, code__iexact=code, is_active=True
        )
    except Coupon.DoesNotExist as exc:
        raise WalletCouponError("Coupon was not found or is inactive") from exc

    now = timezone.now()
    if now < coupon.valid_from or now > coupon.valid_until:
        raise WalletCouponError("Coupon is outside its valid period")
    usage_count = CouponUsage.objects.filter(coupon=coupon).count()
    if coupon.max_uses is not None and usage_count >= coupon.max_uses:
        raise WalletCouponError("Coupon usage limit has been reached")
    if CouponUsage.objects.filter(coupon=coupon, user_id=user_id).count() >= coupon.max_uses_per_user:
        raise WalletCouponError("Coupon has already reached this user's limit")
    if coupon.new_users_only and Order.objects.filter(
        user_id=user_id, brand_id=brand_id
    ).exists():
        raise WalletCouponError("Coupon is only available to new users")
    if (
        coupon.coupon_type != Coupon.CouponType.FIXED_AMOUNT
        or coupon.applicable_plans.exists()
        or (coupon.minimum_order_amount or Decimal("0")) > 0
    ):
        raise WalletCouponError("This coupon must be applied to a subscription order")

    amount = coupon.discount_value
    if coupon.max_discount_amount is not None:
        amount = min(amount, coupon.max_discount_amount)
    if amount <= 0:
        raise WalletCouponError("Coupon has no wallet value")

    try:
        wallet = Wallet.objects.select_for_update().get(
            user_id=user_id, brand_id=brand_id
        )
    except Wallet.DoesNotExist as exc:
        raise WalletCouponError("Wallet was not found") from exc
    brand_currency = wallet.brand.currency
    if wallet.currency != brand_currency:
        raise WalletCouponError("Wallet currency does not match the brand")
    if not wallet.is_active or wallet.is_frozen:
        raise WalletCouponError("Wallet is unavailable")

    usage = CouponUsage.objects.create(
        coupon=coupon, user_id=user_id, order=None, discount_amount=amount
    )
    coupon.current_uses = usage_count + 1
    coupon.save(update_fields=["current_uses", "updated_at"])
    transaction_row = credit_wallet(
        wallet_id=wallet.pk,
        amount=amount,
        transaction_type=WalletTransaction.TransactionType.BONUS,
        description=f"Wallet bonus from coupon {coupon.code}",
        reference_id=f"coupon-usage:{usage.pk}",
        metadata={"coupon_code": coupon.code, "coupon_id": coupon.pk},
        idempotency_key=f"coupon-wallet-credit:{usage.pk}",
    )
    return coupon, usage, transaction_row


@transaction.atomic
def validate_stars_pre_checkout(
    *, payment_id: str, telegram_user_id: int, paid_stars: int, currency: str
) -> bool:
    """Accept only a still-valid Stars invoice matching its stored quote."""
    try:
        payment = Payment.objects.select_for_update(of=("self",)).select_related("user").get(
            payment_id=payment_id
        )
    except Payment.DoesNotExist:
        return False
    if (
        payment.payment_method != Payment.PaymentMethod.TELEGRAM_STARS
        or payment.status != Payment.PaymentStatus.PENDING
        or not payment.wallet_id
        or payment.user.telegram_id != telegram_user_id
        or payment.currency != "USD"
        or currency != "XTR"
        or payment.stars_amount != paid_stars
        or not payment.expires_at
        or payment.expires_at <= timezone.now()
    ):
        return False
    payment.status = Payment.PaymentStatus.AWAITING_CONFIRMATION
    payment.save(update_fields=["status", "updated_at"])
    return True


@transaction.atomic
def confirm_stars_payment(
    *,
    payment_id: str,
    user_id: int,
    charge_id: str,
    paid_stars: int,
) -> Payment:
    """Confirm one Telegram Stars invoice; its payment signal credits once."""
    try:
        payment = Payment.objects.select_for_update().get(
            payment_id=payment_id, user_id=user_id
        )
    except Payment.DoesNotExist as exc:
        raise WalletOperationError("Payment was not found") from exc
    if payment.status == Payment.PaymentStatus.CONFIRMED:
        if payment.telegram_payment_charge_id == charge_id:
            return payment
        raise WalletOperationError("Payment was already confirmed")
    if (
        payment.payment_method != Payment.PaymentMethod.TELEGRAM_STARS
        or payment.status != Payment.PaymentStatus.AWAITING_CONFIRMATION
        or not payment.wallet_id
        or payment.stars_amount != paid_stars
        or not payment.expires_at
        or payment.expires_at <= timezone.now()
        or not charge_id
    ):
        raise WalletOperationError("Payment does not match the Stars invoice")
    payment.status = Payment.PaymentStatus.CONFIRMED
    payment.telegram_payment_charge_id = charge_id
    payment.verified_at = timezone.now()
    payment.save(
        update_fields=(
            "status",
            "telegram_payment_charge_id",
            "verified_at",
            "updated_at",
        )
    )
    return payment
