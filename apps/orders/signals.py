from datetime import datetime
import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.orders.models import Order, Payment, Wallet, WalletTransaction
from apps.subscriptions.models import Subscription
from apps.vpn_providers.models import VPNProvider
from utils.message import broadcast_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Wallet Transactions
# ---------------------------------------------------------------------


@receiver(pre_save, sender=WalletTransaction)
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


@receiver(post_save, sender=WalletTransaction)
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


@receiver(post_delete, sender=WalletTransaction)
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

            if not Subscription.objects.filter(order=order).exists():
                Subscription.objects.create(
                    brand=instance.brand,
                    user=instance.user,
                    plan=instance.order.plan,
                    order=instance.order,
                    vpn_provider=VPNProvider.objects.filter(
                        brand=instance.brand, provider_type=VPNProvider.ProviderType.HIDDIFY
                    ).first(),
                    owner=instance.user,
                    status=Subscription.SubscriptionStatus.ACTIVE,
                    starts_at=datetime.now(),
                )
                broadcast_message(
                    brand_id=instance.brand_id,
                    user_ids=[instance.user.telegram_id],
                    text="واریزی شما برای پلن {} تایید شد.\nاکنون می‌توانید با مراجعه به بخش پلن‌های من کانفیگ‌های ساخته شده استفاده کنید.",
                    buttons_data=[
                        [{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}],
                    ],
                )
        if instance.wallet:
            wallet = instance.wallet
            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type=WalletTransaction.TransactionType.DEPOSIT,
                amount=instance.amount,
                balance_before=wallet.balance,
                balance_after = wallet.balance + instance.amount,
                description="",
            )

            broadcast_message(
                brand_id=instance.brand_id,
                user_ids=[instance.user.telegram_id],
                text="پرداختی کیف پول شما تایید شد.",
                buttons_data=[
                    [{"text": "کیف پول من", "callback_data": "wallet"}],
                ],
            )

    # ---------------------------------------------------------------
    # Payment Failed
    # ---------------------------------------------------------------
    elif instance.status == Payment.PaymentStatus.FAILED:
        if instance.order:
            instance.order.status = Order.OrderStatus.FAILED
            instance.order.save(update_fields=["status", "updated_at"])

        broadcast_message(
            brand_id=instance.brand_id,
            user_ids=[instance.user.telegram_id],
            text="Your payment failed.",
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
            wallet = instance.wallet

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type=WalletTransaction.TransactionType.REFUND,
                amount=instance.amount,
                balance_before=wallet.balance,
                balance_after=wallet.balance + instance.amount,
                reference_id=str(instance.payment_id),
                description=f"Refund for payment {instance.payment_id}",
            )

        broadcast_message(
            brand_id=instance.brand_id,
            user_ids=[instance.user.telegram_id],
            text="The payment amount has been refunded.",
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


@receiver(post_save, sender=Order)
def order_post_save(sender, instance, created, **kwargs):

    previous = getattr(instance, "_previous_status", None)

    if created:
        broadcast_message(
            brand_id=instance.brand_id,
            user_ids=[instance.user.telegram_id],
            text=f"Order {instance.order_number} has been created.",
        )
        return

    if previous == instance.status:
        return

    messages = {
        Order.OrderStatus.AWAITING_PAYMENT: "Awaiting payment.",
        Order.OrderStatus.PAID: "Payment received.",
        Order.OrderStatus.PROCESSING: "Order is processing.",
        Order.OrderStatus.COMPLETED: "Order completed.",
        Order.OrderStatus.CANCELLED: "Order cancelled.",
        Order.OrderStatus.REFUNDED: "Order refunded.",
        Order.OrderStatus.FAILED: "Order failed.",
    }

    if instance.status in messages:
        broadcast_message(
            brand_id=instance.brand_id,
            user_ids=[instance.user.telegram_id],
            text=messages[instance.status],
        )
