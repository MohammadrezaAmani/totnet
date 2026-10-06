"""Durable Celery entry points for subscription provisioning."""

import logging
from html import escape
from datetime import timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.orders.models import Order
from apps.subscriptions.models import ProviderRemoteSubscription, Subscription
from apps.subscriptions.services import provision_order_subscription

logger = logging.getLogger(__name__)


@shared_task
def provision_paid_order(order_pk: int):
    """Provision the single local subscription associated with a paid order."""
    try:
        order = Order.objects.select_related(
            "brand", "user", "recipient", "plan", "plan__vpn_provider"
        ).get(pk=order_pk)
    except Order.DoesNotExist:
        logger.warning("Provisioning skipped: order %s does not exist", order_pk)
        return False
    if order.status not in (Order.OrderStatus.PAID, Order.OrderStatus.PROCESSING):
        logger.info("Provisioning skipped for order %s in state %s", order.pk, order.status)
        return False

    now = timezone.now()
    with transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update()
            .select_related("vpn_provider")
            .filter(order=order)
            .first()
        )
        if subscription is None:
            logger.warning("Provisioning skipped: order %s has no local subscription", order.pk)
            return False
        if subscription.status == Subscription.SubscriptionStatus.ACTIVE:
            return True
        remote_identity_exists = ProviderRemoteSubscription.objects.filter(
            subscription=subscription, remote_id__gt=""
        ).exists() or bool(
            (subscription.connection_configs or {}).get("hiddify_uuid")
            or (subscription.connection_configs or {}).get("secret_uuid")
        )
        if subscription.provisioning_attempts and not remote_identity_exists:
            subscription.provisioning_state = "needs_review"
            subscription.provisioning_error_code = "ambiguous_provisioning_outcome"
            subscription.provisioning_error = (
                "A previous provider request may have succeeded, but no remote ID was saved. "
                "Automatic retry is paused to prevent duplicate accounts."
            )
            subscription.provisioning_retryable = False
            subscription.save(
                update_fields=(
                    "provisioning_state",
                    "provisioning_error_code",
                    "provisioning_error",
                    "provisioning_retryable",
                    "updated_at",
                )
            )
            logger.warning(
                "Provisioning retry held for order %s: prior outcome has no saved remote identity",
                order.pk,
            )
            return False
        if (
            subscription.provisioning_started_at
            and subscription.provisioning_started_at > now - timedelta(minutes=5)
        ):
            logger.info("Provisioning already in progress for order %s", order.pk)
            return False
        subscription.provisioning_started_at = now
        subscription.provisioning_attempts += 1
        subscription.provisioning_state = "in_progress"
        subscription.provisioning_error_code = ""
        subscription.provisioning_error = ""
        subscription.provisioning_retryable = False
        subscription.save(
            update_fields=(
                "provisioning_started_at",
                "provisioning_attempts",
                "provisioning_state",
                "provisioning_error_code",
                "provisioning_error",
                "provisioning_retryable",
                "updated_at",
            )
        )

    try:
        subscription = async_to_sync(provision_order_subscription)(order.pk)
    except Exception as exc:
        subscription.refresh_from_db()
        has_remote_identity = ProviderRemoteSubscription.objects.filter(
            subscription=subscription, remote_id__gt=""
        ).exists() or bool(
            (subscription.connection_configs or {}).get("hiddify_uuid")
            or (subscription.connection_configs or {}).get("secret_uuid")
        )
        subscription.provisioning_state = (
            "retryable_error" if has_remote_identity else "needs_review"
        )
        subscription.provisioning_error_code = type(exc).__name__[:80]
        subscription.provisioning_error = (
            "Provider operation failed. Reconciliation may be retried safely."
            if has_remote_identity
            else "Provider outcome is ambiguous; automatic retry is paused to prevent duplicates."
        )
        subscription.provisioning_retryable = has_remote_identity
        subscription.save(
            update_fields=(
                "provisioning_state",
                "provisioning_error_code",
                "provisioning_error",
                "provisioning_retryable",
                "updated_at",
            )
        )
        logger.error(
            "Subscription provisioning task failed for order %s (%s)",
            order.pk,
            type(exc).__name__,
        )
        return False
    if subscription and subscription.status == Subscription.SubscriptionStatus.ACTIVE:
        Subscription.objects.filter(pk=subscription.pk).update(
            provisioning_state="active",
            provisioning_error_code="",
            provisioning_error="",
            provisioning_retryable=False,
        )
        telegram_ids = {
            telegram_id
            for telegram_id in (
                subscription.owner.telegram_id,
                order.user.telegram_id,
            )
            if telegram_id
        }
        try:
            from utils.message import broadcast_message

            activation_lines = [
                f"✅ <b>اشتراک «{escape(subscription.plan.name)}» فعال شد.</b>"
            ]
            if subscription.connectix_username:
                activation_lines.append(
                    f"👤 نام کاربری: <code>{escape(subscription.connectix_username)}</code>"
                )
            if subscription.subscription_url:
                activation_lines.extend(
                    [
                        "",
                        "🔗 لینک اشتراک:",
                        f"<code>{escape(subscription.subscription_url)}</code>",
                    ]
                )
            activation_lines.append(
                "\nبرای دریافت اطلاعات اتصال و QR از دکمه زیر استفاده کنید."
            )
            broadcast_message(
                brand_id=order.brand_id,
                user_ids=list(telegram_ids),
                text="\n".join(activation_lines),
                buttons_data=[
                    [
                        {
                            "text": "📥 دریافت اکانت / لینک / QR",
                            "callback_data": f"get_config_{subscription.pk}",
                        }
                    ],
                    [{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}],
                ],
            )
        except Exception as exc:
            logger.warning(
                "Provisioning notification failed for order %s (%s)",
                order.pk,
                type(exc).__name__,
            )
        return True
    if subscription:
        subscription.refresh_from_db()
        if subscription.provisioning_state == "unsupported":
            return False
        if subscription.provisioning_state == "retryable_error":
            return False
        if subscription.provisioning_state == "needs_review":
            return False
        has_remote_identity = ProviderRemoteSubscription.objects.filter(
            subscription=subscription, remote_id__gt=""
        ).exists() or bool(
            (subscription.connection_configs or {}).get("hiddify_uuid")
            or (subscription.connection_configs or {}).get("secret_uuid")
        )
        if has_remote_identity:
            subscription.provisioning_state = "retryable_error"
            subscription.provisioning_error_code = "provider_reconciliation_incomplete"
            subscription.provisioning_error = (
                "The provider account exists, but its active subscription link could not be verified."
            )
            subscription.provisioning_retryable = True
        else:
            subscription.provisioning_state = "needs_review"
            subscription.provisioning_error_code = "provider_outcome_ambiguous"
            subscription.provisioning_error = (
                "No remote ID was saved after a provisioning attempt. Automatic retry is paused."
            )
            subscription.provisioning_retryable = False
        subscription.save(
            update_fields=(
                "provisioning_state",
                "provisioning_error_code",
                "provisioning_error",
                "provisioning_retryable",
                "updated_at",
            )
        )
    return False
