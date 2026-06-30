"""
Start and Welcome Handler for Multi-Tenant VPN Bot
"""

import logging
import re
from typing import Optional

from aiogram import types
from aiogram.filters import Command
from asgiref.sync import sync_to_async
from django.db import transaction

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.brands.models import BrandConfiguration
from apps.brands.utils import renderer

from .base import BaseHandler

logger = logging.getLogger(__name__)


class StartHandler(BaseHandler):
    """Handle /start command and initial user registration"""

    async def handle_start_command(
        self, message: types.Message, command: Optional[Command] = None
    ):
        """Handle /start command with optional referral code"""
        telegram_user = message.from_user
        chat_id = message.chat.id

        # Extract referral code from command args
        referral_code = None
        if command and command.args:
            referral_code = command.args.strip()

        # Get or create user (with proper cache key fix in BaseHandler)
        user, created = await self.get_or_create_user(telegram_user)

        if created:
            # Process referral in a separate transaction to avoid deadlocks
            if referral_code and not user.referred_by:
                await self.process_referral_safely(user, referral_code)
            # Show profile setup for new users
            # await self.show_profile_setup(chat_id, user)
            await self.show_main_menu(chat_id, user)
        else:
            await self.show_main_menu(chat_id, user)

    async def process_referral_safely(self, user: User, referral_code: str):
        """Process referral registration safely using sync_to_async for all DB ops"""
        try:
            await self._process_referral_sync(user.id, referral_code, self.brand.id)
        except Exception as e:
            logger.error(f"Error processing referral: {e}")

    @sync_to_async
    def _process_referral_sync(self, user_id: int, referral_code: str, brand_id: int):
        """Run all referral logic synchronously to avoid deadlock"""
        try:
            with transaction.atomic():
                from apps.accounts.models import User as UserModel
                from apps.referrals.models import Referral, ReferralLink

                user = UserModel.objects.select_for_update().get(
                    id=user_id, brand_id=brand_id
                )

                # Check if already has a referrer
                if user.referred_by:
                    return

                try:
                    referral_link = ReferralLink.objects.select_for_update().get(
                        code=referral_code, brand_id=brand_id, is_active=True
                    )
                except ReferralLink.DoesNotExist:
                    logger.warning(f"Invalid referral code: {referral_code}")
                    return

                # Prevent self-referral
                if referral_link.user_id == user_id:
                    logger.warning(f"Self-referral attempt: {user_id}")
                    return

                # Create referral record
                Referral.objects.create(
                    referrer=referral_link.user,
                    referee=user,
                    brand_id=brand_id,
                    referral_link=referral_link,
                    status=Referral.ReferralStatus.PENDING,
                )

                # Update user
                user.referred_by = referral_link.user
                user.referral_count = (user.referral_count or 0) + 1
                user.save(update_fields=["referred_by", "referral_count", "updated_at"])

                # Update referral link click count
                ReferralLink.objects.filter(id=referral_link.id).update(
                    click_count=(referral_link.click_count or 0) + 1
                )

                logger.info(
                    f"Referral processed: user {user.telegram_id} "
                    f"referred by {referral_link.user.telegram_id}"
                )

        except User.DoesNotExist:
            logger.error(f"User not found: {user_id}")
        except Exception as e:
            logger.error(f"Error in referral processing: {e}")
            raise

    async def show_profile_setup(self, chat_id: int, user: User):
        """Show profile setup screen for new users"""
        await self.update_user_state(user, BotState.StateType.PROFILE_SETUP)

        name_status = "✅" if user.full_name else "❌"
        phone_status = "✅" if user.phone_number else "❌"

        welcome_text = f"""
🎉 خوش آمدید به {self.brand.name}!

برای شروع، لطفاً پروفایل خود را تکمیل کنید:

👤 نام کامل: {name_status}
📱 شماره تلفن: {phone_status}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "✏️ تکمیل پروفایل", "callback_data": "setup_profile"}],
                [{"text": "⏭️ رد کردن", "callback_data": "skip_profile"}],
            ]
        )

        await self.send_message_with_keyboard(chat_id, welcome_text, keyboard)

    async def get_config(self) -> BrandConfiguration:
        """Get brand configuration - use async properly"""
        # If self.brand.configuration is already loaded, just return it
        if hasattr(self.brand, "_configuration_cache"):
            return self.brand._configuration_cache

        # Otherwise fetch it
        from django.core.cache import cache

        cache_key = f"brand_config:{self.brand.id}"
        config = await cache.aget(cache_key)

        if config is None:
            config = await BrandConfiguration.objects.aget(brand=self.brand)
            await cache.aset(cache_key, config, timeout=300)

        self.brand._configuration_cache = config
        return config

    async def show_main_menu(
        self, chat_id: int, user: User, callback: Optional[types.CallbackQuery] = None
    ):
        """Show main menu to user"""
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        try:
            config = await self.get_config()
        except BrandConfiguration.DoesNotExist:
            config = None

        # Get counts safely
        subscription_count = await self._get_subscription_count(user.id, self.brand.id)
        referral_count = user.referral_count or 0
        wallet_balance = user.wallet_balance or 0

        context = {
            "name": user.full_name or user.first_name or "کاربر",
            "level": user.level or "عادی",
            "wallet": self.format_price(wallet_balance, self.brand.currency),
            "subscriptions": subscription_count,
            "referrals": referral_count,
            "brand": self.brand.name,
        }

        template = config.welcome_message if config else None

        if not template:
            template = """
