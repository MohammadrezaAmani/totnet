"""Transactional order and wallet operations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.orders.models import Order, Payment, Wallet, WalletTransaction


class WalletCheckoutError(Exception):
    """A wallet payment could not be completed."""


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
            Order.objects.select_for_update()
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

    if not wallet.is_active or wallet.is_frozen:
        raise WalletCheckoutError("Wallet is unavailable")
    if wallet.currency != order.currency:
        raise WalletCheckoutError("Wallet currency does not match order currency")
    if wallet.balance < order.final_price:
        raise WalletCheckoutError("Insufficient wallet balance")

    idempotency_key = f"order-wallet-payment:{order.order_id}"
    existing_transaction = WalletTransaction.objects.filter(
        idempotency_key=idempotency_key
    ).first()
    if existing_transaction:
        raise WalletCheckoutError("Wallet transaction exists without confirmed payment")

    balance_before = wallet.balance
    wallet.balance -= order.final_price
    wallet.save(update_fields=["balance", "updated_at"])
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
    WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type=WalletTransaction.TransactionType.PAYMENT,
        amount=-order.final_price,
        balance_before=balance_before,
        balance_after=wallet.balance,
        reference_id=str(order.order_id),
        idempotency_key=idempotency_key,
        description=f"Wallet payment for order {order.order_number}",
        metadata={"payment_id": str(payment.payment_id)},
    )
    return WalletCheckoutResult(str(payment.payment_id), already_paid=False)
