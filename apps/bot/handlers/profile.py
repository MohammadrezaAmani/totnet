"""
Profile Handler for Multi-Tenant VPN Bot
Handles user profile management and statistics
"""

import logging
import re

from aiogram import types
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User, UserProfile
from apps.bot.models import BotState
from apps.subscriptions.models import Subscription, SubscriptionClaim
from apps.referrals.selectors import reward_summary

from .base import BaseHandler

logger = logging.getLogger(__name__)


class ProfileHandler(BaseHandler):
    """Handle user profile operations"""

    DEVICE_LABELS = {
        UserProfile.DeviceType.IPHONE: "آیفون",
        UserProfile.DeviceType.ANDROID_SAMSUNG: "اندروید _ سامسونگ",
        UserProfile.DeviceType.ANDROID_OTHER: "اندروید _ شیائومی و سایر",
        UserProfile.DeviceType.WINDOWS: "ویندوز",
        UserProfile.DeviceType.MACOS: "مکینتاش",
        UserProfile.DeviceType.LINUX: "لینوکس",
    }

    async def show_my_profile(self, callback: types.CallbackQuery):
        user, _ = await self.get_or_create_user(callback.from_user)

        now = timezone.now()
        subscription_count = await (
            Subscription.objects.filter(
                (Q(user=user) | Q(owner=user)),
                brand=self.brand,
                status=Subscription.SubscriptionStatus.ACTIVE,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .acount()
        )
        pending_claim_count = await SubscriptionClaim.objects.filter(
            user=user, brand=self.brand, status=SubscriptionClaim.Status.PENDING
        ).acount()
        rewards = await reward_summary(user_id=user.pk, brand_id=self.brand.pk)
        profile, _ = await UserProfile.objects.aget_or_create(user=user)
        device_label = self.DEVICE_LABELS.get(profile.device_type, "ثبت نشده")

        from apps.orders.models import Wallet

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
            wallet_balance = wallet.balance
        except Wallet.DoesNotExist:
            wallet_balance = 0

        display_username = (
            f"@{user.username}"
            if user.username and user.username != f"user_{user.telegram_id}"
            else "ثبت نشده"
        )

        text = f"""
👤 پروفایل من

👨‍💼 نام: {user.full_name or user.first_name or "ثبت نشده"}
📱 تلفن: {user.phone_number or "ثبت نشده"}
👤 نام کاربری: {display_username}
📅 تاریخ عضویت: {user.created_at.strftime("%Y/%m/%d") if user.created_at else "نامشخص"}

📱 نوع دستگاه: {device_label}

📊 وضعیت:
• اشتراک‌های فعال: {subscription_count}
• درخواست ثبت اشتراک در انتظار بررسی: {pending_claim_count}
• سطح کاربری: {rewards['level_title']}
• موجودی نقد کیف پول: {self.format_price(wallet_balance, self.brand.currency)}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "✏️ ویرایش و تکمیل پروفایل", "callback_data": "edit_profile"}],
                [
                    {"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"},
                    {"text": "👥 معرفی دوستان", "callback_data": "referral_system"},
                ],
                [{"text": "💰 کیف پول", "callback_data": "wallet"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        await self._render(callback, text, keyboard)
        await callback.answer()

    async def edit_profile(self, callback: types.CallbackQuery):
        user, _ = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user, BotState.StateType.PROFILE_EDIT, {"step": "menu"}
        )

        text = """
✏️ ویرایش و تکمیل پروفایل

کدام بخش را می‌خواهید تغییر دهید یا تکمیل کنید؟
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "👨‍💼 نام کامل", "callback_data": "edit_full_name"}],
                [{"text": "📱 شماره تلفن", "callback_data": "edit_phone"}],
                [{"text": "📧 ایمیل", "callback_data": "edit_email"}],
                [{"text": "📱 نوع دستگاه", "callback_data": "edit_device"}],
                [{"text": "🔙 بازگشت", "callback_data": "my_profile"}],
            ]
        )

        await self._render(callback, text, keyboard)
        await callback.answer()


    async def show_device_options(self, callback: types.CallbackQuery):
        """Let the user set the primary device used for the service."""
        user, _ = await self.get_or_create_user(callback.from_user)
        profile, _ = await UserProfile.objects.aget_or_create(user=user)

        labels = self.DEVICE_LABELS
        current = labels.get(profile.device_type, "ثبت نشده")
        rows = [
            [
                {
                    "text": f"{'✅ ' if profile.device_type == value else ''}{label}",
                    "callback_data": f"set_device_{value}",
                }
            ]
            for value, label in labels.items()
        ]
        rows.append([{"text": "🔙 بازگشت", "callback_data": "edit_profile"}])
        text = f"📱 <b>نوع دستگاه</b>\n\nدستگاه فعلی: {current}\n\nنوع دستگاه اصلی خود را انتخاب کنید:"
        await self._render(callback, text, self.create_keyboard(rows))
        await callback.answer()

    async def set_device_type(self, callback: types.CallbackQuery, device_type: str):
        """Persist one of the supported profile device choices."""
        valid = {choice for choice, _label in UserProfile.DeviceType.choices}
        if device_type not in valid:
            await callback.answer("❌ نوع دستگاه نامعتبر است.", show_alert=True)
            return

        user, _ = await self.get_or_create_user(callback.from_user)
        profile, _ = await UserProfile.objects.aget_or_create(user=user)
        profile.device_type = device_type
        await profile.asave(update_fields=["device_type", "updated_at"])
        await self.show_device_options(callback)

    async def request_field_update(self, callback: types.CallbackQuery, field: str):
        user, _ = await self.get_or_create_user(callback.from_user)

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
            await self.get_main_menu_keyboard(user),
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
