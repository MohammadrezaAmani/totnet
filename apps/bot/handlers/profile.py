"""
Profile Handler for Multi-Tenant VPN Bot
Handles user profile management and statistics
"""

import logging
import re
from datetime import date
from html import escape

import jdatetime
from asgiref.sync import sync_to_async

from aiogram import types
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.bot.models import BotState
from apps.subscriptions.models import Subscription, SubscriptionClaim
from apps.referrals.selectors import reward_summary, pill_progress

from .base import BaseHandler
from utils.phone import normalize_iranian_phone, is_valid_iranian_phone

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
        selected_devices = profile.devices or (
            [profile.device_type] if profile.device_type else []
        )
        device_label = (
            "، ".join(
                self.DEVICE_LABELS[d]
                for d in selected_devices
                if d in self.DEVICE_LABELS
            )
            or "ثبت نشده"
        )
        pills = await pill_progress(user_id=user.pk, brand_id=self.brand.pk)

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

        birth_label = (
            jdatetime.date.fromgregorian(date=user.birth_date).strftime("%Y/%m/%d")
            if user.birth_date
            else "ثبت نشده"
        )
        text = f"""
👤 پروفایل من

👨‍💼 نام: {escape(user.full_name or user.first_name or "ثبت نشده")}
📱 تلفن: {user.phone_number or "ثبت نشده"}
👤 نام کاربری: {escape(display_username)}
📅 تاریخ پذیرش در مطب: {user.created_at.strftime("%Y/%m/%d") if user.created_at else "نامشخص"}

🎂 تاریخ تولد: {birth_label}
📍 محل کار: {escape(profile.work_location) or "ثبت نشده"}
🏠 محل زندگی: {escape(profile.living_location) or "ثبت نشده"}
📱 دستگاه‌ها: {device_label}

📊 وضعیت:
• اشتراک‌های فعال: {subscription_count}
• درخواست ثبت اشتراک در انتظار بررسی: {pending_claim_count}
• سطح کاربری: {escape(rewards["level_title"])}
• کسب {pills["remaining"]} قرص تا نقد شدن کامل امتیاز
• موجودی نقد کیف پول: {self.format_price(wallet_balance, self.brand.currency)}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "✏️ ویرایش و تکمیل پروفایل", "callback_data": "edit_profile"}],
                [
                    {"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"},
                    {"text": "👥 معرفی دوستان", "callback_data": "referral_system"},
                ],
                [{"text": "🎯 نحوه کسب امتیاز", "callback_data": "how_to_earn"}],
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
                [
                    {
                        "text": "🎁 تکمیل پروفایل و دریافت امتیاز",
                        "callback_data": "complete_profile",
                    }
                ],
                [{"text": "🎂 تاریخ تولد", "callback_data": "edit_birth_date"}],
                [{"text": "👨‍💼 نام کامل", "callback_data": "edit_full_name"}],
                [{"text": "📱 شماره تلفن", "callback_data": "edit_phone"}],
                [{"text": "📍 محل کار و زندگی", "callback_data": "edit_locations"}],
                [{"text": "📧 ایمیل", "callback_data": "edit_email"}],
                [{"text": "📱 دستگاه‌ها", "callback_data": "edit_device"}],
                [{"text": "🔙 بازگشت", "callback_data": "my_profile"}],
            ]
        )

        await self._render(callback, text, keyboard)
        await callback.answer()

    async def show_device_options(self, callback):
        user, _ = await self.get_or_create_user(callback.from_user)
        profile, _ = await UserProfile.objects.aget_or_create(user=user)
        selected = profile.devices or (
            [profile.device_type] if profile.device_type else []
        )
        rows = [
            [
                {
                    "text": ("✅ " if value in selected else "") + label,
                    "callback_data": f"set_device_{value}",
                }
            ]
            for value, label in self.DEVICE_LABELS.items()
        ]
        rows.append([{"text": "🔙 بازگشت", "callback_data": "edit_profile"}])
        await self._render(
            callback,
            "📱 <b>دستگاه‌ها</b>\n\nمی‌توانید چند دستگاه انتخاب کنید؛ با انتخاب مجدد، دستگاه حذف می‌شود.",
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def set_device_type(self, callback, device_type):
        if device_type not in self.DEVICE_LABELS:
            await callback.answer("دستگاه نامعتبر است.", show_alert=True)
            return
        user, _ = await self.get_or_create_user(callback.from_user)
        await self._toggle_device(user.pk, device_type)
        await self.show_device_options(callback)

    @sync_to_async
    def _toggle_device(self, user_id, device_type):
        from django.db import transaction

        with transaction.atomic():
            profile, _ = UserProfile.objects.select_for_update().get_or_create(
                user_id=user_id
            )
            selected = profile.devices or (
                [profile.device_type] if profile.device_type else []
            )
            profile.devices = (
                [d for d in selected if d != device_type]
                if device_type in selected
                else selected + [device_type]
            )
            profile.device_type = profile.devices[0] if profile.devices else None
            profile.save(update_fields=["devices", "device_type", "updated_at"])

    FIELD_PROMPTS = {
        "full_name": "👨‍💼 نام کامل خود را ارسال کنید:",
        "birth_date": "🎂 تاریخ تولد\nبرای به یاد شما بودن در این روز زیبا\n\nتاریخ تولد را به شکل سال/ماه/روز وارد کنید؛ مثال شمسی: ۱۳۷۵/۰۱/۱۵ یا میلادی: 1996/04/03",
        "phone": "📱 شماره تلفن\nجهت دسترسی داشتن به شما در صورت قطعی اینترنت و اطلاع‌رسانی\n\nشماره تلفن همراه خود را ارسال کنید:",
        "locations": "📍 محل کار و زندگی\nباخبر شدن از وضعیت اینترنت لوکیشن شما در صورت تجمیع گزارشات کاربران\n\nشهر/محله محل کار و محل زندگی را در دو خط ارسال کنید. اگر یکسان است، یک خط کافی است.",
        "email": "📧 ایمیل خود را ارسال کنید:",
    }
    COMPLETION_FIELDS = ("birth_date", "phone", "locations")

    async def begin_completion(self, callback):
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(user, BotState.StateType.PROFILE_EDIT)
        await self.update_user_state(
            user,
            BotState.StateType.PROFILE_EDIT,
            {"completion_flow": True, "field": "birth_date", "step": "waiting_input"},
        )
        await self._render(
            callback,
            self.FIELD_PROMPTS["birth_date"],
            self.get_back_keyboard("edit_profile"),
        )
        await self.remember_profile_prompt(user, callback.message.message_id)
        await callback.answer()

    async def request_field_update(self, callback, field):
        if field not in self.FIELD_PROMPTS:
            await callback.answer("فیلد نامعتبر است.")
            return
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.clear_previous_keyboard(callback.message.chat.id, user)
        await self.update_user_state(user, BotState.StateType.PROFILE_EDIT)
        await self.update_user_state(
            user,
            BotState.StateType.PROFILE_EDIT,
            {"field": field, "step": "waiting_input"},
        )
        await self._render(
            callback, self.FIELD_PROMPTS[field], self.get_back_keyboard("edit_profile")
        )
        await self.remember_profile_prompt(user, callback.message.message_id)
        await callback.answer()

    async def handle_profile_contact(self, message, user, state):
        if (
            state.state_data.get("field") != "phone"
            or message.contact.user_id != message.from_user.id
        ):
            await message.reply("لطفاً شماره تلفن خودتان را ارسال کنید.")
            return
        await self._save_field(message, user, state, message.contact.phone_number)

    async def handle_profile_field_message(self, message, user, state):
        await self._save_field(message, user, state, (message.text or "").strip())

    async def _save_field(self, message, user, state, value):
        if state.state_data.get("step") != "waiting_input":
            await message.reply("بخش موردنظر را از منوی ویرایش پروفایل انتخاب کنید.")
            return
        field = state.state_data.get("field")
        error = None
        profile = None
        if field == "full_name":
            if not 2 <= len(value) <= 255:
                error = "نام باید بین ۲ تا ۲۵۵ کاراکتر باشد."
            else:
                user.full_name = value
        elif field == "birth_date":
            try:
                normalized = value.translate(
                    str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
                )
                year, month, day = map(int, re.split(r"[/\-.]", normalized))
                born = (
                    jdatetime.date(year, month, day).togregorian()
                    if year < 1700
                    else date(year, month, day)
                )
                if born > timezone.localdate() or born.year < 1900:
                    raise ValueError()
                user.birth_date = born
            except ValueError, OverflowError:
                error = "تاریخ تولد معتبر نیست؛ آن را به شکل سال/ماه/روز وارد کنید."
        elif field == "phone":
            phone = normalize_iranian_phone(value)
            if not is_valid_iranian_phone(phone):
                error = "شماره تلفن معتبر نیست."
            else:
                user.phone_number = phone
        elif field == "locations":
            parts = [part.strip() for part in value.splitlines() if part.strip()]
            if not 1 <= len(parts) <= 2 or any(
                not 2 <= len(part) <= 255 for part in parts
            ):
                error = "محل کار و محل زندگی را در یک یا دو خط، هرکدام بین ۲ تا ۲۵۵ کاراکتر بنویسید."
            else:
                profile, _ = await UserProfile.objects.aget_or_create(user=user)
                profile.work_location = parts[0]
                profile.living_location = parts[-1]
        elif field == "email":
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError

            try:
                validate_email(value)
                if len(value) > 254:
                    raise ValidationError("long")
                user.email = value
            except ValidationError:
                error = "ایمیل معتبر نیست."
        else:
            error = "ابتدا فیلد موردنظر را از منو انتخاب کنید."
        if error:
            await self.send_profile_prompt(
                message.chat.id,
                user,
                f"❌ {error}",
                self.get_back_keyboard("edit_profile"),
            )
            return
        if profile:
            await profile.asave(
                update_fields=["work_location", "living_location", "updated_at"]
            )
        else:
            field_name = {"phone": "phone_number"}.get(field, field)
            await user.asave(update_fields=[field_name, "updated_at"])
        if (
            state.state_data.get("completion_flow")
            and field in self.COMPLETION_FIELDS[:-1]
        ):
            next_field = self.COMPLETION_FIELDS[self.COMPLETION_FIELDS.index(field) + 1]
            await self.update_user_state(
                user,
                BotState.StateType.PROFILE_EDIT,
                {"field": next_field, "step": "waiting_input"},
            )
            await self.send_profile_prompt(
                message.chat.id,
                user,
                self.FIELD_PROMPTS[next_field],
                self.get_back_keyboard("edit_profile"),
            )
            return
        from apps.referrals.services import grant_profile_completion_reward

        granted = await sync_to_async(grant_profile_completion_reward)(
            user_id=user.pk, brand_id=self.brand.pk
        )
        await self.clear_previous_keyboard(message.chat.id, user)
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        text = "✅ پروفایل شما ذخیره شد." + (
            "\n🎁 یک امتیاز تکمیل پروفایل به جعبه قرص‌های شما اضافه شد."
            if granted
            else ""
        )
        await self.send_message_with_keyboard(
            message.chat.id,
            text,
            self.create_keyboard(
                [
                    [{"text": "👤 پروفایل من", "callback_data": "my_profile"}],
                    [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
                ]
            ),
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
