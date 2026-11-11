"""Durable administrator-triggered challenge offers for one brand."""
import logging
from datetime import timedelta
from html import escape

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ChallengeBroadcast, ChallengeProgram, GamificationNotification,
    UserChallenge,
)
from .rate_policy import validate_challenge_configuration
from .scheduling import audience_users

logger = logging.getLogger(__name__)


def eligible_challenge_users(program):
    """Include previously offered users for safe retry; never reopen used offers."""
    previous_participants = UserChallenge.objects.filter(program=program).exclude(
        status=UserChallenge.Status.OFFERED
    ).values("user_id")
    return audience_users(program.brand_id).exclude(pk__in=previous_participants).order_by("pk")


def queue_challenge_offer(*, program, user, trigger, anchor_at=None):
    from .tasks import _queue_once

    challenge, _ = UserChallenge.objects.get_or_create(
        user=user, brand_id=program.brand_id, program=program,
        defaults={"status": UserChallenge.Status.OFFERED, "offered_at": timezone.now()},
    )
    if challenge.status != UserChallenge.Status.OFFERED:
        return False
    return _queue_once(
        user=user, brand_id=program.brand_id,
        notification_type=GamificationNotification.NotificationType.CHALLENGE_OFFER,
        dedupe_key=f"challenge-offer:{challenge.pk}",
        text=(f"🏆 <b>چالش ویژه {escape(program.name)} برای شما فعال شد</b>\n\n"
              "امکان شرکت در این چالش فقط یک‌بار وجود دارد.\n"
              "مایل به دریافت شرایط شرکت در چالش هستید؟"),
        buttons_data=[[
            {"text": "✅ بله", "callback_data": f"challenge_accept_{challenge.pk}"},
            {"text": "❌ خیر", "callback_data": f"challenge_decline_{challenge.pk}"},
        ]],
        metadata={
            "challenge_id": challenge.pk, "trigger": trigger,
            **({"anchor_at": anchor_at.isoformat()} if anchor_at else {}),
        },
    )


def publish_challenge_broadcast(broadcast_id):
    from .tasks import process_challenge_broadcast
    try:
        process_challenge_broadcast.delay(broadcast_id)
    except Exception:
        logger.exception("Cannot dispatch challenge broadcast %s", broadcast_id)


@transaction.atomic
def start_challenge_broadcast(*, brand_id, admin_id):
    program = ChallengeProgram.objects.select_for_update().get(brand_id=brand_id)
    if not program.is_active:
        raise ValidationError("ابتدا چالش را فعال کنید.")
    validate_challenge_configuration(program)
    running = ChallengeBroadcast.objects.filter(
        program=program, status__in=(ChallengeBroadcast.Status.QUEUED,
                                     ChallengeBroadcast.Status.RUNNING)
    ).first()
    if running:
        return running, False
    count = eligible_challenge_users(program).count()
    if count == 0:
        raise ValidationError("کاربر واجد شرایطی برای ارسال وجود ندارد.")
    broadcast = ChallengeBroadcast.objects.create(
        program=program, started_by_id=admin_id, eligible_count=count,
    )
    transaction.on_commit(lambda: publish_challenge_broadcast(broadcast.pk))
    return broadcast, True


@transaction.atomic
def cancel_challenge_broadcast(*, brand_id, broadcast_id):
    job = ChallengeBroadcast.objects.select_for_update().get(
        pk=broadcast_id, program__brand_id=brand_id
    )
    if job.status in (ChallengeBroadcast.Status.QUEUED, ChallengeBroadcast.Status.RUNNING):
        job.status = ChallengeBroadcast.Status.CANCELLED
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at", "updated_at"])
    return job


def recover_challenge_broadcasts():
    """Beat retries a job if a queued Celery message was lost."""
    cutoff = timezone.now() - timedelta(minutes=2)
    for pk in ChallengeBroadcast.objects.filter(
        status__in=(ChallengeBroadcast.Status.QUEUED, ChallengeBroadcast.Status.RUNNING),
        updated_at__lt=cutoff,
    ).order_by("updated_at").values_list("pk", flat=True)[:20]:
        publish_challenge_broadcast(pk)