🏠 منوی اصلی {brand}

👋 سلام {name}!

📊 وضعیت شما:
• اشتراک‌های فعال: {subscriptions}
• موجودی کیف پول: {wallet}
• تعداد معرفی‌ها: {referrals}
• سطح: {level}

لطفاً یکی از گزینه‌های زیر را انتخاب کنید:
            """

        welcome_text = renderer.render(template, context)
        keyboard = await self.get_main_menu_keyboard(user)

        if callback:
            try:
                await self.edit_message_with_keyboard(
                    callback.message.chat.id,
                    callback.message.message_id,
                    welcome_text,
                    keyboard,
                )
            except Exception:
                # If edit fails (e.g., message too old), send new message
                await self.send_message_with_keyboard(chat_id, welcome_text, keyboard)
            finally:
                await callback.answer()
        else:
            await self.send_message_with_keyboard(chat_id, welcome_text, keyboard)

    @sync_to_async
    def _get_subscription_count(self, user_id: int, brand_id: int) -> int:
        """Get active subscription count synchronously"""
        from apps.subscriptions.models import Subscription

        return Subscription.objects.filter(
            user_id=user_id, brand_id=brand_id, status="active"
        ).count()

    async def handle_profile_setup_callback(self, callback: types.CallbackQuery):
        """Handle profile setup callback"""
        user, _ = await self.get_or_create_user(callback.from_user)

        if callback.data == "setup_profile":
            await self.start_profile_setup(callback.message.chat.id, user)
        elif callback.data == "skip_profile":
            await self.show_main_menu(callback.message.chat.id, user)
        elif callback.data == "main_menu":
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
        step = state.state_data.get("step") if state.state_data else None

        if step == "name":
            await self._handle_name_step(message, user)
        elif step == "phone":
            await self._handle_phone_step(message, user)

    async def _handle_name_step(self, message: types.Message, user: User):
        """Handle name input step"""
        name = message.text.strip()
        if len(name) < 2:
            await message.reply("❌ نام باید حداقل ۲ کاراکتر باشد.")
            return

        # Update name using sync to avoid signal issues
        await self._update_user_name(user.id, name)

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

    @sync_to_async
    def _update_user_name(self, user_id: int, name: str):
        """Update user name synchronously"""
        User.objects.filter(id=user_id).update(full_name=name)

    async def _handle_phone_step(self, message: types.Message, user: User):
        """Handle phone input step"""
        phone = message.text.strip()

        # Normalize phone number
        phone = self._normalize_phone(phone)

        if not self._is_valid_iranian_phone(phone):
            await message.reply(
                "❌ شماره تلفن معتبر نیست.\n\n"
                "لطفاً شماره موبایل ایرانی وارد کنید.\n"
                "مثال: 09123456789 یا +989123456789"
            )
            return

        await self._update_user_phone(user.id, phone)

        await message.reply("✅ پروفایل شما با موفقیت تکمیل شد!")
        await self.show_main_menu(message.chat.id, user)

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Normalize phone number format"""
        phone = phone.strip()
        if phone.startswith("+98"):
            phone = "0" + phone[3:]
        elif phone.startswith("98") and not phone.startswith("0"):
            phone = "0" + phone[2:]
        return phone

    @staticmethod
    def _is_valid_iranian_phone(phone: str) -> bool:
        """Validate Iranian mobile phone number"""
        return bool(re.match(r"^09\d{9}$", phone))

    @sync_to_async
    def _update_user_phone(self, user_id: int, phone: str):
        """Update user phone synchronously"""
        User.objects.filter(id=user_id).update(phone_number=phone)

    async def handle_referral_setup(self, callback: types.CallbackQuery):
        """Handle referral system setup - DEPRECATED"""
        logger.warning(
            "handle_referral_setup is deprecated - use ReferralsHandler instead"
        )
        await callback.answer("❌ لطفاً از منوی اصلی استفاده کنید.")
