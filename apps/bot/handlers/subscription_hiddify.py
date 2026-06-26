"""
Enhanced Subscription Handler with Hiddify Integration
Full client-side features for VPN users

Features:
- Profile information
- All VPN configs
- Recommended apps by platform
- MTProxy configurations
- QR codes and subscription URLs
"""

import io
import logging
from typing import Optional
from uuid import uuid4

import qrcode
from aiogram import types
from aiogram.enums import ContentType

from apps.accounts.models import User
from apps.subscriptions.models import Subscription
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.services.hiddify import (
    HiddifyProvider,
    PlatformType,
)

from .base import BaseHandler

logger = logging.getLogger(__name__)


class SubscriptionHiddifyHandler(BaseHandler):
    """Enhanced subscription handler with Hiddify client features"""

    async def get_hiddify_provider(self) -> Optional[HiddifyProvider]:
        """Get Hiddify provider instance for this brand"""
        try:
            provider = await VPNProvider.objects.filter(
                brand=self.brand,
                provider_type=VPNProvider.ProviderType.HIDDIFY,
                status=VPNProvider.ProviderStatus.ACTIVE,
            ).afirst()

            if not provider:
                return None

            return HiddifyProvider(
                base_url=provider.base_url,
                api_key=provider.api_key,
                proxy_path=provider.proxy_path,
                public_api_key=provider.public_api_key,
            )
        except Exception as e:
            logger.error(f"Error getting Hiddify provider: {e}")
            return None

    async def get_user_secret_uuid(
        self, user: User, subscription: Subscription = None
    ) -> Optional[str]:
        """Get user's secret UUID from their active subscription"""
        try:
            if subscription:
                secret_uuid = subscription.connection_configs.get("secret_uuid")
                if secret_uuid:
                    return secret_uuid

                hiddify_uuid = subscription.connection_configs.get("hiddify_uuid")
                if hiddify_uuid:
                    return hiddify_uuid

                provider = await self.get_hiddify_provider()

                if provider:
                    try:
                        users = await provider.get_all_users()
                        if users:
                            for puser in users:
                                if puser.telegram_id == user.telegram_id:
                                    if not subscription.connection_configs:
                                        subscription.connection_configs = {}
                                    subscription.connection_configs["secret_uuid"] = (
                                        str(puser.uuid)
                                    )
                                    subscription.connection_configs["hiddify_uuid"] = (
                                        str(puser.uuid)
                                    )
                                    await subscription.asave()
                                    await provider.close()
                                    return str(puser.uuid)
                    except Exception as e:
                        logger.error(f"Error searching for user in Hiddify: {e}")
                    finally:
                        await provider.close()

            sub = (
                await Subscription.objects.filter(
                    user=user,
                    brand=self.brand,
                    status=Subscription.SubscriptionStatus.ACTIVE,
                )
                .select_related("vpn_provider")
                .afirst()
            )

            if sub and sub.vpn_provider:
                secret_uuid = sub.connection_configs.get("secret_uuid")
                if secret_uuid:
                    return secret_uuid

                hiddify_uuid = sub.connection_configs.get("hiddify_uuid")
                if hiddify_uuid:
                    return hiddify_uuid

        except Exception as e:
            logger.error(f"Error getting user secret UUID: {e}")
        return None

    async def show_my_subscriptions(self, callback: types.CallbackQuery):
        """Show user's subscriptions with Hiddify integration"""
        user, _ = await self.get_or_create_user(callback.from_user)

        subscriptions = []
        async for sub in (
            Subscription.objects.filter(user=user, brand=self.brand)
            .select_related("plan", "vpn_provider")
            .order_by("-created_at")
        ):
            subscriptions.append(sub)

        if not subscriptions:
            text = """
📱 اشتراک\u200cهای من

❌ شما هنوز هیچ اشتراکی ندارید.

برای خرید اشتراک از منوی اصلی استفاده کنید.
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
            active_subs = [s for s in subscriptions if s.status == "active"]
            text = f"""
