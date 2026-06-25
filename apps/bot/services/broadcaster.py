import asyncio
import json
import logging

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.conf import settings

logger = logging.getLogger(__name__)


class BroadcastSubscriber:
    def __init__(self, bot: Bot, brand_id: str):
        self.bot = bot
        self.brand_id = brand_id
        # اتصال به ردیس
        self.redis = aioredis.from_url(settings.REDIS_URL)

    def _build_keyboard(self, buttons_data: list) -> InlineKeyboardMarkup:
        """ساخت کیبورد از روی دیتای دریافت شده از ردیس"""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        if not buttons_data:
            return None

        for row in buttons_data:
            button_row = []
            for button in row:
                if isinstance(button, dict):
                    btn = InlineKeyboardButton(
                        text=button.get("text", ""),
                        callback_data=button.get("callback_data"),
                        url=button.get("url"),
                        web_app=button.get("web_app"),
                    )
                    button_row.append(btn)
            keyboard.inline_keyboard.append(button_row)
        return keyboard

    async def _send_single_message(self, user_id: int, payload: dict):
        """ارسال پیام به یک کاربر مشخص با پشتیبانی از انواع message"""
        text = payload.get("text", "")
        msg_type = payload.get("msg_type", "text")
        file_id = payload.get("file_id")
        keyboard = self._build_keyboard(payload.get("buttons_data"))
        parse_mode = payload.get("parse_mode", "HTML")

        try:
            if msg_type == "text":
                await self.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
            elif msg_type == "photo":
                await self.bot.send_photo(
                    chat_id=user_id,
                    photo=file_id,
                    caption=text,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
            elif msg_type == "video":
                await self.bot.send_video(
                    chat_id=user_id,
                    video=file_id,
                    caption=text,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
            elif msg_type == "document":
                await self.bot.send_document(
                    chat_id=user_id,
                    document=file_id,
                    caption=text,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )

        except TelegramRetryAfter as e:
            logger.warning(f"Rate limit hit. Sleeping for {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            # تلاش مجدد بعد از صبر کردن
            await self._send_single_message(user_id, payload)

        except TelegramForbiddenError:
            logger.warning(f"User {user_id} blocked the bot.")
            # در اینجا می‌توانید کاربر را در دیتابیس غیرفعال کنید

        except TelegramBadRequest as e:
            logger.error(f"Bad request for user {user_id}: {e}")

        except Exception as e:
            logger.error(f"Failed to send broadcast to {user_id}: {e}")

    async def start_listening(self):
        """شروع گوش دادن به کانال ردیس برای دریافت پیام‌های بروکدکست"""
        pubsub = self.redis.pubsub()
        channel_name = f"broadcast_channel:{self.brand_id}"
        await pubsub.subscribe(channel_name)

        logger.info(f"Started listening for broadcasts on channel: {channel_name}")

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        payload = json.loads(message["data"])
                        user_ids = payload.get("user_ids", [])

                        # ارسال پیام به کاربران (به صورت یکی یکی برای جلوگیری از بن شدن بات)
                        for user_id in user_ids:
                            await self._send_single_message(user_id, payload)
                            # تاخیر بسیار کوتاه برای احترام به محدودیت‌های تلگرام
                            await asyncio.sleep(0.05)

                    except json.JSONDecodeError:
                        logger.error("Failed to decode broadcast payload from Redis")
        except Exception as e:
            logger.error(f"Redis subscriber error: {e}")
        finally:
            await pubsub.unsubscribe(channel_name)
            await self.redis.close()
