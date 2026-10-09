"""
Rewards Handler for Multi-Tenant VPN Bot
Handles loyalty program, achievements, and levels
"""

import logging
import uuid

from aiogram import types
from asgiref.sync import sync_to_async
from django.db.models import Sum

from apps.referrals.models import (
    RewardAccount,
    RewardPointLedger,
)
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
        """Show the medical grade and the current three-point cash box."""
        user, _ = await self.get_or_create_user(callback.from_user)
        progress_data = await referral_level_progress(
            user_id=user.pk, brand_id=self.brand.pk
        )
        current = progress_data["current_level"]
        level_name = f"{current.badge} {current.name}" if current else "در حال تعیین"

        from apps.referrals.models import ReferralReward

        cashed_out = await ReferralReward.objects.filter(
            user=user,
            brand=self.brand,
            status=ReferralReward.RewardStatus.PROCESSED,
            is_cashed_out=True,
        ).aaggregate(total=Sum("amount"))
        spent_ledger = await RewardPointLedger.objects.filter(
            account__user=user,
            account__brand=self.brand,
            points_delta__lt=0,
            entry_type__in=[
                RewardPointLedger.EntryType.REDEEMED,
                RewardPointLedger.EntryType.CONVERTED_TO_WALLET,
            ],
        ).aaggregate(total=Sum("points_delta"))
        spent_points = (cashed_out["total"] or 0) - (spent_ledger["total"] or 0)

        open_box = await ReferralReward.objects.filter(
            user=user,
            brand=self.brand,
            reward_type__in=["normal_point", "profile_point"],
            status=ReferralReward.RewardStatus.PROCESSED,
            is_cashed_out=False,
        ).aaggregate(total=Sum("amount"))
        box_points = open_box["total"] or 0
        completed_normal_points = await ReferralReward.objects.filter(
            user=user,
            brand=self.brand,
            reward_type__in=["normal_point", "profile_point"],
            status=ReferralReward.RewardStatus.PROCESSED,
            is_cashed_out=True,
        ).aaggregate(total=Sum("amount"))

        text = f"""
🏅 <b>درجه پزشکی و امتیازات</b>

🩺 <b>درجه پزشکی:</b> {level_name}
با فعالیت بیشتر در این سیستم اعتبار و جایگاه شما در این جامعه پزشکی ارتقا میابد.

🧾 <b>تمام امتیازات خرج شده توسط شما تا الان:</b> {spent_points:g}

📦 <b>جعبه قرص‌ها</b>
• قرص‌های موجود در جعبه فعلی: {box_points:g}
• قرص‌های نقدشده: {int(completed_normal_points["total"] or 0)}

جمع ارزش هر ۳ قرص به موجودی نقد کیف پول منتقل می‌شود.
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🎯 نحوه کسب امتیاز و رایگان‌شدن خدمات",
                        "callback_data": "how_to_earn",
                    }
                ],
                [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )
        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as exc:
            logger.warning("Could not edit rewards message: %s", exc)
            await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
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
                "lifetime_points": "تمام امتیازات کسب‌شده",
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
        text = """💊شما جزوی از کادر پزشکی هستید و سهم بزرگی در این جامعه دارید
💊با هر بار خرید کاربری که توسط شما معرفی شده، بخشی از مبلغ خرید به شما تعلق می‌گیرد
💊محاسبه و دریافت این امتیازات تا ابد پابرجاست تا به این واسطه خیلی زود هزینه اینترنت شما صفر شود
💊هر خرید دوستان شما معادل یک قرص است
💊هر قرص به تنهایی قابل نقد شدن نیست و باید تعدادش مضربی از ۳ باشد
💊درواقع جمع ارزش هر ۳ قرص به موجودی نقد شما منتقل می‌شود
💊ارزش هر قرص متفاوت است و بستگی به خرید ثبت شده توسط دوست شما دارد

💉سهم DR.VPN در صفر کردن مخارج اینترنت شما طراحی این سیستم بوده
فعالیت بیشتر شما برای خودتون و اطرافیانتون مفید خواهد بود"""
        keyboard = self.create_keyboard(
            [
                [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                [{"text": "🏆 چالش‌های فعال", "callback_data": "active_challenges"}],
                [{"text": "🔙 بازگشت", "callback_data": "rewards"}],
            ]
        )
        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as exc:
            logger.warning("Could not edit reward help message: %s", exc)
            await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
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
📊 تمام امتیازات کسب‌شده: {user_points:g}

💡 رتبه بر اساس مجموع امتیازات ثبت‌شده محاسبه می‌شود.
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
