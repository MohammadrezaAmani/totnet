"""
Rewards Handler for Multi-Tenant VPN Bot
Handles loyalty program, achievements, and levels
"""

import logging
import uuid

from aiogram import types
from asgiref.sync import sync_to_async

from apps.referrals.models import Achievement, ReferralProgram, RewardAccount
from apps.referrals.selectors import referral_level_progress
from apps.referrals.services import (
    AchievementClaimError,
    RewardRedemptionError,
    claim_user_achievement,
    redeem_reward_service,
    refresh_user_achievements,
)

from .base import BaseHandler

logger = logging.getLogger(__name__)


class RewardsHandler(BaseHandler):
    """Handle rewards, achievements, and loyalty programs"""

    async def show_rewards(self, callback: types.CallbackQuery):
        """Show rewards and achievements"""
        user, _ = await self.get_or_create_user(callback.from_user)
        progress_data = await referral_level_progress(
            user_id=user.pk, brand_id=self.brand.pk
        )
        account = progress_data["account"]
        program = progress_data["program"]
        lifetime_points = account.lifetime_points if account else 0
        liquid_points = account.liquid_points if account else 0
        referrals = progress_data["referral_count"]
        current = progress_data["current_level"]
        next_level = progress_data["next_level"]
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
        next_text = "بالاترین سطح"
        if next_level:
            next_text = (
                f"{next_level.badge} {next_level.name}: "
                f"{next_level.min_referrals} معرفی، "
                f"{next_level.min_lifetime_points:g} امتیاز، "
                f"نرخ تبدیل {next_level.min_conversion_rate:g}%"
            )
        point_value_text = f"{point_value:g} {self.brand.currency}" if point_value else "تنظیم نشده"
        free_points = service.free_points if service else 0
        free_progress = (
            min(100, int(liquid_points * 100 / free_points)) if free_points else 0
        )
        achievement_count = await Achievement.objects.filter(
            brand=self.brand, is_active=True
        ).acount()
        text = f"""
🎁 جایزه‌ها و امتیازات

👤 <b>سطح شما:</b> {level_name}
📊 امتیاز مادام‌العمر: {lifetime_points:g}
💰 امتیازهای کامل و قابل استفاده: {liquid_points:g}
👥 معرفی‌ها: {referrals}
🏅 دستاوردهای فعال: {achievement_count}

📦 <b>جعبه امتیاز جاری:</b> {filled_display} (باقی‌مانده {remaining_box:g}، {progress}%)
🎯 <b>سطح بعدی:</b> {next_text}
🔁 <b>خدمت مرجع:</b> {service.plan.name if service else 'تنظیم نشده'}
💵 ارزش هر امتیاز مرجع: {point_value_text}
🎁 پیشرفت خدمت رایگان: {liquid_points:g}/{free_points:g} امتیاز قابل استفاده ({free_progress}%)
        """

        keyboard = self.create_keyboard(
            ([
                [{"text": "🎁 دریافت اشتراک رایگان", "callback_data": f"claim_reward:{account.redemption_nonce.hex}"}],
            ] if account and service and service.is_active and service.plan.is_active and liquid_points >= free_points > 0 else []) + [
                [{"text": "📈 جزئیات معرفی‌های من", "callback_data": "referral_stats"}],
                [{"text": "🏅 دستاوردها", "callback_data": "achievements"}],
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

    async def show_achievements(
        self, callback: types.CallbackQuery, page: int = 1, answer_callback: bool = True
    ):
        """Show configured achievements, real progress, and claimable rewards."""
        user, _ = await self.get_or_create_user(callback.from_user)
        achievements = await sync_to_async(
            refresh_user_achievements, thread_sensitive=True
        )(user_id=user.pk, brand_id=self.brand.pk)
        if not achievements:
            text = "🏅 برای این برند هنوز دستاوردی پیکربندی نشده است."
            rows = [[{"text": "🔙 بازگشت", "callback_data": "rewards"}]]
        else:
            requirement_labels = {
                "referrals": "معرفی",
                "conversions": "خرید دوستان",
                "purchases": "خرید",
                "lifetime_points": "امتیاز مادام‌العمر",
                "total_spent": "مجموع خرید",
                "wallet_deposits": "شارژ کیف پول",
            }
            lines = ["🏅 <b>دستاوردهای شما</b>"]
            rows = []
            per_page = 8
            page = max(1, page)
            total_pages = max(1, (len(achievements) + per_page - 1) // per_page)
            page = min(page, total_pages)
            visible = achievements[(page - 1) * per_page : page * per_page]
            for item in visible:
                achievement = item.achievement
                requirements = achievement.requirements or {}
                target = "، ".join(
                    f"{requirement_labels.get(key, key)}: {value}"
                    for key, value in requirements.items()
                ) or "بدون شرط تعریف‌شده"
                reward = []
                if achievement.reward_points:
                    reward.append(f"{achievement.reward_points} امتیاز")
                if achievement.reward_amount:
                    reward.append(
                        f"{achievement.reward_amount:g} {self.brand.currency} کیف پول"
                    )
                reward_text = " + ".join(reward) or "نشان افتخاری"
                if item.is_completed and not item.reward_claimed and reward:
                    status = "✅ تکمیل شده"
                    rows.append(
                        [{"text": f"🎁 دریافت {achievement.name}", "callback_data": f"claim_achievement_{achievement.pk}"}]
                    )
                elif item.is_completed:
                    status = "✅ دریافت شده" if item.reward_claimed else "✅ تکمیل شده"
                else:
                    status = f"📈 پیشرفت {item.progress:g}%"
                claim_text = f"؛ دفعات دریافت {item.claim_count}" if achievement.is_repeatable and item.claim_count else ""
                lines.append(
                    f"\n<b>{achievement.name}</b> — {status}{claim_text}\n"
                    f"شرط: {target}\nپاداش: {reward_text}"
                )
            text = "\n".join(lines)
            if page > 1:
                rows.append([{"text": "⬅️ قبلی", "callback_data": f"achievements_page_{page - 1}"}])
            if page < total_pages:
                rows.append([{"text": "بعدی ➡️", "callback_data": f"achievements_page_{page + 1}"}])
            rows.append([{"text": "🎁 امتیازها", "callback_data": "rewards"}])
            text = f"{text}\n\nصفحه {page} از {total_pages}"
        keyboard = self.create_keyboard(rows)
        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception:
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )
        if answer_callback:
            await callback.answer()

    async def claim_achievement(self, callback: types.CallbackQuery, achievement_id: int):
        """Claim a completed achievement once, with a database idempotency lock."""
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            await sync_to_async(claim_user_achievement, thread_sensitive=True)(
                user_id=user.pk,
                brand_id=self.brand.pk,
                achievement_id=achievement_id,
            )
        except AchievementClaimError:
            await callback.answer("این پاداش آمادهٔ دریافت نیست یا قبلاً دریافت شده است.", show_alert=True)
            return
        except Exception as exc:
            logger.error("Achievement claim failed (%s)", type(exc).__name__)
            await callback.answer("❌ دریافت پاداش انجام نشد.", show_alert=True)
            return
        await callback.answer("✅ پاداش دستاورد به حسابتان اضافه شد.")
        await self.show_achievements(callback, answer_callback=False)

    async def show_how_to_earn(self, callback: types.CallbackQuery):
        """Show how to earn rewards"""
        program = await ReferralProgram.objects.filter(
            brand=self.brand, is_active=True
        ).select_related("reference_service__plan").afirst()
        levels = []
        if program and program.enable_level_rewards:
            async for level in program.levels.order_by("level"):
                levels.append(level)
        level_text = "\n".join(
            f"{level.badge} {level.name} — {level.min_referrals} معرفی، "
            f"{level.min_lifetime_points:g} امتیاز، تبدیل {level.min_conversion_rate:g}%، "
            f"ضریب {level.reward_multiplier:g}×، پاداش {level.bonus_reward:g}"
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
        async for item in RewardAccount.objects.filter(brand=self.brand).select_related("user").order_by("-lifetime_points", "created_at")[:10]:
            top_accounts.append(item)

        text = """
🏆 جدول امتیازات

<b>۱۰ نفر برتر:</b>
"""

        for top_account in top_accounts:
            is_you = " (شما)" if top_account.user_id == user.id else ""
            rank = await RewardAccount.objects.filter(
                brand=self.brand,
                lifetime_points__gt=top_account.lifetime_points,
            ).acount() + 1
            rank_medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}️⃣")
            text += f"\n{rank_medal} {top_account.user.username}{is_you}\n"
            text += f"   📊 {top_account.lifetime_points:g} امتیاز\n"

        user_position = await RewardAccount.objects.filter(
            brand=self.brand, lifetime_points__gt=user_points
        ).acount()
        user_position += 1

        text += f"""

👤 <b>شما:</b>
📍 رتبه: {user_position}
📊 امتیاز مادام‌العمر: {user_points:g}

💡 رتبه بر اساس امتیاز مادام‌العمر و لحظه‌ای محاسبه می‌شود.
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
