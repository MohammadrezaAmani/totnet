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
    ReferralProgram,
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
        """Render the XMind medical-grade / cash-points dashboard."""
        user, _ = await self.get_or_create_user(callback.from_user)
        progress_data = await referral_level_progress(
            user_id=user.pk, brand_id=self.brand.pk
        )
        account = progress_data["account"]
        program = progress_data["program"]
        lifetime_points = account.lifetime_points if account else 0
        liquid_points = account.liquid_points if account else 0
        current = progress_data["current_level"]
        service = program.reference_service if program else None
        point_value = service.point_value if service else None

        open_box = None
        if account and service:
            open_box = (
                await account.boxes.filter(service=service, state="open")
                .order_by("cycle", "sequence")
                .afirst()
            )

        box_progress = 0
        box_filled = "—"
        box_remaining = 0
        if open_box and open_box.capacity:
            box_progress = min(100, int(open_box.filled * 100 / open_box.capacity))
            box_filled = f"{open_box.filled:g} از {open_box.capacity:g} امتیاز کسب شده"
            box_remaining = max(open_box.capacity - open_box.filled, 0)

        earned_value = 0
        spent_value = 0
        if account:
            earned = await account.ledger_entries.filter(
                entry_type__in=[
                    RewardPointLedger.EntryType.EARNED,
                    RewardPointLedger.EntryType.ACHIEVEMENT_BONUS,
                ]
            ).aaggregate(total=Sum("value_delta"))
            earned_value = earned["total"] or 0

            redeemed = await account.ledger_entries.filter(
                entry_type=RewardPointLedger.EntryType.REDEEMED
            ).aaggregate(total=Sum("value_delta"))
            converted = await account.ledger_entries.filter(
                entry_type=RewardPointLedger.EntryType.CONVERTED_TO_WALLET
            ).aaggregate(total=Sum("value_delta"))
            spent_value = abs(redeemed["total"] or 0) + max(converted["total"] or 0, 0)

        current_cash_value = liquid_points * point_value if point_value else 0
        free_points = service.free_points if service else 0
        permanent_progress = (
            min(100, int(lifetime_points * 100 / free_points)) if free_points else 0
        )
        progress_bar = self._create_progress_bar(permanent_progress)
        level_name = f"{current.badge} {current.name}" if current else "در حال تعیین"

        text = f"""
🏅 <b>درجه پزشکی و امتیازات</b>

🩺 <b>درجه پزشکی:</b> {level_name}
سطح اعتباری شما بر اساس قوانین فعال برند محاسبه می‌شود.

💵 <b>جمع تومانی/نقدی امتیازهای دریافتی:</b> {self.format_price(earned_value, self.brand.currency)}
📊 <b>جمع امتیاز مادام‌العمر:</b> {lifetime_points:g}
💰 <b>جمع امتیازات نقد:</b> {liquid_points:g}
💳 <b>ارزش فعلی امتیازات نقد:</b> {self.format_price(current_cash_value, self.brand.currency)}
🧾 <b>جمع ارزش امتیازهای کسب‌شده و خرج‌شده:</b> {self.format_price(earned_value + spent_value, self.brand.currency)}

📦 <b>جعبه امتیازات</b>
{box_filled}
فقط {box_remaining:g} امتیاز تا کامل شدن این جعبه باقی مانده است.
پیشرفت جعبه: {box_progress}%

📈 <b>نوار پیشرفت رایگان‌شدن خدمات</b>
{progress_bar} {permanent_progress}%
شما حدود {permanent_progress}% مسیر آستانه فعلی دریافت رایگان خدمات را طی کرده‌اید.
        """

        rows = []
        if (
            account
            and service
            and service.is_active
            and service.plan.is_active
            and liquid_points >= free_points > 0
        ):
            rows.append(
                [
                    {
                        "text": "🎁 دریافت سرویس رایگان",
                        "callback_data": f"claim_reward:{account.redemption_nonce.hex}",
                    }
                ]
            )
        rows.extend(
            [
                [
                    {
                        "text": "🎯 کسب امتیاز و رایگان‌شدن خدمات",
                        "callback_data": "how_to_earn",
                    }
                ],
                [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )
        keyboard = self.create_keyboard(rows)

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as exc:
            logger.warning("Could not edit rewards message: %s", exc)
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
            await callback.answer(
                str(exc)
                if isinstance(exc, RewardRedemptionError)
                else "درخواست نامعتبر است.",
                show_alert=True,
            )
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
            self.create_keyboard(
                [
                    [{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}],
                    [{"text": "🎁 امتیازها", "callback_data": "rewards"}],
                ]
            ),
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
                target = (
                    "، ".join(
                        f"{requirement_labels.get(key, key)}: {value}"
                        for key, value in requirements.items()
                    )
                    or "بدون شرط تعریف‌شده"
                )
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
                        [
                            {
                                "text": f"🎁 دریافت {achievement.name}",
                                "callback_data": f"claim_achievement_{achievement.pk}",
                            }
                        ]
                    )
                elif item.is_completed:
                    status = "✅ دریافت شده" if item.reward_claimed else "✅ تکمیل شده"
                else:
                    status = f"📈 پیشرفت {item.progress:g}%"
                claim_text = (
                    f"؛ دفعات دریافت {item.claim_count}"
                    if achievement.is_repeatable and item.claim_count
                    else ""
                )
                lines.append(
                    f"\n<b>{achievement.name}</b> — {status}{claim_text}\n"
                    f"شرط: {target}\nپاداش: {reward_text}"
                )
            text = "\n".join(lines)
            if page > 1:
                rows.append(
                    [
                        {
                            "text": "⬅️ قبلی",
                            "callback_data": f"achievements_page_{page - 1}",
                        }
                    ]
                )
            if page < total_pages:
                rows.append(
                    [
                        {
                            "text": "بعدی ➡️",
                            "callback_data": f"achievements_page_{page + 1}",
                        }
                    ]
                )
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

    async def claim_achievement(
        self, callback: types.CallbackQuery, achievement_id: int
    ):
        """Claim a completed achievement once, with a database idempotency lock."""
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            await sync_to_async(claim_user_achievement, thread_sensitive=True)(
                user_id=user.pk,
                brand_id=self.brand.pk,
                achievement_id=achievement_id,
            )
        except AchievementClaimError:
            await callback.answer(
                "این پاداش آمادهٔ دریافت نیست یا قبلاً دریافت شده است.", show_alert=True
            )
            return
        except Exception as exc:
            logger.error("Achievement claim failed (%s)", type(exc).__name__)
            await callback.answer("❌ دریافت پاداش انجام نشد.", show_alert=True)
            return
        await callback.answer("✅ پاداش دستاورد به حسابتان اضافه شد.")
        await self.show_achievements(callback, answer_callback=False)

    async def show_how_to_earn(self, callback: types.CallbackQuery):
        """Show how to earn rewards"""
        program = (
            await ReferralProgram.objects.filter(brand=self.brand, is_active=True)
            .select_related("reference_service__plan")
            .afirst()
        )
        levels = []
        if program and program.enable_level_rewards:
            async for level in program.levels.order_by("level"):
                levels.append(level)
        level_text = (
            "\n".join(
                f"{level.badge} {level.name} — {level.min_referrals} معرفی، "
                f"{level.min_lifetime_points:g} امتیاز، تبدیل {level.min_conversion_rate:g}%، "
                f"ضریب {level.reward_multiplier:g}×، پاداش {level.bonus_reward:g}"
                for level in levels
            )
            or "سطحی برای این برند تنظیم نشده است."
        )
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
        account = await RewardAccount.objects.filter(
            user=user, brand=self.brand
        ).afirst()
        user_points = account.lifetime_points if account else 0
        top_accounts = []
        async for item in (
            RewardAccount.objects.filter(brand=self.brand)
            .select_related("user")
            .order_by("-lifetime_points", "created_at")[:10]
        ):
            top_accounts.append(item)

        text = """
🏆 جدول امتیازات

<b>۱۰ نفر برتر:</b>
"""

        for top_account in top_accounts:
            is_you = " (شما)" if top_account.user_id == user.id else ""
            rank = (
                await RewardAccount.objects.filter(
                    brand=self.brand,
                    lifetime_points__gt=top_account.lifetime_points,
                ).acount()
                + 1
            )
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
