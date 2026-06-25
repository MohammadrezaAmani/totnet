"""
Subscription Management Handler for Multi-Tenant VPN Bot
"""

import io
import logging
from datetime import datetime, timedelta

import qrcode
from aiogram import types
from aiogram.types import BufferedInputFile

from apps.subscriptions.models import Subscription, SubscriptionConfig

from .base import BaseHandler

logger = logging.getLogger(__name__)


class SubscriptionHandler(BaseHandler):
    """Handle subscription management and delivery"""

    async def show_my_subscriptions(self, callback: types.CallbackQuery):
        """Show user's subscriptions"""
        user = await self.get_or_create_user(callback.from_user)

        subscriptions = []
        async for sub in (
            Subscription.objects.filter(user=user, brand=self.brand)
            .select_related("plan", "vpn_provider")
            .order_by("-created_at")
        ):
            subscriptions.append(sub)

        if not subscriptions:
            text = """
📱 اشتراک‌های من

شما هنوز هیچ اشتراکی ندارید.

برای خرید اشتراک جدید از منوی اصلی استفاده کنید.
            """
            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🛒 خرید اشتراک",
                            "callback_data": "purchase_subscription",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
                ]
            )
        else:
            text = f"""
📱 اشتراک‌های من

شما {len(subscriptions)} اشتراک دارید:
            """

            keyboard_buttons = []
            for sub in subscriptions[:10]:
                status_emoji = {
                    "active": "🟢",
                    "expired": "🔴",
                    "suspended": "🟡",
                    "cancelled": "⚫",
                    "pending": "🟠",
                }.get(sub.status, "❓")

                remaining = ""
                if sub.expires_at:
                    days_left = (
                        sub.expires_at - datetime.now(sub.expires_at.tzinfo)
                    ).days
                    if days_left > 0:
                        remaining = f"({days_left} روز)"
                    else:
                        remaining = "(منقضی شده)"

                subscription_text = f"{status_emoji} {sub.plan.name} {remaining}"

                keyboard_buttons.append(
                    [
                        {
                            "text": subscription_text,
                            "callback_data": f"subscription_details_{sub.id}",
                        }
                    ]
                )

            keyboard_buttons.extend(
                [
                    [
                        {
                            "text": "🛒 خرید اشتراک جدید",
                            "callback_data": "purchase_subscription",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
                ]
            )
            keyboard = self.create_keyboard(keyboard_buttons)

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_subscription_details(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Show detailed subscription information"""
        user = await self.get_or_create_user(callback.from_user)

        try:
            subscription = await Subscription.objects.select_related(
                "plan", "vpn_provider"
            ).aget(id=subscription_id, user=user, brand=self.brand)
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
            return

        text = f"""
📱 جزئیات اشتراک

🏷️ پلن: {subscription.plan.name}
🆔 شناسه: `{subscription.subscription_id}`
📊 وضعیت: {self.get_status_text(subscription.status)}

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
• باقی‌مانده: {self.format_traffic(subscription.traffic_limit_gb - float(subscription.traffic_used_gb))}
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
                    [
                        {
                            "text": "🔄 تمدید",
                            "callback_data": f"renew_{subscription_id}",
                        },
                        {
                            "text": "⬆️ ارتقا",
                            "callback_data": f"upgrade_{subscription_id}",
                        },
                    ],
                ]
            )
        elif subscription.status == Subscription.SubscriptionStatus.EXPIRED:
            keyboard_buttons.append(
                [
                    {
                        "text": "🔄 تمدید اشتراک",
                        "callback_data": f"renew_{subscription_id}",
                    }
                ]
            )

        keyboard_buttons.extend(
            [
                [
                    {
                        "text": "🎁 انتقال به دیگری",
                        "callback_data": f"transfer_{subscription_id}",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}],
            ]
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
        user = await self.get_or_create_user(callback.from_user)

        try:
            subscription = await Subscription.objects.select_related(
                "vpn_provider", "plan"
            ).aget(
                id=subscription_id,
                user=user,
                brand=self.brand,
                status=Subscription.SubscriptionStatus.ACTIVE,
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک فعال یافت نشد.", show_alert=True)
            return

        try:
            config = await SubscriptionConfig.objects.aget(subscription=subscription)
        except SubscriptionConfig.DoesNotExist:
            config = await self.generate_subscription_config(subscription)

        if not config:
            await callback.answer(
                "❌ خطا در دریافت کانفیگ. با پشتیبانی تماس بگیرید.", show_alert=True
            )
            return

        provider_type = subscription.vpn_provider.provider_type

        if provider_type == "connectix":
            await self.send_connectix_config(callback, subscription, config)
        else:
            await self.send_standard_config(callback, subscription, config)

    async def send_connectix_config(
        self,
        callback: types.CallbackQuery,
        subscription: Subscription,
        config: SubscriptionConfig,
    ):
        """Send Connectix-style configuration (username/password + QR)"""
        text = f"""
📱 اطلاعات اتصال - {subscription.plan.name}

👤 نام کاربری: `{subscription.connectix_username}`
🔑 کلمه عبور: `{subscription.connectix_password}`

📱 نحوه اتصال:
1️⃣ اپلیکیشن کانکتیکس را نصب کنید
2️⃣ نام کاربری و کلمه عبور را وارد کنید
3️⃣ روی اتصال کلیک کنید

یا از QR Code زیر استفاده کنید:
        """

        qr_data = f"connectix://{subscription.connectix_username}:{subscription.connectix_password}"
        qr_image = self.generate_qr_code(qr_data)

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
        user = await self.get_or_create_user(callback.from_user)

        try:
            subscription = await Subscription.objects.select_related("plan").aget(
                id=subscription_id, user=user, brand=self.brand
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
            remaining = subscription.traffic_limit_gb - float(
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

    async def generate_subscription_config(self, subscription: Subscription):
        """Generate VPN configuration for subscription"""

        try:
            vless_config = {
                "id": str(subscription.subscription_id),
                "add": subscription.vpn_provider.base_url.replace(
                    "https://", ""
                ).replace("http://", ""),
                "port": "443",
                "ps": f"{self.brand.name} - {subscription.plan.name}",
                "net": "ws",
                "type": "none",
                "host": "",
                "path": "/",
                "tls": "tls",
            }

            subscription_url = f"vless://{vless_config['id']}@{vless_config['add']}:{vless_config['port']}"

            config = await SubscriptionConfig.objects.acreate(
                subscription=subscription,
                vless_config=vless_config,
                subscription_url=subscription_url,
            )

            return config
        except Exception as e:
            logger.error(f"Error generating config: {e}")
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
        user = await self.get_or_create_user(callback.from_user)

        try:
            subscription = await Subscription.objects.select_related(
                "plan", "vpn_provider"
            ).aget(
                id=subscription_id,
                user=user,
                brand=self.brand,
                status=Subscription.SubscriptionStatus.ACTIVE,
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
        user = await self.get_or_create_user(callback.from_user)

        try:
            subscription = await Subscription.objects.select_related(
                "plan", "vpn_provider"
            ).aget(
                id=subscription_id,
                user=user,
                brand=self.brand,
                status=Subscription.SubscriptionStatus.ACTIVE,
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
        user = await self.get_or_create_user(callback.from_user)

        try:
            subscription = await Subscription.objects.select_related(
                "plan", "vpn_provider"
            ).aget(
                id=subscription_id,
                user=user,
                brand=self.brand,
                status=Subscription.SubscriptionStatus.ACTIVE,
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
        user = await self.get_or_create_user(callback.from_user)

        try:
            subscription = await Subscription.objects.select_related(
                "plan", "vpn_provider"
            ).aget(
                id=subscription_id,
                user=user,
                brand=self.brand,
                status=Subscription.SubscriptionStatus.ACTIVE,
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
