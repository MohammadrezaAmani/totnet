"""Atomic decisions for manually reviewed wallet and challenge receipts."""

from django.db import transaction
from django.utils import timezone

from apps.orders.models import Payment
from apps.referrals.models import UserChallenge


class ReceiptReviewError(ValueError):
    pass


@transaction.atomic
def review_direct_deposit(*, payment_id: int, brand_id: int, reviewer_id: int,
                          approve: bool) -> Payment:
    payment = Payment.objects.select_for_update().filter(
        pk=payment_id, brand_id=brand_id,
    ).first()
    if not payment or payment.status not in {
        Payment.PaymentStatus.PENDING, Payment.PaymentStatus.AWAITING_CONFIRMATION,
    }:
        raise ReceiptReviewError("این پرداخت قابل بررسی نیست.")
    if payment.order_id:
        raise ReceiptReviewError("این رسید مربوط به سفارش است.")
    is_challenge = UserChallenge.objects.filter(
        entry_payment_id=payment_id, user_id=payment.user_id,
        brand_id=payment.brand_id, status=UserChallenge.Status.AWAITING_FUNDS,
    ).exists()
    if not is_challenge and not payment.wallet_id:
        raise ReceiptReviewError("نوع پرداخت معتبر نیست.")
    if payment.wallet_id and is_challenge:
        raise ReceiptReviewError("مسیر پرداخت مبهم است.")
    payment.status = (
        Payment.PaymentStatus.CONFIRMED if approve else Payment.PaymentStatus.FAILED
    )
    payment.verified_by_id = reviewer_id
    payment.verified_at = timezone.now()
    # Signals activate the challenge within this same transaction. Any error
    # aborts confirmation and retains the pending receipt.
    payment.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    return payment
