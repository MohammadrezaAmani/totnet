"""
Referrals Handler for Multi-Tenant VPN Bot
Handles referral system and marketing
"""

import logging

from aiogram import types

from apps.referrals.models import Referral, ReferralLink, ReferralProgram, RewardAccount

from .base import BaseHandler

logger = logging.getLogger(__name__)


class ReferralsHandler(BaseHandler):
    """Handle referral system operations"""

    async def get_bot_username(self) -> str:
        """Get bot username, preferring brand config over API call"""

        if hasattr(self.brand, "bot_username") and self.brand.bot_username:
            return self.brand.bot_username

        try:
            me = await self.bot.me()
            return me.username or self.brand.slug
        except Exception as e:
            logger.warning(f"Could not fetch bot username: {e}")
            return self.brand.slug

    async def show_referral_menu(self, callback: types.CallbackQuery):
        """Show referral system menu"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            referral_link = await ReferralLink.objects.aget(user=user, brand=self.brand)
        except ReferralLink.DoesNotExist:
            referral_link = await ReferralLink.objects.acreate(
                user=user, brand=self.brand, code=user.referral_code
            )

        bot_username = await self.get_bot_username()
        referral_url = f"https://t.me/{bot_username}?start={referral_link.code}"
        account = await RewardAccount.objects.filter(user=user, brand=self.brand).afirst()
        lifetime_points = account.lifetime_points if account else 0
        program = await ReferralProgram.objects.filter(brand=self.brand, is_active=True).select_related("reference_service").afirst()
        current_level = None
        if program:
            async for level in program.levels.filter(min_lifetime_points__lte=lifetime_points).order_by("-min_lifetime_points")[:1]:
                current_level = level
        level_label = f"{current_level.badge} {current_level.name}" if current_level else "—"

        text = f"""
👥 سیستم معرفی دوستان

🔗 لینک معرفی شما:
{referral_url}

📊 آمار معرفی:
• تعداد کلیک: {referral_link.click_count}
• تعداد ثبت‌نام: {referral_link.conversion_count}
• تعداد کل معرفی‌ها: {user.referral_count}

💰 درآمد از معرفی:
• امتیاز مادام‌العمر: {lifetime_points:g}
• امتیاز کامل قابل استفاده: {account.liquid_points if account else 0:g}
• سطح فعلی: {level_label}

با معرفی دوستان خود امتیاز و جایزه کسب کنید!
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📤 اشتراک‌گذاری لینک", "callback_data": "share_referral"}],
                [{"text": "📈 آمار تفصیلی", "callback_data": "referral_stats"}],
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

    async def show_referral_stats(self, callback: types.CallbackQuery):
        """Show detailed referral statistics"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            referral_link = await ReferralLink.objects.aget(user=user, brand=self.brand)
        except ReferralLink.DoesNotExist:
            await callback.answer("❌ لینک معرفی یافت نشد.")
            return

        referrals = []
        async for referral in Referral.objects.filter(referrer=user, brand=self.brand):
            referrals.append(referral)

        completed_referrals = [
            r for r in referrals if r.status == Referral.ReferralStatus.REWARDED
        ]
        pending_referrals = [
            r for r in referrals if r.status == Referral.ReferralStatus.PENDING
        ]

        account = await RewardAccount.objects.filter(user=user, brand=self.brand).afirst()
        lifetime_points = account.lifetime_points if account else 0
        program = await ReferralProgram.objects.filter(brand=self.brand, is_active=True).select_related("reference_service").afirst()
        level_lines = []
        if program:
            async for level in program.levels.order_by("level"):
                done = lifetime_points >= level.min_lifetime_points
                level_lines.append(f"• {level.badge} {level.name}: {level.min_lifetime_points:g} امتیاز {'✓' if done else ''}")
        levels_text = "\n".join(level_lines) or "سطحی برای این برند تنظیم نشده است."
        text = f"""
📈 آمار تفصیلی معرفی

📊 آمار کلیک و ثبت:
• تعداد کلیک: {referral_link.click_count or 0}
• تعداد ثبت‌نام: {len(referrals)}
• نرخ تبدیل: {(len(completed_referrals) / max(referral_link.click_count or 1, 1)) * 100:.1f}%

✅ معرفی‌های تکمیل شده: {len(completed_referrals)}
⏳ معرفی‌های در انتظار: {len(pending_referrals)}

💰 درآمد:
• امتیاز مادام‌العمر: {lifetime_points:g}
• امتیاز کامل قابل استفاده: {account.liquid_points if account else 0:g}

🏆 سطح‌ها:
{levels_text}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🔙 بازگشت", "callback_data": "referral_system"}],
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

    async def share_referral_link(self, callback: types.CallbackQuery):
        """Share referral link with user"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            referral_link = await ReferralLink.objects.aget(user=user, brand=self.brand)
        except ReferralLink.DoesNotExist:
            referral_link = await ReferralLink.objects.acreate(
                user=user, brand=self.brand, code=user.referral_code
            )

        bot_username = await self.get_bot_username()
        referral_url = f"https://t.me/{bot_username}?start={referral_link.code}"

        text = f"""
📤 لینک معرفی شما برای اشتراک‌گذاری:

<code>{referral_url}</code>

با اشتراک‌گذاری این لینک، دوستان شما می‌توانند از بات استفاده کنند و شما هم امتیاز کسب خواهید کرد!
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🔗 کپی کردن لینک",
                        "callback_data": "copy_referral_link",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "referral_system"}],
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

        await callback.answer("✅ لینک معرفی شما آماده است!")

    async def copy_referral_link(self, callback: types.CallbackQuery):
        """Copy referral link to clipboard"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            referral_link = await ReferralLink.objects.aget(user=user, brand=self.brand)
        except ReferralLink.DoesNotExist:
            await callback.answer("❌ لینک معرفی یافت نشد.")
            return

        bot_username = await self.get_bot_username()
        referral_url = f"https://t.me/{bot_username}?start={referral_link.code}"

        await callback.answer(
            f"لینک شما: {referral_url}\n\nتوجه: لطفاً لینک را کپی کنید و برای دوستانتان بفرستید.",
            show_alert=False,
        )