📱 اشتراک\u200cهای من

✅ اشتراک\u200cهای فعال: {len(active_subs)}
📊 کل اشتراک\u200cها: {len(subscriptions)}

یک اشتراک را انتخاب کنید:
            """

            keyboard_buttons = []
            for sub in subscriptions[:5]:
                status_emoji = "🟢" if sub.status == "active" else "🔴"
                plan_name = sub.plan.name if sub.plan else "نامشخص"
                keyboard_buttons.append(
                    [
                        {
                            "text": f"{status_emoji} {plan_name}",
                            "callback_data": f"subscription_details_{sub.id}",
                        }
                    ]
                )

            keyboard_buttons.append(
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}]
            )
            keyboard = self.create_keyboard(keyboard_buttons)

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_subscription_details(
        self, callback: types.CallbackQuery, sub_id: int
    ):
        """Show detailed subscription information with Hiddify integration"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = (
                await Subscription.objects.filter(
                    id=sub_id, user=user, brand=self.brand
                )
                .select_related("plan", "vpn_provider")
                .afirst()
            )

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            text = f"""
📱 جزئیات اشتراک

📋 پلن: {sub.plan.name if sub.plan else "نامشخص"}
🆔 شناسه: {str(sub.subscription_id)[:8]}...
📊 وضعیت: {"🟢 فعال" if sub.status == "active" else "🔴 غیرفعال"}

📅 شروع: {sub.starts_at.strftime("%Y/%m/%d") if sub.starts_at else "نامشخص"}
📅 انقضا: {sub.expires_at.strftime("%Y/%m/%d") if sub.expires_at else "نامشخص"}
            """

            if sub.traffic_limit_gb:
                used_gb = float(sub.traffic_used_gb or 0)
                total_gb = sub.traffic_limit_gb
                percent = min(100, (used_gb / total_gb) * 100) if total_gb else 0
                text += f"""
📊 ترافیک:
• استفاده شده: {used_gb:.2f} GB
• کل: {total_gb} GB
• درصد: {percent:.1f}%
                """

            keyboard_buttons = [
                [
                    {
                        "text": "📱 دریافت کانفیگ",
                        "callback_data": f"get_config_{sub.id}",
                    },
                    {"text": "📊 آمار مصرف", "callback_data": f"usage_stats_{sub.id}"},
                ],
                [{"text": "📱 دانلود اپلیکیشن", "callback_data": f"get_apps_{sub.id}"}],
                [{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}],
            ]
            keyboard = self.create_keyboard(keyboard_buttons)

        except Exception as e:
            logger.error(f"Error showing subscription details: {e}")
            text = f"❌ خطا در بارگذاری اطلاعات: {e}"
            keyboard = self.get_back_keyboard("my_subscriptions")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def get_subscription_config(self, callback: types.CallbackQuery, sub_id: int):
        """Get VPN configurations from Hiddify"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = (
                await Subscription.objects.filter(
                    id=sub_id, user=user, brand=self.brand
                )
                .select_related("vpn_provider")
                .afirst()
            )

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = await self.get_user_secret_uuid(user, sub)

            if not secret_uuid:
                if sub.vpn_provider and sub.vpn_provider.provider_type == "hiddify":
                    await callback.answer(
                        "🔄 در حال همگام‌سازی با پنل...", show_alert=False
                    )

                    provider = await self.get_hiddify_provider()
                    if provider:
                        try:
                            users = await provider.get_all_users()
                            if users:
                                for puser in users:
                                    if puser.telegram_id == user.telegram_id or (
                                        str(user.telegram_id) in str(puser.name)
                                    ):
                                        if not sub.connection_configs:
                                            sub.connection_configs = {}
                                        sub.connection_configs["secret_uuid"] = str(
                                            puser.uuid
                                        )
                                        sub.connection_configs["hiddify_uuid"] = str(
                                            puser.uuid
                                        )
                                        await sub.asave()
                                        secret_uuid = str(puser.uuid)
                                        break
                        except Exception as e:
                            logger.error(f"Error syncing with Hiddify: {e}")
                        finally:
                            await provider.close()

                if not secret_uuid:
                    text = """
