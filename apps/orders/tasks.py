"""Scheduled deletion of transactional Telegram messages."""

import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings

from apps.brands.models import Brand

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=20)
def delete_transient_payment_message(self, *, brand_id: int, chat_id: int, message_id: int):
    token = Brand.objects.filter(pk=brand_id).values_list("bot_token", flat=True).first()
    if not token:
        return False

    async def remove():
        bot = Bot(token=token, session=AiohttpSession(proxy=settings.SOCKS5_PROXY))
        try:
            return await bot.delete_message(chat_id=chat_id, message_id=message_id)
        finally:
            await bot.session.close()

    try:
        return bool(async_to_sync(remove)())
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except Exception as exc:
        logger.warning("Transient payment message deletion failed for brand %s", brand_id)
        raise self.retry(exc=exc)
