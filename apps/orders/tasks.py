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


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def delete_confirmed_admin_receipts(self, payment_id: int):
    """Remove Telegram receipt copies after confirmation; preserve DB sale/payment."""
    from apps.orders.models import AdminReceiptMessage, Payment
    from django.utils import timezone

    payment = Payment.objects.select_related("brand").filter(pk=payment_id).first()
    if not payment or payment.status != Payment.PaymentStatus.CONFIRMED:
        return 0

    token = payment.brand.bot_token
    if not token:
        return 0

    async def remove(chat_id, message_id):
        bot = Bot(token=token, session=AiohttpSession(proxy=settings.SOCKS5_PROXY))
        try:
            return await bot.delete_message(chat_id=chat_id, message_id=message_id)
        finally:
            await bot.session.close()

    deleted = 0
    transient_error = None
    for receipt in AdminReceiptMessage.objects.filter(payment=payment, deleted_at__isnull=True):
        try:
            async_to_sync(remove)(receipt.chat_id, receipt.message_id)
        except TelegramBadRequest as exc:
            # Telegram forbids deleting some older messages; retain as undeleted
            # for audit, never treat these as a successful deletion.
            logger.warning("Cannot delete admin receipt %s: %s", receipt.pk, exc)
        except TelegramForbiddenError as exc:
            logger.warning("No permission to delete admin receipt %s: %s", receipt.pk, exc)
        except Exception as exc:
            transient_error = exc
            logger.exception("Telegram delete failed for admin receipt %s", receipt.pk)
        else:
            receipt.deleted_at = timezone.now()
            receipt.save(update_fields=["deleted_at"])
            deleted += 1
    if transient_error:
        raise self.retry(exc=transient_error)
    return deleted


@shared_task
def recover_confirmed_admin_receipt_deletions():
    """Recover deletion work even if the first queue attempt was lost."""
    from datetime import timedelta
    from django.utils import timezone
    from apps.orders.models import AdminReceiptMessage, Payment

    ids = (AdminReceiptMessage.objects.filter(
        deleted_at__isnull=True,
        created_at__gte=timezone.now() - timedelta(hours=47),
        payment__status=Payment.PaymentStatus.CONFIRMED,
    ).values_list("payment_id", flat=True).distinct()[:100])
    count = 0
    for payment_id in ids:
        try:
            delete_confirmed_admin_receipts.delay(payment_id)
            count += 1
        except Exception:
            logger.exception("Could not enqueue deletion recovery for payment %s", payment_id)
    return count