❌ خطا در دریافت کانفیگ

UUID کاربر در پنل یافت نشد.

لطفاً با پشتیبانی تماس بگیرید.
                    """
                    keyboard = self.create_keyboard(
                        [
                            [{"text": "📞 پشتیبانی", "callback_data": "support"}],
                            [
                                {
                                    "text": "🔙 بازگشت",
                                    "callback_data": f"subscription_details_{sub_id}",
                                }
                            ],
                        ]
                    )
                    await self.edit_message_with_keyboard(
                        callback.message.chat.id,
                        callback.message.message_id,
                        text,
                        keyboard,
                    )
                    await callback.answer("❌ خطا در یافتن UUID", show_alert=True)
                    return

            provider = await self.get_hiddify_provider()
            if not provider:
                await callback.answer("❌ پنل VPN در دسترس نیست", show_alert=True)
                return

            try:
                configs = await provider.get_user_configs(secret_uuid=secret_uuid)

                if not configs:
                    text = "❌ هیچ کانفیگی یافت نشد."
                    keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")
                else:
                    text = "📱 کانفیگ‌های VPN\n\nیکی از گزینه‌ها را انتخاب کنید:"

                    keyboard_buttons = []

                    for idx, cfg in enumerate(configs):
                        name = cfg.name or cfg.type or f"config-{idx}"
                        keyboard_buttons.append(
                            [
                                {
                                    "text": f"📱 {name}",
                                    "callback_data": f"show_config_{sub_id}_{idx}",
                                }
                            ]
                        )

                    keyboard_buttons.append(
                        [
                            {
                                "text": "🔙 بازگشت",
                                "callback_data": f"subscription_details_{sub_id}",
                            }
                        ]
                    )

                    keyboard = self.create_keyboard(keyboard_buttons)

            except Exception as e:
                text = f"❌ خطا در دریافت کانفیگ: {e}"
                keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")

            finally:
                await provider.close()

        except Exception as e:
            logger.error(f"Error getting subscription config: {e}")
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("my_subscriptions")
        if callback.message.content_type == ContentType.TEXT:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        else:
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )
        await callback.answer()

    async def show_config_by_protocol(
        self, callback: types.CallbackQuery, sub_id: int, idx: int
    ):
        """Show specific config by index (no re-fetch logic, uses cached configs)"""

        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return
            secret_uuid = await self.get_user_secret_uuid(user, sub)

            provider = await self.get_hiddify_provider()
            if not provider:
                await callback.answer("❌ پنل VPN در دسترس نیست", show_alert=True)
                return

            configs = await provider.get_user_configs(secret_uuid=secret_uuid)
            if not configs:
                await callback.answer("❌ کانفیگ‌ها در دسترس نیستند", show_alert=True)
                return

            if idx < 0 or idx >= len(configs):
                await callback.answer("❌ کانفیگ نامعتبر است", show_alert=True)
                return

            config = configs[idx]

            text = (
                """
    📱 کانفیگ VPN
