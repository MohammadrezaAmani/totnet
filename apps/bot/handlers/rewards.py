"""
Rewards Handler for Multi-Tenant VPN Bot
Handles loyalty program, achievements, and levels
"""

import logging
import uuid

from aiogram import types
from asgiref.sync import sync_to_async

from apps.referrals.models import Referral, ReferralProgram, RewardAccount
from apps.referrals.services import RewardRedemptionError, redeem_reward_service

from .base import BaseHandler

logger = logging.getLogger(__name__)


class RewardsHandler(BaseHandler):
    """Handle rewards, achievements, and loyalty programs"""

    async def show_rewards(self, callback: types.CallbackQuery):
        """Show rewards and achievements"""
        user, _ = await self.get_or_create_user(callback.from_user)
        account = await RewardAccount.objects.filter(
            user=user, brand=self.brand
        ).select_related("reference_service__plan").afirst()
        program = await ReferralProgram.objects.filter(
            brand=self.brand, is_active=True
        ).select_related("reference_service__plan").afirst()
        lifetime_points = account.lifetime_points if account else 0
        liquid_points = account.liquid_points if account else 0
        referrals = await Referral.objects.filter(
            referrer=user, brand=self.brand
        ).acount()
        levels = []
        if program:
            async for level in program.levels.order_by("min_lifetime_points", "level"):
                levels.append(level)
        current = None
        next_level = None
        for level in levels:
            if level.min_lifetime_points <= lifetime_points:
                current = level
            elif next_level is None:
                next_level = level
        service = program.reference_service if program else None
        point_value = service.point_value if service else None
        open_box = None
        if account and service:
            open_box = await account.boxes.filter(
                service=service, state="open"
            ).order_by("cycle", "sequence").afirst()
        progress = 0
        if open_box and open_box.capacity:
            progress = min(100, int(open_box.filled * 100 / open_box.capacity))
        filled_display = f"{open_box.filled:g}/{open_box.capacity:g}" if open_box else "—"
        remaining_box = open_box.capacity - open_box.filled if open_box else 0
        level_name = f"{current.badge} {current.name}" if current else "—"
        next_text = (
            f"{next_level.badge} {next_level.name}: {next_level.min_lifetime_points:g}"
            if next_level else "بالاترین سطح"
        )
        point_value_text = f"{point_value:g} {self.brand.currency}" if point_value else "تنظیم نشده"
        free_points = service.free_points if service else 0
        free_progress = (
            min(100, int(liquid_points * 100 / free_points)) if free_points else 0
        )
        text = f"""
🎁 جایزه‌ها و امتیازات

👤 <b>سطح شما:</b> {level_name}
📊 امتیاز مادام‌العمر: {lifetime_points:g}
💰 امتیازهای کامل و قابل استفاده: {liquid_points:g}
👥 معرفی‌ها: {referrals}

📦 <b>جعبه امتیاز جاری:</b> {filled_display} (باقی‌مانده {remaining_box:g}، {progress}%)
🎯 <b>سطح بعدی:</b> {next_text}
🔁 <b>خدمت مرجع:</b> {service.plan.name if service else 'تنظیم نشده'}
💵 ارزش هر امتیاز مرجع: {point_value_text}
🎁 پیشرفت خدمت رایگان: {liquid_points:g}/{free_points:g} امتیاز قابل استفاده ({free_progress}%)
        """

        keyboard = self.create_keyboard(
            ([
                [{"text": "🎁 دریافت اشتراک رایگان", "callback_data": f"claim_reward:{account.redemption_nonce.hex}"}],
            ] if account and service and liquid_points >= free_points > 0 else []) + [
                [{"text": "📈 جزئیات معرفی‌های من", "callback_data": "referral_stats"}],
                [{"text": "🏆 جدول امتیازات", "callback_data": "leaderboard"}],
                [{"text": "🎯 نحوه کسب امتیاز", "callback_data": "how_to_earn"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit rewards message: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def redeem_reward(self, callback: types.CallbackQuery, request_key: str):
        """Redeem completed points once and start provisioning the reward order."""
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            key = uuid.UUID(hex=request_key)
            await sync_to_async(redeem_reward_service)(
                user_id=user.pk, brand_id=self.brand.pk, request_key=key
            )
        except (ValueError, RewardRedemptionError) as exc:
            await callback.answer(str(exc) if isinstance(exc, RewardRedemptionError) else "درخواست نامعتبر است.", show_alert=True)
            return
        except Exception as exc:
            logger.error("Reward redemption failed (%s)", type(exc).__name__)
            await callback.answer("❌ دریافت جایزه انجام نشد.", show_alert=True)
            return

        await callback.answer("✅ امتیازها ثبت شد؛ اشتراک در حال فعال‌سازی است.")
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            "✅ جایزه ثبت شد و اشتراک در صف فعال‌سازی قرار گرفت. وضعیت را از بخش «اشتراک‌های من» ببینید.",
            self.create_keyboard([[{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}], [{"text": "🎁 امتیازها", "callback_data": "rewards"}]]),
        )

    async def show_how_to_earn(self, callback: types.CallbackQuery):
        """Show how to earn rewards"""
        program = await ReferralProgram.objects.filter(
            brand=self.brand, is_active=True
        ).afirst()
        levels = []
        if program:
            async for level in program.levels.order_by("level"):
                levels.append(level)
        level_text = "\n".join(
            f"{level.badge} {level.name} — {level.min_lifetime_points:g} امتیاز مادام‌العمر"
            for level in levels
        ) or "سطحی برای این برند تنظیم نشده است."
        service = program.reference_service if program else None
        if service:
            explanation = (
                f"هر خرید سودآورِ کاربر معرفی‌شده، یک‌بار و فقط برای معرف مستقیم امتیاز ایجاد می‌کند. "
                f"امتیاز بر اساس سود خرید و ارزش مرجع «{service.plan.name}» محاسبه می‌شود. "
                "امتیازها در جعبه‌های قابل تنظیم جمع می‌شوند؛ با تکمیل جعبه به موجودی قابل استفاده می‌روند."
            )
        else:
            explanation = "روش امتیازدهی برای این برند هنوز تنظیم نشده است."
        text = f"🎯 نحوه کسب امتیاز\n\n{explanation}\n\n🏆 سطح‌های پاداش:\n{level_text}"

        keyboard = self.create_keyboard(
            [
                [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                [{"text": "🎁 جایزه‌های من", "callback_data": "rewards"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit message: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_leaderboard(self, callback: types.CallbackQuery):
        """Show top users leaderboard"""
        user, _ = await self.get_or_create_user(callback.from_user)
        account = await RewardAccount.objects.filter(user=user, brand=self.brand).afirst()
        user_points = account.lifetime_points if account else 0
        top_accounts = []
        async for item in RewardAccount.objects.filter(brand=self.brand).select_related("user").order_by("-lifetime_points")[:10]:
            top_accounts.append(item)

        text = """
🏆 جدول امتیازات

<b>۱۰ نفر برتر:</b>
"""

        for idx, top_account in enumerate(top_accounts, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"{idx}️⃣")
            is_you = " (شما)" if top_account.user_id == user.id else ""
            text += f"\n{medal} {top_account.user.username}{is_you}\n"
            text += f"   📊 {top_account.lifetime_points:g} امتیاز\n"

        user_position = await RewardAccount.objects.filter(
            brand=self.brand, lifetime_points__gt=user_points
        ).acount()
        user_position += 1

        text += f"""

👤 <b>شما:</b>
📍 رتبه: {user_position}
📊 امتیاز مادام‌العمر: {user_points:g}

💡 نکته: جدول هر ساعت به‌روز می‌شود
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🎯 نحوه کسب امتیاز", "callback_data": "how_to_earn"}],
                [{"text": "🔙 بازگشت", "callback_data": "rewards"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit message: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    def _create_progress_bar(self, percentage: int, length: int = 10) -> str:
        """Create a visual progress bar"""
        filled = int(length * percentage / 100)
        empty = length - filled
        bar = "█" * filled + "░" * empty
        return f"[{bar}]"
