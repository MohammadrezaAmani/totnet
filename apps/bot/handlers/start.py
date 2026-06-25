"""
Start and Welcome Handler for Multi-Tenant VPN Bot
"""

import logging
import re

from aiogram import types
from aiogram.filters import Command

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.referrals.models import Referral, ReferralLink

from .base import BaseHandler

logger = logging.getLogger(__name__)


class StartHandler(BaseHandler):
    """Handle /start command and initial user registration"""

    async def handle_start_command(
        self, message: types.Message, command: Command = None
    ):
        """Handle /start command with optional referral code"""
        telegram_user = message.from_user
        chat_id = message.chat.id

        referral_code = None
        if command and hasattr(command, "args") and command.args:
            referral_code = command.args

        user = await self.get_or_create_user(telegram_user)

        if referral_code and not user.referred_by:
            await self.process_referral(user, referral_code)

        if not user.full_name or not user.phone_number:
            await self.show_profile_setup(chat_id, user)
        else:
            await self.show_main_menu(chat_id, user)

    async def process_referral(self, user: User, referral_code: str):
        """Process referral registration"""
        try:
            referral_link = await ReferralLink.objects.aget(
                code=referral_code, brand=self.brand, is_active=True
            )

            await Referral.objects.acreate(
                referrer=referral_link.user,
                referee=user,
                brand=self.brand,
                referral_link=referral_link,
                status=Referral.ReferralStatus.PENDING,
            )

            user.referred_by = referral_link.user
            await user.asave()

            referral_link.click_count = (referral_link.click_count or 0) + 1
            await referral_link.asave()

            logger.info(
                f"Referral processed: {user.telegram_id} referred by {referral_link.user.telegram_id}"
            )

        except ReferralLink.DoesNotExist:
            logger.warning(f"Invalid referral code: {referral_code}")
        except Exception as e:
            logger.error(f"Error processing referral: {e}")

    async def show_profile_setup(self, chat_id: int, user: User):
        """Show profile setup screen for new users"""
        await self.update_user_state(user, BotState.StateType.PROFILE_SETUP)

        welcome_text = f"""
🎉 خوش آمدید به {self.brand.name}!

برای شروع، لطفاً پروفایل خود را تکمیل کنید:

👤 نام کامل: {"✅" if user.full_name else "❌"}
📱 شماره تلفن: {"✅" if user.phone_number else "❌"}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "✏️ تکمیل پروفایل", "callback_data": "setup_profile"}],
                [{"text": "⏭️ رد کردن", "callback_data": "skip_profile"}],
            ]
        )

        await self.send_message_with_keyboard(chat_id, welcome_text, keyboard)

    async def show_main_menu(self, chat_id: int, user: User):
        """Show main menu"""
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        subscription_count = await user.subscriptions.filter(
            brand=self.brand, status="active"
        ).acount()

        wallet_balance = user.wallet_balance
        referral_count = user.referral_count

        welcome_text = f"""
🏠 منوی اصلی {self.brand.name}

👋 سلام {user.full_name or user.first_name}!

📊 وضعیت شما:
• اشتراک‌های فعال: {subscription_count}
• موجودی کیف پول: {self.format_price(wallet_balance, self.brand.currency)}
• تعداد معرفی‌ها: {referral_count}
• سطح: {user.level}

لطفاً یکی از گزینه‌های زیر را انتخاب کنید:
        """

        keyboard = await self.get_main_menu_keyboard(user)

        await self.send_message_with_keyboard(chat_id, welcome_text, keyboard)

    async def handle_profile_setup_callback(self, callback: types.CallbackQuery):
        """Handle profile setup callback"""
        user = await self.get_or_create_user(callback.from_user)

        if callback.data == "setup_profile":
            await self.start_profile_setup(callback.message.chat.id, user)
        elif callback.data == "skip_profile":
            await self.show_main_menu(callback.message.chat.id, user)

        await callback.answer()

    async def start_profile_setup(self, chat_id: int, user: User):
        """Start profile setup process"""
        await self.update_user_state(
            user, BotState.StateType.PROFILE_SETUP, {"step": "name"}
        )

        text = """
✏️ تکمیل پروفایل

لطفاً نام کامل خود را وارد کنید:
        """

        keyboard = self.get_back_keyboard("main_menu")

        await self.send_message_with_keyboard(chat_id, text, keyboard)

    async def handle_profile_setup_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle profile setup messages"""
        step = state.state_data.get("step")

        if step == "name":
            name = message.text.strip()
            if len(name) < 2:
                await message.reply("❌ نام باید حداقل 2 کاراکتر باشد.")
                return

            user.full_name = name
            await user.asave()

            await self.update_user_state(
                user, BotState.StateType.PROFILE_SETUP, {"step": "phone"}
            )

            keyboard = self.create_keyboard(
                [
                    [{"text": "📱 ارسال شماره تلفن", "callback_data": "request_phone"}],
                    [{"text": "⏭️ رد کردن", "callback_data": "skip_phone"}],
                    [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
                ]
            )

            await self.send_message_with_keyboard(
                message.chat.id,
                "✅ نام شما ثبت شد.\n\n📱 لطفاً شماره تلفن خود را وارد کنید:",
                keyboard,
            )

        elif step == "phone":
            phone = message.text.strip()

            if re.match(r"^(\+98|0)?9\d{9}$", phone):
                user.phone_number = phone
                await user.asave()

                await message.reply("✅ پروفایل شما با موفقیت تکمیل شد!")
                await self.show_main_menu(message.chat.id, user)
            else:
                await message.reply(
                    "❌ شماره تلفن معتبر نیست. لطفاً شماره موبایل ایرانی وارد کنید."
                )

    async def handle_referral_setup(self, callback: types.CallbackQuery):
        """Handle referral system setup - DEPRECATED, use ReferralsHandler instead"""

        logger.warning(
            "handle_referral_setup called on StartHandler - please use ReferralsHandler instead"
        )
        await callback.answer("❌ استفاده از منوی اصلی کنید.")
