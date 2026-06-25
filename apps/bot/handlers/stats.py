"""
Statistics Handler for Multi-Tenant VPN Bot
Handles user statistics and detailed analytics
"""

import logging

from aiogram import types

from apps.subscriptions.models import Subscription

from .base import BaseHandler

logger = logging.getLogger(__name__)


class StatsHandler(BaseHandler):
    """Handle statistics and analytics"""

    async def show_statistics(self, callback: types.CallbackQuery):
        """Show user statistics"""
        user = await self.get_or_create_user(callback.from_user)

        subscription_count = await Subscription.objects.filter(
            user=user, brand=self.brand, status="active"
        ).acount()

        from apps.orders.models import Wallet

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
            wallet_balance = wallet.balance
        except Wallet.DoesNotExist:
            wallet_balance = 0

        text = f"""
📊 آمار شما

👤 نام کاربری: {user.username}
📱 تلفن: {user.phone_number or "ثبت نشده"}
📅 تاریخ عضویت: {user.created_at.strftime("%Y/%m/%d") if user.created_at else "نامشخص"}

اشتراک‌های فعال: {subscription_count}
معرفی شده توسط: {user.referred_by.username if user.referred_by else "خودتان"}
تعداد معرفی‌ها: {user.referral_count}
امتیازات: {user.reward_points}
سطح: {user.level}

💰 موجودی کیف پول: {self.format_price(wallet_balance, self.brand.currency)}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📈 آمار جامع‌تر", "callback_data": "detailed_stats"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit statistics message: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_detailed_stats(self, callback: types.CallbackQuery):
        """Show detailed user statistics"""
        user = await self.get_or_create_user(callback.from_user)

        subscriptions = []
        async for subscription in Subscription.objects.filter(
            user=user, brand=self.brand
        ):
            subscriptions.append(subscription)

        active_subs = [s for s in subscriptions if s.status == "active"]
        expired_subs = [s for s in subscriptions if s.status == "expired"]
        cancelled_subs = [s for s in subscriptions if s.status == "cancelled"]

        total_traffic_used = 0
        total_traffic_limit = 0
        for sub in active_subs:
            try:
                if hasattr(sub, "traffic_used"):
                    total_traffic_used += sub.traffic_used
                if hasattr(sub, "traffic_limit"):
                    total_traffic_limit += sub.traffic_limit
            except Exception:
                pass

        from apps.orders.models import Wallet

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
            wallet_balance = wallet.balance
        except Wallet.DoesNotExist:
            wallet_balance = 0

        text = f"""
📈 آمار جامع شما

👤 نام کاربری: {user.username}
📧 ایمیل: {user.email or "ثبت نشده"}
📱 تلفن: {user.phone_number or "ثبت نشده"}
📅 تاریخ عضویت: {user.created_at.strftime("%Y/%m/%d") if user.created_at else "نامشخص"}

📊 اشتراک‌ها:
• اشتراک‌های فعال: {len(active_subs)}
• اشتراک‌های منقضی: {len(expired_subs)}
• اشتراک‌های لغو شده: {len(cancelled_subs)}

🌐 ترافیک:
• ترافیک مصرف شده: {self.format_traffic(total_traffic_used) if total_traffic_used else "نامشخص"}
• حد مجاز ترافیک: {self.format_traffic(total_traffic_limit) if total_traffic_limit else "نامشخص"}

👥 معرفی:
• تعداد کل معرفی‌ها: {user.referral_count}
• امتیازات کسب شده: {user.reward_points}
• سطح فعلی: {user.level}

💰 مالی:
• موجودی کیف پول: {self.format_price(wallet_balance, self.brand.currency)}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📊 آمار اشتراک‌ها", "callback_data": "subscription_stats"}],
                [{"text": "🔙 بازگشت", "callback_data": "statistics"}],
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

    async def show_subscription_stats(self, callback: types.CallbackQuery):
        """Show statistics for all user subscriptions"""
        user = await self.get_or_create_user(callback.from_user)

        subscriptions = []
        async for subscription in Subscription.objects.filter(
            user=user, brand=self.brand
        ):
            subscriptions.append(subscription)

        if not subscriptions:
            await callback.answer("❌ هیچ اشتراکی برای شما وجود ندارد.")
            return

        stats_text = "📊 آمار اشتراک‌های شما:\n\n"

        for idx, sub in enumerate(subscriptions, 1):
            status_emoji = (
                "🟢"
                if sub.status == "active"
                else "🔴"
                if sub.status == "expired"
                else "⚫"
            )

            try:
                traffic_used = getattr(sub, "traffic_used", 0)
                traffic_limit = getattr(sub, "traffic_limit", 0)
                traffic_info = (
                    f"\n  ترافیک: {self.format_traffic(traffic_used)} / {self.format_traffic(traffic_limit)}"
                    if traffic_limit
                    else ""
                )
            except Exception:
                traffic_info = ""

            try:
                start_date = (
                    sub.created_at.strftime("%Y/%m/%d") if sub.created_at else "نامشخص"
                )
                expire_date = (
                    sub.expire_at.strftime("%Y/%m/%d") if sub.expire_at else "نامشخص"
                )
                date_info = f"\n  شروع: {start_date}\n  انقضا: {expire_date}"
            except Exception:
                date_info = ""

            stats_text += (
                f"{idx}. {status_emoji} {getattr(sub, 'name', 'اشتراک')}"
                + date_info
                + traffic_info
                + "\n\n"
            )

        text = f"""
📊 آمار اشتراک‌های شما

{stats_text}

برای اطلاعات بیشتر، به بخش "اشتراک‌های من" مراجعه کنید.
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🔙 بازگشت", "callback_data": "detailed_stats"}],
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