"""
                + (f"""📋 نام: {config.name}\n""" if config.name else "")
                + (f"""🌐 دامنه: {config.domain}\n""" if config.domain else "")
                + (f"""🔌 پروتکل: {config.protocol}\n""" if config.protocol else "")
                + (f"""🔐 امنیت: {config.security}\n""" if config.security else "")
                + (f"""📡 ترنسپورت: {config.transport}\n""" if config.transport else "")
                + f"""
    🔗 لینک کانفیگ:
    <code>{config.link}</code>
)"""
                if config.link
                else ""
                """
    ⚠️ روی لینک برای کپی کلیک کنید.
    """
            ).strip()

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🖼 QR Code",
                            "callback_data": f"qr_{sub_id}_{idx}",
                        }
                    ],
                    [
                        {
                            "text": "🔙 بازگشت",
                            "callback_data": f"get_config_{sub_id}",
                        }
                    ],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard(f"get_config_{sub_id}")

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
            parse_mode="HTML",
        )

        await callback.answer()

    async def show_apps_menu(self, callback: types.CallbackQuery, sub_id: int):
        """Show VPN client apps for different platforms"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = sub.connection_configs.get("secret_uuid")
            provider = await self.get_hiddify_provider()

            if not provider:
                await self._show_static_apps(callback, sub_id)
                return

            try:
                text = """
📱 اپلیکیشن\u200cهای VPN

سیستم عامل خود را انتخاب کنید:
                """

                keyboard_buttons = [
                    [{"text": "🤖 اندروید", "callback_data": f"apps_android_{sub_id}"}],
                    [{"text": "🍎 iOS", "callback_data": f"apps_ios_{sub_id}"}],
                    [{"text": "🪟 ویندوز", "callback_data": f"apps_windows_{sub_id}"}],
                    [{"text": "🐧 لینوکس", "callback_data": f"apps_linux_{sub_id}"}],
                    [{"text": "🍎 مک", "callback_data": f"apps_mac_{sub_id}"}],
                    [
                        {
                            "text": "🔄 همه پلتفرم\u200cها",
                            "callback_data": f"apps_all_{sub_id}",
                        }
                    ],
                    [
                        {
                            "text": "🔙 بازگشت",
                            "callback_data": f"subscription_details_{sub_id}",
                        }
                    ],
                ]
                keyboard = self.create_keyboard(keyboard_buttons)

            finally:
                await provider.close()

        except Exception as e:
            logger.error(f"Error showing apps menu: {e}")
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_apps_by_platform(
        self, callback: types.CallbackQuery, sub_id: int, platform: str
    ):
        """Show recommended apps for a specific platform"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = sub.connection_configs.get("secret_uuid")
            provider = await self.get_hiddify_provider()

            if not provider or not secret_uuid:
                text = "❌ امکان دریافت لیست اپلیکیشن\u200cها وجود ندارد."
                keyboard = self.get_back_keyboard(f"get_apps_{sub_id}")
            else:
                try:
                    platform_enum = PlatformType(platform)
                    apps = await provider.get_user_apps(
                        platform=platform_enum, secret_uuid=secret_uuid
                    )

                    if not apps:
                        text = f"❌ هیچ اپلیکیشنی برای {platform} یافت نشد."
                    else:
                        platform_names = {
                            "android": "🤖 اندروید",
                            "ios": "🍎 iOS",
                            "windows": "🪟 ویندوز",
                            "linux": "🐧 لینوکس",
                            "mac": "🍎 مک",
                            "all": "🌐 همه",
                        }

                        text = f"""
📱 اپلیکیشن\u200cهای {platform_names.get(platform, platform)}

تعداد: {len(apps)} اپلیکیشن

                        """

                        for i, app in enumerate(apps[:5], 1):
                            text += f"{i}. {app.title}\n"
                            if app.description:
                                text += f"   {app.description[:50]}...\n"

                        keyboard_buttons = []
                        for app in apps[:5]:
                            for install in app.install[:1]:
                                keyboard_buttons.append(
                                    [{"text": f"📥 {app.title}", "url": install.url}]
                                )

                        keyboard_buttons.append(
                            [
                                {
                                    "text": "🔙 بازگشت",
                                    "callback_data": f"get_apps_{sub_id}",
                                }
                            ]
                        )
                        keyboard = self.create_keyboard(keyboard_buttons)

                except Exception as e:
                    text = f"❌ خطا: {e}"
                    keyboard = self.get_back_keyboard(f"get_apps_{sub_id}")

                finally:
                    await provider.close()

        except Exception as e:
            logger.error(f"Error showing apps: {e}")
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard(f"get_apps_{sub_id}")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def _show_static_apps(self, callback: types.CallbackQuery, sub_id: int):
        """Show static list of recommended VPN apps"""
        text = """
