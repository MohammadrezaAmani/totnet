"""Durable delivery of operational notices to the configured brand channel."""
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.bot.models import OperationalNotice
from apps.brands.models import BrandConfiguration

logger = logging.getLogger(__name__)
CLAIM_PERIOD = timedelta(minutes=5)


@shared_task(bind=True, max_retries=5, default_retry_delay=90)
def send_operational_notice(self, notice_id):
    now = timezone.now()
    with transaction.atomic():
        notice = (OperationalNotice.objects.select_for_update()
                  .select_related("brand").filter(pk=notice_id).first())
        if notice is None or notice.sent_at:
            return False
        if notice.delivery_claimed_at and notice.delivery_claimed_at > now - CLAIM_PERIOD:
            return False
        target = BrandConfiguration.objects.filter(brand_id=notice.brand_id).values_list(
            "operations_channel_id", flat=True,
        ).first()
        if not target or not notice.brand.bot_token:
            return False
        notice.delivery_claimed_at = now
        notice.save(update_fields=["delivery_claimed_at"])
        token, text, brand_id = notice.brand.bot_token, notice.text, notice.brand_id

    async def send():
        bot = Bot(token, session=AiohttpSession(proxy=settings.SOCKS5_PROXY))
        try:
            chat = int(target) if target.lstrip("-").isdigit() else target
            return await bot.send_message(chat_id=chat, text=text)
        finally:
            await bot.session.close()

    try:
        sent = async_to_sync(send)()
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.error("Operations channel unavailable for brand %s: %s", brand_id, type(exc).__name__)
        with transaction.atomic():
            OperationalNotice.objects.filter(pk=notice_id, sent_at__isnull=True).update(
                delivery_claimed_at=None
            )
        return False
    except Exception as exc:
        OperationalNotice.objects.filter(pk=notice_id, sent_at__isnull=True).update(
            delivery_claimed_at=None
        )
        raise self.retry(exc=exc)

    with transaction.atomic():
        updated = OperationalNotice.objects.filter(pk=notice_id, sent_at__isnull=True).update(
            sent_at=timezone.now(), telegram_message_id=sent.message_id,
            delivery_claimed_at=None,
        )
    return bool(updated)


@shared_task
def recover_operational_notices(limit=100):
    threshold = timezone.now() - CLAIM_PERIOD
    candidates = list(OperationalNotice.objects.filter(
        sent_at__isnull=True, brand__status="active",
    ).filter(Q(delivery_claimed_at__isnull=True) | Q(delivery_claimed_at__lte=threshold))
        .order_by("pk").values_list("pk", flat=True)[:limit])
    for pk in candidates:
        send_operational_notice.delay(pk)
    return len(candidates)
