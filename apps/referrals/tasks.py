"""Retryable background processing for purchase referral rewards."""

import logging

from celery import shared_task
from django.db.models import Exists, OuterRef

from apps.orders.models import Order, Payment

from .models import Referral, ReferralProgram, ReferralReward
from .services import RewardConfigurationError, award_level_one_referral_for_payment

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def process_referral_reward(self, payment_id: str):
    """Process one confirmed purchase; transient configuration issues are retried."""
    try:
        return str(award_level_one_referral_for_payment(payment_id=payment_id))
    except RewardConfigurationError as exc:
        logger.warning("Referral reward deferred for payment %s: %s", payment_id, exc)
        raise self.retry(exc=exc)


@shared_task
def recover_pending_referral_rewards(limit: int = 100):
    """Recover confirmed referred purchases if enqueueing or processing failed."""
    rewarded_orders = ReferralReward.objects.filter(order_id=OuterRef("order_id"))
    eligible_referrals = Referral.objects.filter(
        referee_id=OuterRef("order__user_id"),
        brand_id=OuterRef("order__brand_id"),
        status__in=[
            Referral.ReferralStatus.PENDING,
            Referral.ReferralStatus.CONVERTED,
            Referral.ReferralStatus.REWARDED,
        ],
    )
    active_programs = ReferralProgram.objects.filter(
        brand_id=OuterRef("order__brand_id"),
        is_active=True,
        reference_service__isnull=False,
    )
    payments = (
        Payment.objects.filter(
            status=Payment.PaymentStatus.CONFIRMED,
            order__isnull=False,
            order__status=Order.OrderStatus.PAID,
        )
        .annotate(has_reward=Exists(rewarded_orders))
        .annotate(has_referral=Exists(eligible_referrals))
        .annotate(has_program=Exists(active_programs))
        .filter(has_reward=False, has_referral=True, has_program=True)
        .order_by("created_at")
        .values_list("payment_id", flat=True)[:limit]
    )
    queued = 0
    for payment_id in payments:
        process_referral_reward.delay(str(payment_id))
        queued += 1
    return queued