📱 اپلیکیشن\u200cهای پیشنهادی

🤖 اندروید:
• V2RayNG
• Hiddify Android
• NekoBox

🍎 iOS:
• Hiddify iOS
• V2Box
• Streisand

🪟 ویندوز:
• Hiddify Desktop
• v2rayN
• NekoRay

🐧 لینوکس:
• Hiddify AppImage
• Qv2ray

🍎 مک:
• Hiddify macOS
• V2Box macOS
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📥 دانلود Hiddify", "url": "https://hiddify.com/downloads"}],
                [
                    {
                        "text": "🔙 بازگشت",
                        "callback_data": f"subscription_details_{sub_id}",
                    }
                ],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_mtproxies(self, callback: types.CallbackQuery, sub_id: int):
        """Show MTProxy configurations"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = sub.connection_configs.get("secret_uuid")
            provider = await self.get_hiddify_provider()

            if not provider or not secret_uuid:
                text = "❌ امکان دریافت MTProxy وجود ندارد."
                keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")
            else:
                try:
                    mtproxies = await provider.get_user_mtproxies(
                        secret_uuid=secret_uuid
                    )

                    if not mtproxies:
                        text = "❌ هیچ MTProxy\u200cای یافت نشد."
                    else:
                        text = f"""
📱 MTProxy\u200cها

تعداد: {len(mtproxies)} پروکسی

                        """
                        for i, proxy in enumerate(mtproxies, 1):
                            text += f"{i}. {proxy.title}\n"
                            text += f"<code>{proxy.link}</code>\n\n"

                        text += "⚠️ روی لینک کلیک کنید تا کپی شود."

                    keyboard = self.create_keyboard(
                        [
                            [
                                {
                                    "text": "🔙 بازگشت",
                                    "callback_data": f"subscription_details_{sub_id}",
                                }
                            ],
                        ]
                    )

                except Exception as e:
                    text = f"❌ خطا: {e}"
                    keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")

                finally:
                    await provider.close()

        except Exception as e:
            logger.error(f"Error showing MTProxies: {e}")
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("my_subscriptions")

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
            parse_mode="HTML",
        )
        await callback.answer()

    async def show_usage_statistics(self, callback: types.CallbackQuery, sub_id: int):
        """Show usage statistics from Hiddify"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = (
                await Subscription.objects.filter(
                    id=sub_id, user=user, brand=self.brand
                )
                .select_related("plan")
                .afirst()
            )

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = sub.connection_configs.get("secret_uuid")
            provider = await self.get_hiddify_provider()

            if not provider or not secret_uuid:
                text = f"""
📊 آمار مصرف

📋 پلن: {sub.plan.name if sub.plan else "نامشخص"}
📊 وضعیت: {"🟢 فعال" if sub.status == "active" else "🔴 غیرفعال"}

📈 ترافیک استفاده شده: {float(sub.traffic_used_gb or 0):.2f} GB
📊 سقف ترافیک: {sub.traffic_limit_gb or "نامحدود"} GB

📅 روزهای باقیمانده: {sub.days_remaining or "نامحدود"}
                """
                keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")
            else:
                try:
                    profile = await provider.get_user_profile(secret_uuid=secret_uuid)

                    if profile:
                        used = profile.profile_usage_current
                        total = profile.profile_usage_total
                        days = profile.profile_remaining_days

                        percent = (used / total * 100) if total > 0 else 0
                        progress_bar = self._create_progress_bar(percent)

                        text = f"""
📊 آمار مصرف از پنل

📱 پروفایل: {profile.profile_title}
🌐 لینک: {profile.profile_url}

📈 مصرف: {used:.2f} / {total:.2f} GB
{progress_bar} {percent:.1f}%

📅 روزهای باقیمانده: {days}

🔗 زبان: {profile.lang.value.upper()}
⚡ تست سرعت: {"✅ فعال" if profile.speedtest_enable else "❌ غیرفعال"}
                        """
                    else:
                        text = "❌ اطلاعات پروفایل دریافت نشد."

                    keyboard = self.create_keyboard(
                        [
                            [
                                {
                                    "text": "🔄 بروزرسانی",
                                    "callback_data": f"usage_stats_{sub_id}",
                                }
                            ],
                            [
                                {
                                    "text": "🔙 بازگشت",
                                    "callback_data": f"subscription_details_{sub_id}",
                                }
                            ],
                        ]
                    )

                except Exception as e:
                    text = f"❌ خطا: {e}"
                    keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")

                finally:
                    await provider.close()

        except Exception as e:
            logger.error(f"Error showing usage stats: {e}")
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("my_subscriptions")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def get_short_url(self, callback: types.CallbackQuery, sub_id: int):
        """Get short URL for subscription"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = sub.connection_configs.get("secret_uuid")
            provider = await self.get_hiddify_provider()

            if not provider or not secret_uuid:
                text = "❌ امکان دریافت لینک کوتاه وجود ندارد."
                keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")
            else:
                try:
                    short = await provider.get_user_short_url(secret_uuid=secret_uuid)

                    if short:
                        text = f"""
