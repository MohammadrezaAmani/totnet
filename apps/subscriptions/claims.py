"""Attach unclaimed gifts only to the recipient identity designated by the buyer."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Order
from .models import Subscription, SubscriptionClaim


@transaction.atomic
def claim_designated_gift(*, user_id, brand_id, username, telegram_username):
    user = User.objects.select_for_update().get(pk=user_id, brand_id=brand_id)
    matches = list(
        Subscription.objects.filter(
            brand_id=brand_id,
            order__order_type=Order.OrderType.GIFT,
            order__recipient_claimed_at__isnull=True,
        )
        .filter(
            Q(connectix_username__iexact=username) | Q(vpn_user_email__iexact=username)
        )
        .values_list("pk", flat=True)[:2]
    )
    if len(matches) != 1:
        return None
    subscription_id = matches[0]
    order_id = Subscription.objects.values_list("order_id", flat=True).get(
        pk=subscription_id
    )
    # Match the reward processor's order -> subscription lock order.
    order = Order.objects.select_for_update().get(pk=order_id)
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .get(pk=subscription_id)
    )
    if (
        order.recipient_claimed_at
        or order.user_id == user.pk
        or order.status
        not in {
            Order.OrderStatus.PAID,
            Order.OrderStatus.PROCESSING,
            Order.OrderStatus.COMPLETED,
        }
    ):
        return None
    designated = order.recipient_telegram_username.lstrip("@").lower()
    actual = (telegram_username or "").lower()
    if order.recipient_id:
        if order.recipient_id != user.pk:
            return None
    elif not designated or designated != actual:
        return None
    now = timezone.now()
    order.recipient = user
    order.recipient_claimed_at = now
    order.save(update_fields=["recipient", "recipient_claimed_at", "updated_at"])
    subscription.owner = user
    subscription.is_gift = True
    subscription.save(update_fields=["owner", "is_gift", "updated_at"])
    claim, _ = SubscriptionClaim.objects.get_or_create(
        user=user, brand_id=brand_id, username=username
    )
    claim.status = SubscriptionClaim.Status.APPROVED
    claim.matched_subscription = subscription
    claim.reviewed_at = now
    claim.admin_note = (
        "Automatically matched to the buyer-designated Telegram recipient."
    )
    claim.save(
        update_fields=[
            "status",
            "matched_subscription",
            "reviewed_at",
            "admin_note",
            "updated_at",
        ]
    )
    from apps.referrals.services import _enqueue_claimed_gift_reward

    payment = (
        order.payments.filter(status="confirmed").order_by("-created_at", "-pk").first()
    )
    if payment:
        transaction.on_commit(
            lambda: _enqueue_claimed_gift_reward(str(payment.payment_id))
        )
    return subscription
