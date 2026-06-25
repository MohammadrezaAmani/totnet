"""
Rewards Handler for Multi-Tenant VPN Bot
Handles loyalty program, achievements, and levels
"""

import logging

from aiogram import types

from apps.referrals.models import Referral

from .base import BaseHandler

logger = logging.getLogger(__name__)


class RewardsHandler(BaseHandler):
    """Handle rewards, achievements, and loyalty programs"""

    LEVEL_THRESHOLDS = {
        1: {"referrals": 0, "points": 0, "title": "🌱 تازه‌کار", "badge": "👶"},
        2: {"referrals": 5, "points": 100, "title": "🌿 نوآموز", "badge": "🌱"},
        3: {"referrals": 10, "points": 300, "title": "🌾 معرفی‌کننده", "badge": "🌾"},
        4: {"referrals": 25, "points": 750, "title": "🎯 سفیر", "badge": "⭐"},
        5: {"referrals": 50, "points": 1500, "title": "👑 پادشاه", "badge": "👑"},
        6: {"referrals": 100, "points": 3000, "title": "💎 الماسی", "badge": "💎"},
    }

    async def show_rewards(self, callback: types.CallbackQuery):
        """Show rewards and achievements"""
        user, _ = await self.get_or_create_user(callback.from_user)

        referral_count = await Referral.objects.filter(
            referrer=user, brand=self.brand, status="completed"
        ).acount()

        current_level = self._get_user_level(referral_count, user.reward_points)
        next_level = current_level + 1

        current_threshold = self.LEVEL_THRESHOLDS.get(current_level, {})
        next_threshold = self.LEVEL_THRESHOLDS.get(next_level, {})

        progress = 0
        if next_level <= 6:
            next_referrals = next_threshold.get("referrals", 0)
            current_referrals = current_threshold.get("referrals", 0)
            if next_referrals > current_referrals:
                progress = int(
                    (
                        (referral_count - current_referrals)
                        / (next_referrals - current_referrals)
                    )
                    * 100
                )
                progress = min(progress, 100)

        progress_bar = self._create_progress_bar(progress)

        text = f"""
🎁 جایزه‌ها و امتیازات

👤 <b>سطح شما:</b>
{current_threshold.get("badge", "🌱")} {current_threshold.get("title", "نامشخص")}

📊 <b>آمار شما:</b>
• امتیازات: {user.reward_points} نقطه
• معرفی‌های تکمیل‌شده: {referral_count}
• سطح فعلی: {current_level}/6

🎯 <b>سطح بعدی:</b>
{next_threshold.get("badge", "??")} {next_threshold.get("title", "نامشخص")}
• نیاز: {next_threshold.get("referrals", 0)} معرفی
• امتیازات: {next_threshold.get("points", 0)} نقطه

{progress_bar} {progress}%

💡 <b>نکات:</b>
• هر معرفی: ۵۰ نقطه
• معرفی اول: ۱۰۰ نقطه
• استفاده اشتراک دوست: ۲۵ نقطه

🎁 <b>جوایز سطح‌ها:</b>
1. تازه‌کار 👶: شروع سفر
2. نوآموز 🌱: ۱۰% تخفیف
3. معرفی‌کننده 🌾: ۲۰% تخفیف + اولویت پشتیبانی
4. سفیر ⭐: ۳۰% تخفیف + مشاوره رایگان
5. پادشاه 👑: ۴۰% تخفیف + اعتبار ماهانه
6. الماسی 💎: ۵۰% تخفیف + VIP پشتیبانی
        """

        keyboard = self.create_keyboard(
            [
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

    async def show_how_to_earn(self, callback: types.CallbackQuery):
        """Show how to earn rewards"""
        text = """
🎯 نحوه کسب امتیازات و جوایز

📌 <b>راه‌های کسب امتیاز:</b>

1️⃣ <b>معرفی دوستان:</b>
   • هر معرفی: ۵۰ نقطه
   • معرفی اول: ۱۰۰ نقطه (۲ برابر!)
   • معرفی کسی که اشتراک بخره: ۲۰۰ نقطه

2️⃣ <b>استفاده از اشتراک:</b>
   • اشتراک ۱ ماهه: ۲۵ نقطه
   • اشتراک ۳ ماهه: ۱۰۰ نقطه
   • اشتراک ۱ سال: ۵۰۰ نقطه

3️⃣ <b>فعالیت‌های ویژه:</b>
   • ترک نظر: ۱۰ نقطه
   • تکمیل پروفایل: ۲۵ نقطه
   • تصدیق شماره موبایل: ۵۰ نقطه

🏆 <b>سطح‌های پاداش:</b>

سطح ۱ 👶 - تازه‌کار
└─ 0 معرفی | هدایای رایگان

سطح ۲ 🌱 - نوآموز
└─ 5 معرفی | 10% تخفیف

سطح ۳ 🌾 - معرفی‌کننده
└─ 10 معرفی | 20% تخفیف + اولویت پشتیبانی

سطح ۴ ⭐ - سفیر
└─ 25 معرفی | 30% تخفیف + مشاوره رایگان

سطح ۵ 👑 - پادشاه
└─ 50 معرفی | 40% تخفیف + اعتبار ماهانه

سطح ۶ 💎 - الماسی
└─ 100 معرفی | 50% تخفیف + VIP پشتیبانی

💡 <b>نکات مهم:</b>
• امتیازات هرگز حذف نمی‌شوند
• سطح‌ها بر اساس امتیاز محاسبه می‌شوند
• شما می‌توانید از جوایز خود استفاده کنید
        """

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
        from apps.accounts.models import User as UserModel

        try:
            top_users = await UserModel.objects.filter(brand=self.brand).order_by(
                "-reward_points"
            )[:10]
        except Exception as e:
            logger.error(f"Error fetching leaderboard: {e}")
            await callback.answer("❌ خطا در بارگذاری جدول")
            return

        user, _ = await self.get_or_create_user(callback.from_user)

        text = """
🏆 جدول امتیازات

<b>۱۰ نفر برتر:</b>
"""

        for idx, top_user in enumerate(top_users, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"{idx}️⃣")
            is_you = " (شما)" if top_user.id == user.id else ""
            text += f"\n{medal} {top_user.username}{is_you}\n"
            text += f"   📊 {top_user.reward_points} نقطه\n"

        user_position = await UserModel.objects.filter(
            brand=self.brand, reward_points__gt=user.reward_points
        ).acount()
        user_position += 1

        text += f"""

👤 <b>شما:</b>
📍 رتبه: {user_position}
📊 امتیازات: {user.reward_points}

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

    def _get_user_level(self, referral_count: int, reward_points: int) -> int:
        """Determine user level based on referrals and points"""
        for level in range(6, 0, -1):
            threshold = self.LEVEL_THRESHOLDS[level]
            if (
                referral_count >= threshold["referrals"]
                and reward_points >= threshold["points"]
            ):
                return level
        return 1

    def _create_progress_bar(self, percentage: int, length: int = 10) -> str:
        """Create a visual progress bar"""
        filled = int(length * percentage / 100)
        empty = length - filled
        bar = "█" * filled + "░" * empty
        return f"[{bar}]"