🔗 لینک کوتاه اشتراک

📌 لینک:
<code>{short.short}</code>

🌐 لینک کامل:
<code>{short.full_url}</code>

⏰ انقضا: {short.expire_in // 60} دقیقه

⚠️ روی لینک کلیک کنید تا کپی شود.
                        """
                    else:
                        text = "❌ لینک کوتاه دریافت نشد."

                    keyboard = self.create_keyboard(
                        [
                            [
                                {
                                    "text": "🔗 دریافت لینک جدید",
                                    "callback_data": f"short_url_{sub_id}",
                                }
                            ],
                            [
                                {
                                    "text": "🔙 بازگشت",
                                    "callback_data": f"subscription_details_{sub_id}",
                                }
                            ],
                        ]
                    )

                except Exception as e:
                    text = f"❌ خطا: {e}"
                    keyboard = self.get_back_keyboard(f"subscription_details_{sub_id}")

                finally:
                    await provider.close()

        except Exception as e:
            logger.error(f"Error getting short URL: {e}")
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("my_subscriptions")

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
            parse_mode="HTML",
        )
        await callback.answer()

    def _create_progress_bar(self, percent: float, length: int = 10) -> str:
        """Create a text progress bar"""
        filled = int(percent / 100 * length)
        empty = length - filled
        return "█" * filled + "░" * empty

    def _format_bytes(self, bytes_val: float) -> str:
        """Format bytes to human readable format"""
        if bytes_val < 1024:
            return f"{bytes_val:.0f} B"
        elif bytes_val < 1024**2:
            return f"{bytes_val / 1024:.2f} KB"
        elif bytes_val < 1024**3:
            return f"{bytes_val / (1024**2):.2f} MB"
        elif bytes_val < 1024**4:
            return f"{bytes_val / (1024**3):.2f} GB"
        else:
            return f"{bytes_val / (1024**4):.2f} TB"

    async def copy_config(
        self, callback: types.CallbackQuery, sub_id: int, config_type: str
    ):
        """Copy config to clipboard (handled by Telegram client)"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = sub.connection_configs.get("secret_uuid")
            provider = await self.get_hiddify_provider()

            if not provider or not secret_uuid:
                await callback.answer("❌ خطا در دسترسی به کانفیگ", show_alert=True)
                return

            try:
                configs = await provider.get_user_configs(secret_uuid=secret_uuid)
                config = next(
                    (c for c in configs if c.protocol.lower() == config_type.lower()),
                    None,
                )

                if config:
                    await callback.answer(f"✅ کانفیگ {config_type.upper()} کپی شد!")
                else:
                    await callback.answer(f"❌ کانفیگ {config_type.upper()} یافت نشد")

            finally:
                await provider.close()

        except Exception as e:
            logger.error(f"Error copying config: {e}")
            await callback.answer("❌ خطا در کپی کانفیگ")

    async def download_config(
        self, callback: types.CallbackQuery, sub_id: int, config_type: str
    ):
        """Download config file"""

        try:
            user, _ = await self.get_or_create_user(callback.from_user)

            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = await self.get_user_secret_uuid(user, sub)

            if not secret_uuid:
                await callback.answer("❌ UUID یافت نشد", show_alert=True)
                return

            provider = await self.get_hiddify_provider()
            if not provider:
                await callback.answer("❌ پنل VPN در دسترس نیست", show_alert=True)
                return

            try:
                configs = await provider.get_user_configs(secret_uuid=secret_uuid)

                if not configs:
                    await callback.answer("❌ کانفیگ‌ها موجود نیستند", show_alert=True)
                    return

                matched = next(
                    (
                        c
                        for c in configs
                        if str(getattr(c, "protocol", "")).lower()
                        == config_type.lower()
                    ),
                    None,
                )

                if not matched:
                    await callback.answer("❌ کانفیگ پیدا نشد", show_alert=True)
                    return

                await callback.answer("📥 در حال آماده‌سازی فایل...")

            finally:
                await provider.close()

        except Exception as e:
            await callback.answer(f"❌ خطا: {e}", show_alert=True)

    async def show_qr_code(self, callback: types.CallbackQuery, sub_id: int, idx: int):
        """Show specific config by index (no re-fetch logic, uses cached configs)"""

        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return
            secret_uuid = await self.get_user_secret_uuid(user, sub)

            provider = await self.get_hiddify_provider()
            if not provider:
                await callback.answer("❌ پنل VPN در دسترس نیست", show_alert=True)
                return

            configs = await provider.get_user_configs(secret_uuid=secret_uuid)
            if not configs:
                await callback.answer("❌ کانفیگ‌ها در دسترس نیستند", show_alert=True)
                return

            if idx < 0 or idx >= len(configs):
                await callback.answer("❌ کانفیگ نامعتبر است", show_alert=True)
                return

            config = configs[idx]
            if config.link:
                file_name = str(uuid4()) + "_subscription_qr.png"
                qr_image = self.generate_qr_code(config.link)
                if qr_image:
                    keyboard = self.create_keyboard(
                        [
                            [
                                {
                                    "text": "🔙 بازگشت",
                                    "callback_data": f"subscription_details_{sub_id}",
                                }
                            ],
                        ]
                    )
                    await self.bot.send_photo(
                        callback.message.chat.id,
                        photo=types.BufferedInputFile(qr_image, filename=file_name),
                        caption="📱 QR Code لینک اشتراک",
                        reply_markup=keyboard,
                    )
            await callback.answer()

        except Exception as e:
            await callback.answer(f"❌ خطا: {e}", show_alert=True)

    def generate_qr_code(self, data: str) -> bytes:
        """Generate QR code image"""
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=1,
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

    async def send_config_file(self, callback: types.CallbackQuery, sub_id: int):
        """Send config file to user"""

        try:
            user, _ = await self.get_or_create_user(callback.from_user)

            sub = await Subscription.objects.filter(
                id=sub_id, user=user, brand=self.brand
            ).afirst()

            if not sub:
                await callback.answer("❌ اشتراک یافت نشد", show_alert=True)
                return

            secret_uuid = await self.get_user_secret_uuid(user, sub)

            if not secret_uuid:
                await callback.answer("❌ UUID یافت نشد", show_alert=True)
                return

            provider = await self.get_hiddify_provider()
            if not provider:
                await callback.answer("❌ پنل VPN در دسترس نیست", show_alert=True)
                return

            try:
                configs = await provider.get_user_configs(secret_uuid=secret_uuid)

                if not configs:
                    await callback.answer("❌ کانفیگ موجود نیست", show_alert=True)
                    return

                await callback.answer("📥 فایل کانفیگ در حال ارسال است...")

            finally:
                await provider.close()

        except Exception as e:
            await callback.answer(f"❌ خطا: {e}", show_alert=True)
