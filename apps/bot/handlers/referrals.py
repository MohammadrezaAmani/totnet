"""
Referrals Handler for Multi-Tenant VPN Bot
Handles referral system and marketing
"""

import logging

from aiogram import types

from apps.referrals.models import Referral, ReferralLink

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

        text = f"""
👥 سیستم معرفی دوستان

🔗 لینک معرفی شما:
{referral_url}

📊 آمار معرفی:
• تعداد کلیک: {referral_link.click_count}
• تعداد ثبت‌نام: {referral_link.conversion_count}
• تعداد کل معرفی‌ها: {user.referral_count}

💰 درآمد از معرفی:
• امتیاز کسب شده: {user.reward_points}
• سطح فعلی: {user.level}

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
            r for r in referrals if r.status == Referral.ReferralStatus.COMPLETED
        ]
        pending_referrals = [
            r for r in referrals if r.status == Referral.ReferralStatus.PENDING
        ]

        text = f"""
📈 آمار تفصیلی معرفی

📊 آمار کلیک و ثبت:
• تعداد کلیک: {referral_link.click_count or 0}
• تعداد ثبت‌نام: {len(pending_referrals) + len(completed_referrals)}
• نرخ تبدیل: {(len(completed_referrals) / max(referral_link.click_count or 1, 1)) * 100:.1f}%

✅ معرفی‌های تکمیل شده: {len(completed_referrals)}
⏳ معرفی‌های در انتظار: {len(pending_referrals)}

💰 درآمد:
• امتیازات کسب شده: {user.reward_points}
• سطح فعلی: {user.level}

🏆 سطح‌های بعدی:
• سطح ۱: {user.referral_count} معرفی ✓
• سطح ۲: ۱۰ معرفی {"✓" if user.referral_count >= 10 else ""}
• سطح ۳: ۲۵ معرفی {"✓" if user.referral_count >= 25 else ""}
• سطح ۴: ۵۰ معرفی {"✓" if user.referral_count >= 50 else ""}
• سطح ۵: ۱۰۰ معرفی {"✓" if user.referral_count >= 100 else ""}
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
