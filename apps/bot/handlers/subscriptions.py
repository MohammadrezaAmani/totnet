"""
Subscription Management Handler for Multi-Tenant VPN Bot
"""

import io
import logging
from datetime import datetime, timedelta
from html import escape

import qrcode
from aiogram import types
from aiogram.types import BufferedInputFile
from django.db.models import Q

from apps.subscriptions.models import Subscription, SubscriptionConfig
from apps.vpn_providers.models import VPNProvider

from .base import BaseHandler

logger = logging.getLogger(__name__)


class SubscriptionHandler(BaseHandler):
    """Handle subscription management and delivery"""

    async def show_my_subscriptions(self, callback: types.CallbackQuery):
        """Show the XMind subscription-management landing page."""
        user, _ = await self.get_or_create_user(callback.from_user)
        own_count = await Subscription.objects.filter(
            owner=user, brand=self.brand
        ).acount()
        others_count = (
            await Subscription.objects.filter(user=user, brand=self.brand)
            .exclude(owner=user)
            .acount()
        )

        text = f"""
📱 <b>اشتراک‌های من</b>

• اشتراک‌های خودم: {own_count}
• اشتراک اطرافیان: {others_count}

بخش موردنظر را انتخاب کنید:
        """
        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": f"👤 لیست اشتراک‌های خودم ({own_count})",
                        "callback_data": "my_own_subscriptions",
                    }
                ],
                [
                    {
                        "text": f"👥 اشتراک اطرافیان ({others_count})",
                        "callback_data": "family_subscriptions",
                    }
                ],
                [{"text": "🔄 صفحه تمدید", "callback_data": "renewal_page"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_own_subscriptions(self, callback: types.CallbackQuery):
        """List subscriptions whose current owner is the Telegram user."""
        user, _ = await self.get_or_create_user(callback.from_user)
        queryset = Subscription.objects.filter(
            owner=user, brand=self.brand
        ).select_related("plan", "vpn_provider", "owner", "user")
        await self._show_subscription_list(
            callback, queryset, title="👤 اشتراک‌های خودم"
        )

    async def show_family_subscriptions(self, callback: types.CallbackQuery):
        """List subscriptions bought by the user for somebody else."""
        user, _ = await self.get_or_create_user(callback.from_user)
        queryset = (
            Subscription.objects.filter(user=user, brand=self.brand)
            .exclude(owner=user)
            .select_related("plan", "vpn_provider", "owner", "user")
        )
        await self._show_subscription_list(
            callback, queryset, title="👥 اشتراک اطرافیان"
        )

    async def _show_subscription_list(self, callback, queryset, *, title: str):
        subscriptions = []
        async for sub in queryset.order_by("-created_at")[:30]:
            subscriptions.append(sub)

        if not subscriptions:
            text = f"{title}\n\nاشتراکی در این بخش وجود ندارد."
            rows = [
                [{"text": "🛒 خرید اشتراک", "callback_data": "purchase_subscription"}],
                [{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}],
            ]
        else:
            text = f"{title}\n\n{len(subscriptions)} اشتراک:"
            rows = []
            for sub in subscriptions:
                status_emoji = {
                    Subscription.SubscriptionStatus.ACTIVE: "🟢",
                    Subscription.SubscriptionStatus.EXPIRED: "🔴",
                    Subscription.SubscriptionStatus.SUSPENDED: "🟡",
                    Subscription.SubscriptionStatus.CANCELLED: "⚫",
                    Subscription.SubscriptionStatus.PENDING: "🟠",
                }.get(sub.status, "❓")
                remaining = ""
                if sub.expires_at:
                    days_left = (
                        sub.expires_at - datetime.now(sub.expires_at.tzinfo)
                    ).days
                    remaining = f" • {days_left} روز" if days_left > 0 else " • منقضی"
                owner = sub.owner.full_name or sub.owner.username
                owner_suffix = f" • {owner}" if sub.user_id != sub.owner_id else ""
                rows.append(
                    [
                        {
                            "text": f"{status_emoji} {sub.plan.name}{remaining}{owner_suffix}",
                            "callback_data": f"subscription_details_{sub.id}",
                        }
                    ]
                )
            rows.extend(
                [
                    [{"text": "🔄 صفحه تمدید", "callback_data": "renewal_page"}],
                    [{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}],
                ]
            )

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def show_renewal_page(self, callback: types.CallbackQuery):
        """Show renewal/re-purchase options without pretending unsupported provider renewals exist."""
        user, _ = await self.get_or_create_user(callback.from_user)
        subscriptions = []
        async for sub in (
            Subscription.objects.filter(Q(user=user) | Q(owner=user), brand=self.brand)
            .select_related("plan", "owner", "user", "vpn_provider")
            .exclude(status=Subscription.SubscriptionStatus.CANCELLED)
            .order_by("-created_at")[:30]
        ):
            subscriptions.append(sub)

        text = "🔄 <b>صفحه تمدید</b>\n\nاشتراکی را برای تمدید یا خرید مجدد انتخاب کنید."
        if not subscriptions:
            rows = [
                [{"text": "🛒 خرید اشتراک", "callback_data": "purchase_subscription"}],
                [{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}],
            ]
        else:
            rows = []
            for sub in subscriptions:
                owner = sub.owner.full_name or sub.owner.username
                rows.append(
                    [
                        {
                            "text": f"🔄 {sub.plan.name} • {owner}",
                            "callback_data": f"renewal_details_{sub.pk}",
                        }
                    ]
                )
            rows.append([{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}])

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def show_renewal_details(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Explain renewal semantics and offer a safe same-plan re-purchase."""
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            subscription = (
                await Subscription.objects.select_related(
                    "plan", "owner", "user", "vpn_provider"
                )
                .filter(Q(user=user) | Q(owner=user), brand=self.brand)
                .aget(pk=subscription_id)
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
            return

        plan = subscription.plan
        owner_name = subscription.owner.full_name or subscription.owner.username
        expiry = (
            subscription.expires_at.strftime("%Y/%m/%d %H:%M")
            if subscription.expires_at
            else "نامحدود"
        )
        plan_available = plan.is_active and plan.is_visible
        text = f"""
🔄 <b>تمدید اشتراک</b>

پلن: {escape(plan.name)}
دارنده: {escape(owner_name)}
انقضای فعلی: {expiry}
قیمت فعلی همان پلن: {self.format_price(plan.discounted_price, plan.currency)}

در نسخه فعلی، تمدید مستقیم روی حساب موجودِ ارائه‌دهنده پیاده‌سازی نشده است. گزینه زیر یک اشتراک جدید با همین پلن می‌سازد تا تمدید جعلی یا تغییر تأییدنشده روی پنل VPN انجام نشود.
        """
        rows = []
        if plan_available:
            rows.append(
                [
                    {
                        "text": "🛒 خرید مجدد همین پلن",
                        "callback_data": f"repurchase_subscription_{subscription.pk}",
                    }
                ]
            )
        else:
            text += "\n⚠️ این پلن دیگر برای فروش فعال نیست."
        rows.extend(
            [
                [
                    {
                        "text": "🛒 انتخاب پلن دیگر",
                        "callback_data": "purchase_subscription",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "renewal_page"}],
            ]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def show_subscription_details(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Show detailed subscription information"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related(
                    "plan", "vpn_provider", "owner", "user"
                )
                .filter(Q(user=user) | Q(owner=user))
                .aget(id=subscription_id, brand=self.brand)
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
            return

        text = f"""
📱 جزئیات اشتراک

🏷️ پلن: {subscription.plan.name}
🆔 شناسه: `{subscription.subscription_id}`
📊 وضعیت: {self.get_status_text(subscription.status)}
👤 خریدار: {subscription.user.full_name or subscription.user.username}
🎁 دارنده: {subscription.owner.full_name or subscription.owner.username}

⏰ اطلاعات زمان:
• شروع: {subscription.starts_at.strftime("%Y/%m/%d %H:%M")}
"""

        if subscription.expires_at:
            text += f"• انقضا: {subscription.expires_at.strftime('%Y/%m/%d %H:%M')}\n"
            days_remaining = subscription.days_remaining
            if days_remaining is not None:
                text += f"• باقی‌مانده: {days_remaining} روز\n"
        else:
            text += "• انقضا: نامحدود\n"

        if subscription.traffic_limit_gb:
            usage_percent = subscription.traffic_percentage_used
            text += f"""
📊 اطلاعات ترافیک:
• حجم کل: {self.format_traffic(subscription.traffic_limit_gb)}
• مصرف شده: {self.format_traffic(float(subscription.traffic_used_gb))}
• باقی‌مانده: {self.format_traffic(float(subscription.traffic_limit_gb) - float(subscription.traffic_used_gb))}
• درصد مصرف: {usage_percent:.1f}%
"""

        text += f"""
🖥️ اطلاعات سرور:
• ارائه‌دهنده: {subscription.vpn_provider.name}
• نوع: {subscription.vpn_provider.get_provider_type_display()}
"""

        if subscription.last_connection:
            text += f"• آخرین اتصال: {subscription.last_connection.strftime('%Y/%m/%d %H:%M')}\n"

        text += f"• تعداد اتصالات: {subscription.total_connections}\n"

        keyboard_buttons = []

        if subscription.status == Subscription.SubscriptionStatus.ACTIVE:
            keyboard_buttons.extend(
                [
                    [
                        {
                            "text": "📥 دریافت کانفیگ",
                            "callback_data": f"get_config_{subscription_id}",
                        },
                        {
                            "text": "📊 آمار مصرف",
                            "callback_data": f"usage_stats_{subscription_id}",
                        },
                    ],
                ]
            )
        elif subscription.status == Subscription.SubscriptionStatus.EXPIRED:
            keyboard_buttons.append(
                [
                    {
                        "text": "🛒 خرید اشتراک جدید",
                        "callback_data": "purchase_subscription",
                    }
                ]
            )
        if subscription.status != Subscription.SubscriptionStatus.CANCELLED:
            keyboard_buttons.append(
                [
                    {
                        "text": "🔄 تمدید / خرید مجدد",
                        "callback_data": f"renewal_details_{subscription_id}",
                    }
                ]
            )
        keyboard_buttons.append(
            [{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}]
        )

        keyboard = self.create_keyboard(keyboard_buttons)

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def get_subscription_config(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Send subscription configuration to user"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related("vpn_provider", "plan")
                .filter(Q(user=user) | Q(owner=user))
                .aget(
                    id=subscription_id,
                    brand=self.brand,
                    status=Subscription.SubscriptionStatus.ACTIVE,
                )
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک فعال یافت نشد.", show_alert=True)
            return

        provider_type = subscription.vpn_provider.provider_type

        if provider_type == VPNProvider.ProviderType.CONNECTIX:
            try:
                config = await SubscriptionConfig.objects.aget(
                    subscription=subscription
                )
            except SubscriptionConfig.DoesNotExist:
                config = None
            await self.send_connectix_config(callback, subscription, config)
            return

        if provider_type == VPNProvider.ProviderType.HIDDIFY:
            from apps.bot.handlers.subscription_hiddify import (
                SubscriptionHiddifyHandler,
            )

            await SubscriptionHiddifyHandler(
                self.bot, self.brand
            ).get_subscription_config(callback, subscription_id)
            return

        try:
            config = await SubscriptionConfig.objects.aget(subscription=subscription)
        except SubscriptionConfig.DoesNotExist:
            config = None

        if not config:
            await callback.answer(
                "❌ خطا در دریافت کانفیگ. با پشتیبانی تماس بگیرید.", show_alert=True
            )
            return
        await self.send_standard_config(callback, subscription, config)

    async def send_connectix_config(
        self,
        callback: types.CallbackQuery,
        subscription: Subscription,
        config: SubscriptionConfig | None,
    ):
        """Send the verified Connectix subscription link and its QR code."""
        subscription_url = subscription.subscription_url or (
            config.subscription_url if config else ""
        )
        if not subscription_url:
            await callback.answer(
                "اشتراک هنوز از Connectix آماده نشده است.", show_alert=True
            )
            return

        text = f"""
📱 اطلاعات اتصال - {subscription.plan.name}

👤 نام کاربری: <code>{escape(subscription.connectix_username or "—")}</code>

🔗 لینک اشتراک:
<code>{escape(subscription_url)}</code>

لینک اشتراک یا QR را در برنامه VPN مورد نظرتان وارد کنید.
        """

        qr_image = self.generate_qr_code(subscription_url)

        await self.bot.send_message(callback.message.chat.id, text, parse_mode="HTML")

        if qr_image:
            await self.bot.send_photo(
                callback.message.chat.id,
                photo=BufferedInputFile(qr_image, filename="qr_code.png"),
                caption="📱 QR Code اتصال",
            )

        await callback.answer("✅ اطلاعات اتصال ارسال شد")

    async def send_standard_config(
        self,
        callback: types.CallbackQuery,
        subscription: Subscription,
        config: SubscriptionConfig,
    ):
        """Send standard VPN configuration (VLESS, VMess, etc.)"""
        text = f"""
📱 کانفیگ اتصال - {subscription.plan.name}

🔗 لینک اشتراک:
`{config.subscription_url}`

📱 نحوه اتصال:
1️⃣ اپلیکیشن v2ray یا مشابه را نصب کنید
2️⃣ لینک بالا را کپی کنید
3️⃣ در اپلیکیشن Add Config کنید

📋 کانفیگ‌های موجود:
        """

        keyboards = []
        _ = []

        if config.vless_config:
            text += "• VLESS ✅\n"
            keyboards.append(
                [
                    {
                        "text": "📋 کپی VLESS",
                        "callback_data": f"copy_config_vless_{subscription.id}",
                    }
                ]
            )

        if config.vmess_config:
            text += "• VMess ✅\n"
            keyboards.append(
                [
                    {
                        "text": "📋 کپی VMess",
                        "callback_data": f"copy_config_vmess_{subscription.id}",
                    }
                ]
            )

        if config.trojan_config:
            text += "• Trojan ✅\n"
            keyboards.append(
                [
                    {
                        "text": "📋 کپی Trojan",
                        "callback_data": f"copy_config_trojan_{subscription.id}",
                    }
                ]
            )

        keyboards.extend(
            [
                [
                    {
                        "text": "📱 QR Code",
                        "callback_data": f"qr_codes_{subscription.id}",
                    }
                ],
                [
                    {
                        "text": "📥 فایل کانفیگ",
                        "callback_data": f"config_file_{subscription.id}",
                    }
                ],
                [
                    {
                        "text": "🔙 بازگشت",
                        "callback_data": f"subscription_details_{subscription.id}",
                    }
                ],
            ]
        )

        keyboard = self.create_keyboard(keyboards)

        await self.bot.send_message(
            callback.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard
        )

        if config.subscription_url:
            qr_image = self.generate_qr_code(config.subscription_url)
            if qr_image:
                await self.bot.send_photo(
                    callback.message.chat.id,
                    photo=BufferedInputFile(qr_image, filename="subscription_qr.png"),
                    caption="📱 QR Code لینک اشتراک",
                )

        await callback.answer("✅ کانفیگ ارسال شد")

    async def show_usage_statistics(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Show subscription usage statistics"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related("plan")
                .filter(Q(user=user) | Q(owner=user))
                .aget(id=subscription_id, brand=self.brand)
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
            return

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=7)

        usage_stats = []
        async for stat in subscription.usage_stats.filter(
            date__gte=start_date, date__lte=end_date
        ).order_by("-date"):
            usage_stats.append(stat)

        text = f"""
📊 آمار مصرف - {subscription.plan.name}

📈 آمار 7 روز اخیر:
"""

        total_upload = 0
        total_download = 0

        if usage_stats:
            for stat in usage_stats:
                upload_gb = stat.upload_bytes / (1024**3)
                download_gb = stat.download_bytes / (1024**3)
                total_upload += upload_gb
                total_download += download_gb

                text += f"""
📅 {stat.date.strftime("%Y/%m/%d")}:
  ⬆️ آپلود: {upload_gb:.2f} GB
  ⬇️ دانلود: {download_gb:.2f} GB
  🔢 اتصالات: {stat.connection_count}
"""
        else:
            text += "❌ آمار مصرفی موجود نیست.\n"

        text += f"""
📊 جمع کل هفته:
• ⬆️ کل آپلود: {total_upload:.2f} GB
• ⬇️ کل دانلود: {total_download:.2f} GB
• 📈 کل ترافیک: {(total_upload + total_download):.2f} GB
"""

        if subscription.traffic_limit_gb:
            remaining = float(subscription.traffic_limit_gb) - float(
                subscription.traffic_used_gb
            )
            text += f"• 📊 باقی‌مانده: {remaining:.2f} GB\n"

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🔄 بروزرسانی آمار",
                        "callback_data": f"refresh_stats_{subscription_id}",
                    }
                ],
                [
                    {
                        "text": "🔙 بازگشت",
                        "callback_data": f"subscription_details_{subscription_id}",
                    }
                ],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    def generate_qr_code(self, data: str) -> bytes:
        """Generate QR code image"""
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(data)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")

            bio = io.BytesIO()
            img.save(bio, format="PNG")
            return bio.getvalue()
        except Exception as e:
            logger.error(f"Error generating QR code: {e}")
            return None

    def get_status_text(self, status: str) -> str:
        """Get Persian status text"""
        status_map = {
            "active": "🟢 فعال",
            "expired": "🔴 منقضی شده",
            "suspended": "🟡 تعلیق شده",
            "cancelled": "⚫ لغو شده",
            "pending": "🟠 در انتظار فعال‌سازی",
        }
        return status_map.get(status, "❓ نامشخص")

    async def show_qr_codes(self, callback: types.CallbackQuery, subscription_id: int):
        """Show QR codes for subscription configuration"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related("plan", "vpn_provider")
                .filter(Q(user=user) | Q(owner=user))
                .aget(
                    id=subscription_id,
                    brand=self.brand,
                    status=Subscription.SubscriptionStatus.ACTIVE,
                )
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک فعال یافت نشد.", show_alert=True)
            return

        try:
            config = await SubscriptionConfig.objects.aget(subscription=subscription)
        except SubscriptionConfig.DoesNotExist:
            await callback.answer(
                "❌ کانفیگ برای این اشتراک موجود نیست.", show_alert=True
            )
            return

        text = f"""
📱 کد QR - {subscription.plan.name}

لطفاً برای دریافت کانفیگ‌های مختلف روی دکمه‌های زیر کلیک کنید:
        """

        keyboard_buttons = []

        if config.vless_config:
            qr_bytes = self.generate_qr_code(config.subscription_url or "")
            if qr_bytes:
                keyboard_buttons.append(
                    [
                        {
                            "text": "📲 QR - VLESS",
                            "callback_data": f"download_vless_{subscription_id}",
                        }
                    ]
                )

        if config.vmess_config:
            keyboard_buttons.append(
                [
                    {
                        "text": "📲 QR - VMess",
                        "callback_data": f"download_vmess_{subscription_id}",
                    }
                ]
            )

        if config.trojan_config:
            keyboard_buttons.append(
                [
                    {
                        "text": "📲 QR - Trojan",
                        "callback_data": f"download_trojan_{subscription_id}",
                    }
                ]
            )

        keyboard_buttons.append(
            [
                {
                    "text": "🔙 بازگشت",
                    "callback_data": f"subscription_details_{subscription_id}",
                }
            ]
        )

        keyboard = self.create_keyboard(keyboard_buttons)

        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def copy_config(
        self, callback: types.CallbackQuery, subscription_id: int, config_type: str
    ):
        """Copy configuration to clipboard (simulated)"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related("plan", "vpn_provider")
                .filter(Q(user=user) | Q(owner=user))
                .aget(
                    id=subscription_id,
                    brand=self.brand,
                    status=Subscription.SubscriptionStatus.ACTIVE,
                )
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک فعال یافت نشد.", show_alert=True)
            return

        try:
            config = await SubscriptionConfig.objects.aget(subscription=subscription)
        except SubscriptionConfig.DoesNotExist:
            await callback.answer(
                "❌ کانفیگ برای این اشتراک موجود نیست.", show_alert=True
            )
            return

        config_text = ""
        if config_type == "vless" and config.vless_config:
            config_text = config.subscription_url or "VLESS Config"
        elif config_type == "vmess" and config.vmess_config:
            config_text = "VMess Config"
        elif config_type == "trojan" and config.trojan_config:
            config_text = "Trojan Config"

        if config_text:
            await callback.answer(
                f"✅ کانفیگ {config_type.upper()} کپی شد\n\n{config_text}",
                show_alert=True,
            )
        else:
            await callback.answer(
                "❌ این نوع کانفیگ برای شما موجود نیست.", show_alert=True
            )

    async def send_config_file(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Send configuration as a file"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related("plan", "vpn_provider")
                .filter(Q(user=user) | Q(owner=user))
                .aget(
                    id=subscription_id,
                    brand=self.brand,
                    status=Subscription.SubscriptionStatus.ACTIVE,
                )
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک فعال یافت نشد.", show_alert=True)
            return

        try:
            config = await SubscriptionConfig.objects.aget(subscription=subscription)
        except SubscriptionConfig.DoesNotExist:
            await callback.answer(
                "❌ کانفیگ برای این اشتراک موجود نیست.", show_alert=True
            )
            return

        text = f"""
📥 دریافت فایل کانفیگ - {subscription.plan.name}

برای دریافت فایل کانفیگ روی دکمه‌های زیر کلیک کنید:
        """

        keyboard_buttons = []

        if config.vless_config:
            keyboard_buttons.append(
                [
                    {
                        "text": "📄 فایل VLESS",
                        "callback_data": f"download_vless_{subscription_id}",
                    }
                ]
            )

        if config.vmess_config:
            keyboard_buttons.append(
                [
                    {
                        "text": "📄 فایل VMess",
                        "callback_data": f"download_vmess_{subscription_id}",
                    }
                ]
            )

        if config.trojan_config:
            keyboard_buttons.append(
                [
                    {
                        "text": "📄 فایل Trojan",
                        "callback_data": f"download_trojan_{subscription_id}",
                    }
                ]
            )

        keyboard_buttons.append(
            [
                {
                    "text": "🔙 بازگشت",
                    "callback_data": f"subscription_details_{subscription_id}",
                }
            ]
        )

        keyboard = self.create_keyboard(keyboard_buttons)

        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def download_config(
        self, callback: types.CallbackQuery, subscription_id: int, config_type: str
    ):
        """Download configuration file"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related("plan", "vpn_provider")
                .filter(Q(user=user) | Q(owner=user))
                .aget(
                    id=subscription_id,
                    brand=self.brand,
                    status=Subscription.SubscriptionStatus.ACTIVE,
                )
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک فعال یافت نشد.", show_alert=True)
            return

        try:
            config = await SubscriptionConfig.objects.aget(subscription=subscription)
        except SubscriptionConfig.DoesNotExist:
            await callback.answer(
                "❌ کانفیگ برای این اشتراک موجود نیست.", show_alert=True
            )
            return

        if config_type == "vless" and config.vless_config:
            qr_bytes = self.generate_qr_code(config.subscription_url or "")
            if qr_bytes:
                try:
                    from aiogram.types import BufferedInputFile

                    input_file = BufferedInputFile(
                        file=qr_bytes,
                        filename=f"qr_{config_type}_{subscription_id}.png",
                    )

                    await self.bot.send_photo(
                        chat_id=callback.message.chat.id,
                        photo=input_file,
                        caption=f"📱 QR Code - {config_type.upper()}\n\n{subscription.plan.name}",
                    )
                except Exception as e:
                    logger.error(f"Error sending QR code: {e}")
                    await callback.answer("❌ خطا در ارسال کد QR")
            else:
                await callback.answer("❌ خطا در تولید کد QR")
        elif config_type == "vmess" and config.vmess_config:
            await callback.answer("📱 کانفیگ VMess آماده است", show_alert=True)
        elif config_type == "trojan" and config.trojan_config:
            await callback.answer("📱 کانفیگ Trojan آماده است", show_alert=True)
        else:
            await callback.answer(
                "❌ این نوع کانفیگ برای شما موجود نیست.", show_alert=True
            )
