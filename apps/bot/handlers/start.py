"""
Start and Welcome Handler for Multi-Tenant VPN Bot
"""

import logging
from html import escape
from typing import Optional

from aiogram import types
from aiogram.filters import Command
from asgiref.sync import sync_to_async
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User, UserProfile
from apps.bot.models import BotState
from apps.bot.admission import INVITE_REQUIRED_MESSAGE, InvitationRequired
from apps.brands.models import BrandConfiguration
from apps.brands.utils import renderer
from apps.subscriptions.models import Subscription, SubscriptionClaim
from utils.phone import normalize_iranian_phone, is_valid_iranian_phone

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
        try:
            user, created = await self.get_or_create_user(
                telegram_user, referral_code=referral_code
            )
        except InvitationRequired:
            await message.answer(INVITE_REQUIRED_MESSAGE)
            return

        if referral_code:
            await self.process_referral_safely(user, referral_code)

        if created:
            await self.show_profile_setup(chat_id, user)
        else:
            await self.show_main_menu(chat_id, user)

    async def process_referral_safely(self, user: User, referral_code: str):
        """Process referral registration safely using sync_to_async for all DB ops"""
        try:
            await self._process_referral_sync(user.id, referral_code, self.brand.id)
        except Exception as exc:
            logger.error("Referral attribution failed (%s)", type(exc).__name__)

    @sync_to_async
    def _process_referral_sync(self, user_id: int, referral_code: str, brand_id: int):
        """Execute the ORM transaction outside the asynchronous bot loop."""
        from apps.referrals.services import attribute_referral

        from apps.referrals.services import track_referral_click

        track_referral_click(code=referral_code, brand_id=brand_id, visitor_id=user_id)
        return attribute_referral(
            user_id=user_id, brand_id=brand_id, code=referral_code
        )

    async def show_profile_setup(self, chat_id: int, user: User):
        """Show profile setup screen for new users"""
        await self.update_user_state(user, BotState.StateType.PROFILE_SETUP)

        name_status = "✅" if user.full_name else "❌"
        phone_status = "✅" if user.phone_number else "❌"
        profile = await UserProfile.objects.filter(user=user).afirst()
        device_status = "✅" if profile and profile.device_type else "❌"

        welcome_text = f"""
🎉 خوش آمدید به {self.brand.name}!

اگر از قبل اشتراک فعال دارید، ابتدا یوزرنیم آن را ثبت کنید.
اگر اشتراک فعال ندارید، تکمیل اولیه پروفایل را ادامه دهید.

👤 نام کامل: {name_status}
📱 شماره تلفن: {phone_status}
📱 دستگاه‌ها: {device_status}
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "✅ اشتراک فعال دارم / ثبت اشتراک",
                        "callback_data": "onboarding_existing_subscription",
                    }
                ],
                [
                    {
                        "text": "🆕 اشتراک فعال ندارم",
                        "callback_data": "onboarding_no_subscription",
                    }
                ],
            ]
        )

        await self.send_profile_prompt(chat_id, user, welcome_text, keyboard)
        await BotState.objects.filter(user=user, brand=self.brand).aupdate(
            welcome_shown_at=timezone.now()
        )

    async def start_existing_subscription_registration(
        self, callback: types.CallbackQuery
    ):
        """Collect an existing VPN username without trusting it as ownership proof."""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.SUBSCRIPTION_MANAGEMENT,
            {"step": "claim_existing_subscription"},
        )
        profile = await UserProfile.objects.filter(user=user).afirst()
        profile_complete = bool(
            user.full_name and user.phone_number and profile and profile.device_type
        )
        back_callback = (
            "my_subscriptions" if profile_complete else "onboarding_no_subscription"
        )
        text = (
            "📱 <b>ثبت اشتراک فعال</b>\n\n"
            "یوزرنیم اشتراک فعال خود را وارد کنید.\n"
            "اشتراکی که برای شما خریداری شده باشد با تطبیق حساب تلگرام شناسایی می‌شود؛ "
            "در غیر این صورت برای جلوگیری از انتقال اشتراک دیگران، درخواست شما برای بررسی ثبت خواهد شد."
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.get_back_keyboard(back_callback),
        )
        await self.remember_profile_prompt(user, callback.message.message_id)
        await callback.answer()

    async def handle_existing_subscription_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Record one existing-subscription username during onboarding."""
        step = (state.state_data or {}).get("step")
        if step != "claim_existing_subscription":
            return
        username = (message.text or "").strip()
        if (
            len(username) < 2
            or len(username) > 100
            or any(char.isspace() for char in username)
            or any(ord(char) < 32 for char in username)
        ):
            await message.reply(
                "❌ یوزرنیم معتبر نیست. یوزرنیم را بدون فاصله و حداکثر ۱۰۰ کاراکتر وارد کنید."
            )
            return

        from apps.subscriptions.claims import claim_designated_gift
        gift = await sync_to_async(claim_designated_gift)(
            user_id=user.pk, brand_id=self.brand.pk, username=username,
            telegram_username=message.from_user.username,
        )
        existing = gift or await (
            Subscription.objects.filter(brand=self.brand, owner=user)
            .filter(
                Q(connectix_username__iexact=username)
                | Q(vpn_user_email__iexact=username)
            )
            .select_related("plan")
            .afirst()
        )
        if existing:
            result_text = (
                ("✅ اشتراک به حساب شما متصل شد.\n" if gift else "✅ این اشتراک از قبل به حساب شما متصل است.\n")
                + f"🏷 طرح: {escape(existing.plan.name)}"
            )
        else:
            claim = await SubscriptionClaim.objects.filter(
                user=user, brand=self.brand, username__iexact=username
            ).afirst()
            if claim is None:
                claim = await SubscriptionClaim.objects.acreate(
                    user=user,
                    brand=self.brand,
                    username=username,
                    status=SubscriptionClaim.Status.PENDING,
                )
            elif claim.status != SubscriptionClaim.Status.APPROVED:
                claim.username = username
                claim.status = SubscriptionClaim.Status.PENDING
                claim.admin_note = ""
                claim.reviewed_by = None
                claim.reviewed_at = None
                await claim.asave(
                    update_fields=[
                        "username",
                        "status",
                        "admin_note",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
            result_text = (
                "✅ درخواست ثبت اشتراک دریافت شد.\n"
                f"👤 یوزرنیم: <code>{escape(username)}</code>\n\n"
                "برای امنیت، دانستن یوزرنیم به‌تنهایی باعث انتقال مالکیت نمی‌شود. "
                "پس از تأیید، اشتراک در «اشتراک‌های من» نمایش داده می‌شود."
            )

        await self.update_user_state(
            user,
            BotState.StateType.SUBSCRIPTION_MANAGEMENT,
            {"step": "claim_existing_subscription"},
        )
        profile = await UserProfile.objects.filter(user=user).afirst()
        profile_complete = bool(
            user.full_name and user.phone_number and profile and profile.device_type
        )
        continue_button = (
            {"text": "🏠 منوی اصلی", "callback_data": "main_menu"}
            if profile_complete
            else {
                "text": "➡️ تکمیل پروفایل",
                "callback_data": "onboarding_finish_claims",
            }
        )
        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "➕ ثبت اشتراک دیگر",
                        "callback_data": "onboarding_add_subscription",
                    }
                ],
                [continue_button],
            ]
        )
        await self.send_profile_prompt(message.chat.id, user, result_text, keyboard)

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
        await self.clear_previous_keyboard(chat_id, user)
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        try:
            config = await self.get_config()
        except BrandConfiguration.DoesNotExist:
            config = None

        from apps.orders.models import Wallet
        from apps.referrals.selectors import reward_summary
        from apps.subscriptions.models import Subscription

        from django.utils import timezone

        now = timezone.now()
        subscription_count = await (
            Subscription.objects.filter(
                (Q(user_id=user.id) | Q(owner_id=user.id)),
                brand_id=self.brand.id,
                status=Subscription.SubscriptionStatus.ACTIVE,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .acount()
        )
        try:
            wallet = await Wallet.objects.aget(user_id=user.id, brand_id=self.brand.id)
            wallet_balance = wallet.balance
        except Wallet.DoesNotExist:
            wallet_balance = 0
        rewards = await reward_summary(user_id=user.id, brand_id=self.brand.id)
        referral_count = user.referral_count or 0

        context = {
            "name": user.full_name or user.first_name or "کاربر",
            "level": rewards["level_title"],
            "lifetime_points": rewards["lifetime_points"],
            "liquid_points": rewards["liquid_points"],
            "wallet": self.format_price(wallet_balance, self.brand.currency),
            "subscriptions": subscription_count,
            "referrals": referral_count,
            "brand": self.brand.name,
        }

        state = await self.get_user_state(user)
        template = config.welcome_message if config and not state.welcome_shown_at else None

        if not template:
            template = """
🏠 منوی اصلی

👤 {name}

📊 وضعیت شما:
• اشتراک‌های فعال: {subscriptions}
• موجودی نقد کیف پول: {wallet}
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
        if not state.welcome_shown_at:
            state.welcome_shown_at = timezone.now()
            await state.asave(update_fields=["welcome_shown_at"])

    async def handle_profile_setup_callback(self, callback: types.CallbackQuery):
        """Handle profile setup callback"""
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)
        step = (state.state_data or {}).get("step")
        required_step = (
            "device" if callback.data.startswith("setup_device_") else "phone"
        )
        if (
            callback.data in {"request_phone", "skip_phone"}
            or callback.data.startswith("setup_device_")
        ) and (
            state.current_state != BotState.StateType.PROFILE_SETUP
            or step != required_step
        ):
            await callback.answer(
                "این مرحله پایان یافته است. از منوی اصلی استفاده کنید."
            )
            return
        if callback.data.startswith("onboarding_") and state.current_state not in {
            BotState.StateType.PROFILE_SETUP,
            BotState.StateType.SUBSCRIPTION_MANAGEMENT,
        }:
            await callback.answer(
                "این مرحله پایان یافته است. از منوی اصلی استفاده کنید."
            )
            return

        if state.last_message_id != callback.message.message_id:
            await self.clear_previous_keyboard(callback.message.chat.id, user)
        await self.remember_profile_prompt(user, callback.message.message_id)

        if callback.data in {
            "setup_profile",
            "onboarding_no_subscription",
            "onboarding_finish_claims",
        }:
            await self.start_profile_setup(callback.message.chat.id, user)
        elif callback.data in {
            "onboarding_existing_subscription",
            "onboarding_add_subscription",
        }:
            await self.start_existing_subscription_registration(callback)
            return
        elif callback.data == "request_phone":
            await self.request_phone_contact(callback.message.chat.id)
        elif callback.data == "skip_phone":
            await self.show_device_setup(callback.message.chat.id, user)
        elif callback.data.startswith("setup_device_"):
            await self.set_initial_device(
                callback.message.chat.id,
                user,
                callback.data.removeprefix("setup_device_"),
            )
        elif callback.data in {"skip_profile", "main_menu"}:
            # Kept for compatibility with older messages already sent before this release.
            await self.show_main_menu(callback.message.chat.id, user)

        await callback.answer()

    async def request_phone_contact(self, chat_id: int):
        """Ask Telegram to share the current user's own phone number."""
        keyboard = types.ReplyKeyboardMarkup(
            keyboard=[
                [
                    types.KeyboardButton(
                        text="📱 ارسال شماره تلفن من", request_contact=True
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await self.bot.send_message(
            chat_id,
            "برای ثبت شماره، دکمه زیر را بزنید تا شماره خودتان از تلگرام ارسال شود.",
            reply_markup=keyboard,
        )

    async def handle_contact_message(self, message: types.Message, user: User):
        state = await self.get_user_state(user)
        if (
            state.current_state != BotState.StateType.PROFILE_SETUP
            or (state.state_data or {}).get("step") != "phone"
        ):
            await message.reply("برای تغییر شماره از بخش ویرایش پروفایل استفاده کنید.")
            return
        contact = message.contact
        if not contact or contact.user_id != message.from_user.id:
            await message.reply("لطفاً شماره تماس خودتان را از تلگرام ارسال کنید.")
            return
        phone = self._normalize_phone(contact.phone_number)
        if not self._is_valid_iranian_phone(phone):
            await message.reply(
                "شماره ارسال‌شده معتبر نیست. شماره موبایل ایران باید ۱۱ رقم باشد."
            )
            return
        await self._update_user_phone(user.id, phone)
        user.phone_number = phone
        await message.answer(
            "✅ شماره تلفن ثبت شد.", reply_markup=types.ReplyKeyboardRemove()
        )
        await self.show_device_setup(message.chat.id, user)

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
        await self.send_profile_prompt(chat_id, user, text, keyboard)

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
        user.full_name = name

        await self.update_user_state(
            user, BotState.StateType.PROFILE_SETUP, {"step": "phone"}
        )

        keyboard = self.create_keyboard(
            [[{"text": "📱 ارسال شماره تلفن", "callback_data": "request_phone"}]]
        )

        await self.send_profile_prompt(
            message.chat.id,
            user,
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
        user.phone_number = phone
        await message.reply(
            "✅ شماره تلفن ثبت شد.", reply_markup=types.ReplyKeyboardRemove()
        )
        await self.show_device_setup(message.chat.id, user)

    async def show_device_setup(self, chat_id: int, user: User):
        """Collect the required device family during initial profile setup."""
        await self.update_user_state(
            user, BotState.StateType.PROFILE_SETUP, {"step": "device"}
        )
        choices = [
            (UserProfile.DeviceType.IPHONE, "آیفون"),
            (UserProfile.DeviceType.ANDROID_SAMSUNG, "اندروید _ سامسونگ"),
            (UserProfile.DeviceType.ANDROID_OTHER, "اندروید _ شیائومی و سایر"),
            (UserProfile.DeviceType.WINDOWS, "ویندوز"),
            (UserProfile.DeviceType.MACOS, "مکینتاش"),
            (UserProfile.DeviceType.LINUX, "لینوکس"),
        ]
        keyboard = self.create_keyboard(
            [
                [{"text": label, "callback_data": f"setup_device_{value}"}]
                for value, label in choices
            ]
        )
        await self.send_profile_prompt(
            chat_id,
            user,
            "📱 <b>دستگاه‌ها</b>\n\nدستگاه اصلی خود را انتخاب کنید:",
            keyboard,
        )

    async def set_initial_device(self, chat_id: int, user: User, device_type: str):
        valid = {value for value, _ in UserProfile.DeviceType.choices}
        if device_type not in valid:
            await self.send_message_with_keyboard(
                chat_id,
                "❌ نوع دستگاه معتبر نیست.",
                self.get_back_keyboard("setup_profile"),
            )
            return
        profile, _ = await UserProfile.objects.aget_or_create(user=user)
        profile.device_type = device_type
        await profile.asave(update_fields=["device_type", "updated_at"])
        await self.clear_previous_keyboard(chat_id, user)
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        await self.bot.send_message(
            chat_id,
            "✅ پروفایل شما با موفقیت تکمیل شد.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        await self.show_main_menu(chat_id, user)

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Normalize phone number format"""
        return normalize_iranian_phone(phone)

    @staticmethod
    def _is_valid_iranian_phone(phone: str) -> bool:
        """Validate Iranian mobile phone number"""
        return is_valid_iranian_phone(phone)

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
