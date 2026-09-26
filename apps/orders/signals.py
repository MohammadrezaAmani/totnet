import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.orders.models import Order, Payment, Wallet, WalletTransaction
from apps.subscriptions.models import Subscription
from apps.vpn_providers.models import VPNProvider
from utils.message import broadcast_message

logger = logging.getLogger(__name__)


def _apply_payment_wallet_delta(instance, *, delta, transaction_type, key, description):
    """Apply one payment-driven wallet change once, protected by a row lock."""
    if not instance.wallet_id:
        return
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=instance.wallet_id)
        if WalletTransaction.objects.filter(idempotency_key=key).exists():
            return
        balance_before = wallet.balance
        wallet.balance += delta
        wallet.save(update_fields=["balance", "updated_at"])
        WalletTransaction.objects.create(
            wallet=wallet,
            transaction_type=transaction_type,
            amount=delta,
            balance_before=balance_before,
            balance_after=wallet.balance,
            reference_id=str(instance.payment_id),
            idempotency_key=key,
            description=description,
        )


# ---------------------------------------------------------------------
# Wallet Transactions
# ---------------------------------------------------------------------


def wallet_transaction_pre_save(sender, instance, **kwargs):
    """
    Fill balance_before / balance_after automatically if not provided.
    """

    if instance.pk:
        return

    wallet = instance.wallet

    if instance.balance_before is None:
        instance.balance_before = wallet.balance

    if instance.transaction_type in (
        WalletTransaction.TransactionType.DEPOSIT,
        WalletTransaction.TransactionType.REFUND,
        WalletTransaction.TransactionType.BONUS,
        WalletTransaction.TransactionType.REFERRAL_REWARD,
        WalletTransaction.TransactionType.ADMIN_ADJUSTMENT,
    ):
        instance.balance_after = wallet.balance + instance.amount

    elif instance.transaction_type in (
        WalletTransaction.TransactionType.WITHDRAWAL,
        WalletTransaction.TransactionType.PAYMENT,
    ):
        instance.balance_after = wallet.balance - instance.amount


def wallet_transaction_created(sender, instance, created, **kwargs):
    """
    Synchronize wallet balance with transaction.
    """

    if not created:
        return

    wallet = instance.wallet

    with transaction.atomic():
        wallet.balance = instance.balance_after
        wallet.save(update_fields=["balance", "updated_at"])

    logger.info(
        "Wallet %s updated. Balance=%s",
        wallet.pk,
        wallet.balance,
    )


def wallet_transaction_deleted(sender, instance, **kwargs):
    """
    Rollback wallet balance if transaction is deleted.
    """

    wallet = instance.wallet

    wallet.balance = instance.balance_before
    wallet.save(update_fields=["balance", "updated_at"])

    logger.warning(
        "Wallet transaction %s deleted. Wallet restored.",
        instance.pk,
    )


# ---------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------


@receiver(pre_save, sender=Payment)
def payment_pre_save(sender, instance, **kwargs):
    """
    Store previous status.
    """

    if not instance.pk:
        instance._previous_status = None
        return

    try:
        old = Payment.objects.get(pk=instance.pk)
        instance._previous_status = old.status
    except Payment.DoesNotExist:
        instance._previous_status = None


