import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


class TelegramSender:
    def __init__(self, bot: Bot, brand: str):
        self.brand_id = brand
        self.bot = bot

    def send(
        self,
        user_id: int,
        text: str,
        msg_type: str,
        file_id: str | None = None,
        buttons_data: list | None = None,
        parse_mode: str = "HTML",
    ):
        asyncio.run(
            self._send(
                user_id=user_id,
                text=text,
                msg_type=msg_type,
                file_id=file_id,
                buttons_data=buttons_data,
                parse_mode=parse_mode,
            )
        )

    async def _send(
        self,
        user_id: int,
        text: str,
        msg_type: str,
        file_id: str | None,
        buttons_data: list | None,
        parse_mode: str,
    ):

        keyboard = self._build_keyboard(buttons_data)
        bot = self.bot
        try:
            if msg_type == "text":
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=keyboard,
                )

            elif msg_type == "photo":
                await bot.send_photo(
                    chat_id=user_id,
                    photo=file_id,
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=keyboard,
                )

            elif msg_type == "video":
                await bot.send_video(
                    chat_id=user_id,
                    video=file_id,
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=keyboard,
                )

            elif msg_type == "document":
                await bot.send_document(
                    chat_id=user_id,
                    document=file_id,
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=keyboard,
                )

        except TelegramForbiddenError:
            logger.warning(
                "User %s blocked bot %s",
                user_id,
                self.brand_id,
            )

        except TelegramBadRequest as e:
            logger.error(
                "Bad request user=%s error=%s",
                user_id,
                e,
            )

        except TelegramRetryAfter as e:
            logger.warning(
                "Rate limited. sleep=%s",
                e.retry_after,
            )

            await asyncio.sleep(e.retry_after)

            await self._send(
                user_id=user_id,
                text=text,
                msg_type=msg_type,
                file_id=file_id,
                buttons_data=buttons_data,
                parse_mode=parse_mode,
            )

        finally:
            await bot.session.close()

    def _build_keyboard(
        self,
        buttons_data: list | None,
    ):

        if not buttons_data:
            return None

        rows = []

        for row in buttons_data:
            buttons = []

            for button in row:
                buttons.append(
                    InlineKeyboardButton(
                        text=button.get("text"),
                        callback_data=button.get("callback_data"),
                        url=button.get("url"),
                    )
                )

            rows.append(buttons)

        return InlineKeyboardMarkup(inline_keyboard=rows)
