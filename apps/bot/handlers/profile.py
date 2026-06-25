"""
Profile Handler for Multi-Tenant VPN Bot
Handles user profile management and statistics
"""

import logging
import re

from aiogram import types

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.subscriptions.models import Subscription

from .base import BaseHandler

logger = logging.getLogger(__name__)


class ProfileHandler(BaseHandler):
    """Handle user profile operations"""

    async def show_my_profile(self, callback: types.CallbackQuery):
        user = await self.get_or_create_user(callback.from_user)

        subscription_count = await Subscription.objects.filter(
            user=user, brand=self.brand, status="active"
        ).acount()

        from apps.orders.models import Wallet

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
            wallet_balance = wallet.balance
        except Wallet.DoesNotExist:
            wallet_balance = 0

        text = f"""
👤 پروفایل من

👨‍💼 نام: {user.full_name or user.first_name or "ثبت نشده"}
📱 تلفن: {user.phone_number or "ثبت نشده"}
👤 نام کاربری: {user.username}
📧 ایمیل: {user.email or "ثبت نشده"}
📅 تاریخ عضویت: {user.created_at.strftime("%Y/%m/%d") if user.created_at else "نامشخص"}

📊 وضعیت:
• اشتراک‌های فعال: {subscription_count}
• موجودی کیف پول: {self.format_price(wallet_balance, self.brand.currency)}
• سطح کاربری: {user.level}
• امتیازات: {user.reward_points}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "✏️ ویرایش پروفایل", "callback_data": "edit_profile"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        await self._render(callback, text, keyboard)
        await callback.answer()

    async def edit_profile(self, callback: types.CallbackQuery):
        user = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user, BotState.StateType.PROFILE_EDIT, {"step": "menu"}
        )

        text = """
✏️ ویرایش پروفایل

کدام بخش را می‌خواهید تغییر دهید؟
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "👨‍💼 نام کامل", "callback_data": "edit_full_name"}],
                [{"text": "📱 شماره تلفن", "callback_data": "edit_phone"}],
                [{"text": "📧 ایمیل", "callback_data": "edit_email"}],
                [{"text": "🔙 بازگشت", "callback_data": "my_profile"}],
            ]
        )

        await self._render(callback, text, keyboard)
        await callback.answer()

    async def request_field_update(self, callback: types.CallbackQuery, field: str):
        user = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user,
            BotState.StateType.PROFILE_EDIT,
            {"field": field, "step": "waiting_input"},
        )

        field_names = {
            "full_name": "نام کامل",
            "phone": "شماره تلفن",
            "email": "ایمیل",
        }

        text = f"""
✏️ تغییر {field_names.get(field, field)}

مقدار جدید را ارسال کنید:
        """

        keyboard = self.get_back_keyboard("edit_profile")

        await self._render(callback, text, keyboard)
        await callback.answer()

    async def handle_profile_field_message(
        self, message: types.Message, user: User, state: BotState
    ):
        field = state.state_data.get("field")
        value = message.text.strip()

        error = None

        if field == "full_name":
            if len(value) < 2:
                error = "نام باید حداقل 2 کاراکتر باشد."
            else:
                user.full_name = value

        elif field == "phone":
            if not re.match(r"^(\+98|0)?9\d{9}$", value):
                error = "شماره تلفن معتبر نیست."
            else:
                user.phone_number = value

        elif field == "email":
            if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", value):
                error = "ایمیل معتبر نیست."
            else:
                user.email = value

        if error:
            await self.update_user_state(
                user, BotState.StateType.PROFILE_EDIT, state.state_data
            )

            await self._render_message(
                message, f"❌ {error}", self.get_back_keyboard("edit_profile")
            )
            return

        await user.asave()

        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        await self._render_message(
            message,
            "✅ تغییر با موفقیت اعمال شد\n\nمنوی اصلی:",
            await self.get_main_menu_keyboard(),
        )

    async def _render(self, callback, text, keyboard):
        """Always edit single bot message"""
        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception:
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

    async def _render_message(self, message, text, keyboard):
        """Replace user message context with single bot message"""
        try:
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)
        except Exception as e:
            logger.warning(f"render_message failed: {e}")