@receiver(post_save, sender=Payment)
def payment_post_save(sender, instance, created, **kwargs):
    """
    React to payment state changes.
    """

    previous = getattr(instance, "_previous_status", None)

    if not created and previous == instance.status:
        return

    # ---------------------------------------------------------------
    # Payment Confirmed
    # ---------------------------------------------------------------
    if instance.status == Payment.PaymentStatus.CONFIRMED:
        if instance.order:
            order = instance.order

            if order.status != Order.OrderStatus.PAID:
                order.status = Order.OrderStatus.PAID
                order.save(update_fields=["status", "updated_at"])

            provider = None
            if order.plan.vpn_provider_id:
                provider = VPNProvider.objects.filter(
                    pk=order.plan.vpn_provider_id,
                    brand=order.brand,
                    status=VPNProvider.ProviderStatus.ACTIVE,
                ).first()
            else:
                provider = VPNProvider.objects.filter(
                    brand=order.brand,
                    status=VPNProvider.ProviderStatus.ACTIVE,
                    is_default=True,
                ).first()
            if provider:
                Subscription.objects.get_or_create(
                    order=order,
                    defaults={
                        "brand": order.brand,
                        "user": order.user,
                        "plan": order.plan,
                        "vpn_provider": provider,
                        "owner": order.recipient or order.user,
                        "status": Subscription.SubscriptionStatus.PENDING,
                        "starts_at": timezone.now(),
                        "traffic_limit_gb": order.plan.traffic_limit_gb,
                    },
                )
            else:
                logger.error(
                    "Payment %s confirmed for order %s, but brand %s has no active provider",
                    instance.pk,
                    order.pk,
                    order.brand_id,
                )
            if provider:
                transaction.on_commit(
                    lambda brand_id=instance.brand_id, telegram_id=instance.user.telegram_id: (
                        broadcast_message(
                            brand_id=brand_id,
                            user_ids=[telegram_id],
                            text="پرداخت شما تأیید شد و سفارش برای فعال‌سازی اشتراک ثبت شد.",
                            buttons_data=[
                                [
                                    {
                                        "text": "📱 اشتراک‌های من",
                                        "callback_data": "my_subscriptions",
                                    }
                                ],
                            ],
                        )
                    )
                )
        if instance.wallet_id and not instance.order_id:
            _apply_payment_wallet_delta(
                instance,
                delta=instance.amount,
                transaction_type=WalletTransaction.TransactionType.DEPOSIT,
                key=f"payment:{instance.payment_id}:confirmed-credit",
                description="Wallet top-up confirmed",
            )
            transaction.on_commit(
                lambda brand_id=instance.brand_id, telegram_id=instance.user.telegram_id: (
                    broadcast_message(
                        brand_id=brand_id,
                        user_ids=[telegram_id],
                        text="پرداختی کیف پول شما تایید شد.",
                        buttons_data=[
                            [{"text": "کیف پول من", "callback_data": "wallet"}]
                        ],
                    )
                )
            )

    # ---------------------------------------------------------------
    # Payment Failed
    # ---------------------------------------------------------------
    elif instance.status == Payment.PaymentStatus.FAILED:
        if instance.order:
            instance.order.status = Order.OrderStatus.FAILED
            instance.order.save(update_fields=["status", "updated_at"])

        transaction.on_commit(
            lambda brand_id=instance.brand_id, telegram_id=instance.user.telegram_id: (
                broadcast_message(
                    brand_id=brand_id,
                    user_ids=[telegram_id],
                    text="Your payment failed.",
                )
            )
        )

    # ---------------------------------------------------------------
    # Payment Cancelled
    # ---------------------------------------------------------------
    elif instance.status == Payment.PaymentStatus.CANCELLED:
        if instance.order:
            instance.order.status = Order.OrderStatus.CANCELLED
            instance.order.save(update_fields=["status", "updated_at"])

    # ---------------------------------------------------------------
    # Payment Refunded
    # ---------------------------------------------------------------
    elif instance.status == Payment.PaymentStatus.REFUNDED:
        if instance.order:
            instance.order.status = Order.OrderStatus.REFUNDED
            instance.order.save(update_fields=["status", "updated_at"])

        if instance.wallet:
            _apply_payment_wallet_delta(
                instance,
                delta=(instance.amount if instance.order_id else -instance.amount),
                transaction_type=WalletTransaction.TransactionType.REFUND,
                key=f"payment:{instance.payment_id}:refunded-wallet-delta",
                description=f"Refund/reversal for payment {instance.payment_id}",
            )


# ---------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------


@receiver(pre_save, sender=Order)
def order_pre_save(sender, instance, **kwargs):

    if not instance.pk:
        instance._previous_status = None
        return

    try:
        old = Order.objects.get(pk=instance.pk)
        instance._previous_status = old.status
    except Order.DoesNotExist:
        instance._previous_status = None


# @receiver(post_save, sender=Order)
# def order_post_save(sender, instance, created, **kwargs):

#     previous = getattr(instance, "_previous_status", None)

#     if created:
#         return

#     if previous == instance.status:
#         return

#     messages = {
#         Order.OrderStatus.AWAITING_PAYMENT: "Awaiting payment.",
#         Order.OrderStatus.PAID: "Payment received.",
#         Order.OrderStatus.PROCESSING: "Order is processing.",
#         Order.OrderStatus.COMPLETED: "Order completed.",
#         Order.OrderStatus.CANCELLED: "Order cancelled.",
#         Order.OrderStatus.REFUNDED: "Order refunded.",
#         Order.OrderStatus.FAILED: "Order failed.",
#     }

#     if instance.status in messages:
#         broadcast_message(
#             brand_id=instance.brand_id,
#             user_ids=[instance.user.telegram_id],
#             text=messages[instance.status],
#         )
