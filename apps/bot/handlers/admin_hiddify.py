"""
Enhanced Admin Handler with Hiddify Integration
Complete admin management for VPN panel and platform

Features:
- Hiddify panel admin management
- User management synced with panel
- Server monitoring and stats
- Real-time usage tracking
"""

import logging
from enum import Enum
from typing import Optional

from aiogram import types

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.orders.models import Order
from apps.subscriptions.models import Subscription
from apps.support.models import SupportTicket
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.services.hiddify import HiddifyAdmin as HiddifyAdminData
from apps.vpn_providers.services.hiddify import (
    HiddifyAdminMode,
    HiddifyLanguage,
    HiddifyProvider,
    HiddifyUser,
)

from .base import BaseHandler

logger = logging.getLogger(__name__)


class AdminRole(str, Enum):
    """Admin role levels"""

    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MODERATOR = "moderator"
    SUPPORT = "support"


class HiddifyAdminHandler(BaseHandler):
    """Enhanced admin handler with Hiddify panel integration"""

    async def check_admin_access(self, user: User) -> bool:
        """Check if user has admin access"""
        if user.is_staff or user.is_superuser:
            return True
        try:
            has_brand_admin = await user.admin_brands.filter(pk=self.brand.pk).aexists()
            return has_brand_admin
        except Exception as e:
            logger.error(f"Error checking admin access: {e}")
            return False

    async def check_admin_role(self, user: User) -> AdminRole:
        """Get user's admin role"""
        if user.is_superuser:
            return AdminRole.SUPER_ADMIN
        if user.is_staff:
            return AdminRole.ADMIN
        try:
            has_brand_admin = await user.admin_brands.filter(pk=self.brand.pk).aexists()
            if has_brand_admin:
                return AdminRole.ADMIN
        except Exception as e:
            logger.error(f"Error checking admin role: {e}")
        return None

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
                admin_uuid=provider.default_admin_uuid,
            )
        except Exception as e:
            logger.error(f"Error getting Hiddify provider: {e}")
            return None

    async def show_admin_menu(self, callback: types.CallbackQuery):
        """Show admin main menu with Hiddify integration"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ شما دسترسی ادمین ندارید")
            return

        role = await self.check_admin_role(user)
        role_name = {
            AdminRole.SUPER_ADMIN: "🔴 سوپر ادمین",
            AdminRole.ADMIN: "🟠 ادمین",
            AdminRole.MODERATOR: "🟡 مدیریت\u200cکننده",
            AdminRole.SUPPORT: "🟢 پشتیبانی",
        }.get(role, "نامشخص")

        has_hiddify = await VPNProvider.objects.filter(
            brand=self.brand, provider_type=VPNProvider.ProviderType.HIDDIFY
        ).aexists()

        text = f"""
🛠️ پنل مدیریت

👤 نام کاربری: {user.username}
📊 نقش: {role_name}
🏢 برند: {self.brand.name}
{"🌐 پنل Hiddify: متصل" if has_hiddify else "⚠️ پنل VPN: تنظیم نشده"}

چه کاری می\u200cخواهید انجام دهید؟
        """

        buttons = []

        if role in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            buttons.extend(
                [
                    [{"text": "📊 داشبورد", "callback_data": "admin_dashboard"}],
                    [
                        {
                            "text": "👥 مدیریت کاربران پنل",
                            "callback_data": "admin_panel_users",
                        }
                    ],
                    [
                        {
                            "text": "👤 مدیریت ادمین\u200cهای پنل",
                            "callback_data": "admin_panel_admins",
                        }
                    ],
                    [{"text": "📊 آمار سرور", "callback_data": "admin_server_status"}],
                    [
                        {
                            "text": "🛒 مدیریت سفارش\u200cها",
                            "callback_data": "admin_orders",
                        }
                    ],
                    [
                        {
                            "text": "🎫 تیکت\u200cهای پشتیبانی",
                            "callback_data": "admin_tickets",
                        }
                    ],
                    [
                        {
                            "text": "📢 ارسال پیام جمعی",
                            "callback_data": "admin_broadcast",
                        }
                    ],
                    [{"text": "⚙️ تنظیمات", "callback_data": "admin_settings"}],
                ]
            )

        elif role == AdminRole.MODERATOR:
            buttons.extend(
                [
                    [{"text": "📊 آمار", "callback_data": "admin_dashboard"}],
                    [{"text": "👥 کاربران", "callback_data": "admin_panel_users"}],
                    [{"text": "🛒 سفارش\u200cها", "callback_data": "admin_orders"}],
                ]
            )

        elif role == AdminRole.SUPPORT:
            buttons.extend(
                [
                    [{"text": "🎫 تیکت\u200cها", "callback_data": "admin_tickets"}],
                    [{"text": "👥 جستجوی کاربر", "callback_data": "admin_search_user"}],
                ]
            )

        buttons.append([{"text": "🔙 بازگشت", "callback_data": "main_menu"}])
        keyboard = self.create_keyboard(buttons)

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit admin menu: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_dashboard(self, callback: types.CallbackQuery):
        """Show admin dashboard with server stats"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        total_users = await User.objects.filter(brand=self.brand).acount()
        active_subs = await Subscription.objects.filter(
            brand=self.brand, status="active"
        ).acount()
        pending_orders = await Order.objects.filter(
            brand=self.brand, status="pending"
        ).acount()
        open_tickets = await SupportTicket.objects.filter(
            brand=self.brand, status="open"
        ).acount()

        server_status = None
        provider = await self.get_hiddify_provider()
        if provider:
            try:
                server_status = await provider.get_server_status()
            except Exception as e:
                logger.error(f"Error getting server status: {e}")
            finally:
                await provider.close()
                
        text = f"""
📊 داشبورد مدیریت

🎯 آمار کاربران:
• کل کاربران: {total_users}
• اشتراک\u200cهای فعال: {active_subs}
• سفارش\u200cهای در انتظار: {pending_orders}
• تیکت\u200cهای باز: {open_tickets}
"""

        if server_status:
            stats = server_status.get("stats")
            text += f"""
🖥️ وضعیت سرور:
• کاربران آنلاین: {server_status.get("usage_history", {}).get("m5", {}).get("online", "نامشخص")}
• استفاده CPU: {stats.get("system", {}).get("cpu_percent", "نامشخص")} / 100
• استفاده RAM: {stats.get("system", {}).get("ram_used", "نامشخص")} / {stats.get("system", {}).get("ram_total", "نامشخص")}
• کاربران فعال: {server_status.get("usage_history", {}).get("total", {}).get("online", "نامشخص")}
"""

        text += "\nچه کاری می\u200cخواهید انجام دهید؟"

        keyboard = self.create_keyboard(
            [
                [{"text": "🔄 بروزرسانی", "callback_data": "admin_dashboard"}],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit dashboard: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_server_status(self, callback: types.CallbackQuery):
        """Show detailed server status from Hiddify (clean UI)"""

        user, _ = await self.get_or_create_user(callback.from_user)
        if not await self.check_admin_access(user):
            await callback.answer("❌ دسترسی ندارید")
            return

        provider = await self.get_hiddify_provider()
        if not provider:
            text = "❌ پنل Hiddify برای این برند تنظیم نشده است."
            keyboard = self.get_back_keyboard("admin")
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
            await callback.answer()
            return

        try:
            status = await provider.get_server_status()
            panel_info = await provider.get_panel_info()

            system = (status or {}).get("stats", {}).get("system", {}) if status else {}
            usage = (status or {}).get("usage_history", {}) if status else {}

            def mb(x):
                return round(float(x), 2) if x is not None else 0

            def gb(x):
                return round(float(x) / 1024, 2) if x else 0

            text = f"""
    🖥️ <b>Hiddify Server Status</b>

    ━━━━━━━━━━━━━━
    📌 Panel
    • Version: <code>{panel_info.get("version", "N/A") if panel_info else "N/A"}</code>

    ━━━━━━━━━━━━━━
    💻 System
    • CPU Cores: {system.get("num_cpus", "N/A")}
    • Load (1m / 5m / 15m): {system.get("load_avg_1min", 0):.2f} / {system.get("load_avg_5min", 0):.2f} / {system.get("load_avg_15min", 0):.2f}

    ━━━━━━━━━━━━━━
    🧠 Memory
    • Used: {mb(system.get("ram_used"))} GB
    • Total: {mb(system.get("ram_total"))} GB

    ━━━━━━━━━━━━━━
    💾 Disk
    • Used: {mb(system.get("disk_used"))} GB
    • Total: {mb(system.get("disk_total"))} GB

    ━━━━━━━━━━━━━━
    🌐 Network
    • Sent: {system.get("bytes_sent_cumulative", 0)} bytes
    • Received: {system.get("bytes_recv_cumulative", 0)} bytes
    • Connections: {system.get("total_connections", 0)}
    • Unique IPs: {system.get("total_unique_ips", 0)}

    ━━━━━━━━━━━━━━
    📊 Usage
    • Today: {usage.get("today", {}).get("usage", 0)}
    • 24h Online: {usage.get("h24", {}).get("online", 0)}
    • Total Users: {usage.get("total", {}).get("users", 0)}
    """

            keyboard = self.create_keyboard(
                [
                    [{"text": "🔄 Refresh", "callback_data": "admin_server_status"}],
                    [
                        {
                            "text": "⚙️ Panel Settings",
                            "callback_data": "admin_panel_settings",
                        }
                    ],
                    [{"text": "🔙 Back", "callback_data": "admin"}],
                ]
            )

        except Exception as e:
            text = f"❌ Error fetching server status:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin")

        finally:
            try:
                await provider.close()
            except:
                pass

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_panel_admins_menu(self, callback: types.CallbackQuery):
        """Show Hiddify panel admins management"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        text = """
👤 مدیریت ادمین\u200cهای پنل Hiddify

از این بخش می\u200cتوانید ادمین\u200cهای پنل VPN را مدیریت کنید.
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "📋 لیست ادمین\u200cها",
                        "callback_data": "admin_list_panel_admins",
                    }
                ],
                [{"text": "➕ افزودن ادمین", "callback_data": "admin_add_panel_admin"}],
                [
                    {
                        "text": "🔍 جستجوی ادمین",
                        "callback_data": "admin_search_panel_admin",
                    }
                ],
                [
                    {
                        "text": "🔄 همگام\u200cسازی",
                        "callback_data": "admin_sync_panel_admins",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def list_panel_admins(self, callback: types.CallbackQuery):
        """List all panel admins from Hiddify"""
        user, _ = await self.get_or_create_user(callback.from_user)

        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            admins = await provider.get_all_admins()

            if not admins:
                text = "❌ هیچ ادمینی یافت نشد."
            else:
                text = f"📋 لیست ادمین\u200cها ({len(admins)} نفر):\n\n"

                for i, admin in enumerate(admins[:10], 1):
                    mode_emoji = {
                        HiddifyAdminMode.SUPER_ADMIN: "🔴",
                        HiddifyAdminMode.ADMIN: "🟠",
                        HiddifyAdminMode.AGENT: "🟡",
                    }.get(admin.mode, "⚪")

                    text += f"{i}. {mode_emoji} {admin.name}\n"
                    text += f"   نوع: {admin.get_mode_display()}\n"
                    if admin.telegram_id:
                        text += f"   تلگرام: {admin.telegram_id}\n"
                    text += "\n"

                if len(admins) > 10:
                    text += f"... و {len(admins) - 10} ادمین دیگر"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "➕ افزودن ادمین",
                            "callback_data": "admin_add_panel_admin",
                        }
                    ],
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_list_panel_admins",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_panel_admins"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا در دریافت لیست ادمین\u200cها: {e}"
            keyboard = self.get_back_keyboard("admin_panel_admins")

        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_add_panel_admin(self, callback: types.CallbackQuery):
        """Start process of adding a new panel admin"""
        user, _ = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "add_panel_admin", "step": "name"},
        )

        text = """
➕ افزودن ادمین جدید به پنل

لطفاً نام ادمین را وارد کنید:
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "❌ انصراف", "callback_data": "admin_panel_admins"}],
            ]
        )

        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_add_panel_admin_step(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle steps for adding a panel admin"""
        step = state.state_data.get("step")
        admin_data = state.state_data.get("admin_data", {})

        if step == "name":
            admin_data["name"] = message.text.strip()
            state.state_data["admin_data"] = admin_data
            state.state_data["step"] = "mode"
            await state.asave()

            text = """
نوع ادمین را انتخاب کنید:
            """
            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🔴 سوپر ادمین",
                            "callback_data": "admin_mode_super_admin",
                        }
                    ],
                    [{"text": "🟠 ادمین", "callback_data": "admin_mode_admin"}],
                    [{"text": "🟡 نماینده", "callback_data": "admin_mode_agent"}],
                    [{"text": "❌ انصراف", "callback_data": "admin_panel_admins"}],
                ]
            )
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "telegram_id":
            try:
                telegram_id = int(message.text.strip())
                admin_data["telegram_id"] = telegram_id
            except ValueError:
                await message.reply("❌ لطفاً یک عدد معتبر وارد کنید.")
                return

            state.state_data["admin_data"] = admin_data
            state.state_data["step"] = "can_add_admin"
            await state.asave()

            text = "آیا این ادمین می\u200cتواند ادمین دیگر اضافه کند؟"
            keyboard = self.create_keyboard(
                [
                    [{"text": "✅ بله", "callback_data": "admin_can_add_yes"}],
                    [{"text": "❌ خیر", "callback_data": "admin_can_add_no"}],
                ]
            )
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    async def handle_panel_admin_mode_selection(
        self, callback: types.CallbackQuery, mode: str
    ):
        """Handle admin mode selection"""
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)

        if not state.state_data:
            await callback.answer("❌ خطا در داده‌ها")
            return

        state.state_data.setdefault("admin_data", {})["mode"] = mode
        state.state_data["step"] = "telegram_id"
        await state.asave()

        text = """
شماره تلگرام ادمین را وارد کنید (یا 0 برای رد کردن):
        """
        keyboard = self.get_back_keyboard("admin_panel_admins")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_panel_admin_can_add(
        self, callback: types.CallbackQuery, can_add: bool
    ):
        """Handle can_add_admin selection and create admin"""
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)

        if not state.state_data or "admin_data" not in state.state_data:
            await callback.answer("❌ خطا در داده‌ها")
            return

        admin_data = state.state_data["admin_data"]
        admin_data["can_add_admin"] = can_add
        admin_data["lang"] = "fa"

        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            new_admin = HiddifyAdminData(
                name=admin_data["name"],
                mode=HiddifyAdminMode(admin_data["mode"]),
                lang=HiddifyLanguage.FA,
                can_add_admin=admin_data["can_add_admin"],
                telegram_id=(
                    admin_data.get("telegram_id")
                    if admin_data.get("telegram_id", 0) > 0
                    else None
                ),
            )

            created = await provider.create_admin(new_admin)

            if created:
                text = f"""
✅ ادمین با موفقیت ایجاد شد!

👤 نام: {created.name}
📋 نوع: {created.get_mode_display()}
🔑 UUID: {created.uuid}
                """
            else:
                text = "❌ خطا در ایجاد ادمین. لطفاً دوباره تلاش کنید."

        except Exception as e:
            text = f"❌ خطا: {e}"

        finally:
            await provider.close()

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "📋 لیست ادمین\u200cها",
                        "callback_data": "admin_list_panel_admins",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin_panel_admins"}],
            ]
        )

        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    async def show_panel_users_menu(self, callback: types.CallbackQuery):
        """Show panel users management menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        text = """
👥 مدیریت کاربران پنل Hiddify

از این بخش می\u200cتوانید کاربران VPN را مدیریت کنید.
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "📋 لیست کاربران",
                        "callback_data": "admin_list_panel_users",
                    }
                ],
                [{"text": "➕ افزودن کاربر", "callback_data": "admin_add_panel_user"}],
                [
                    {
                        "text": "🔍 جستجوی کاربر",
                        "callback_data": "admin_search_panel_user",
                    }
                ],
                [{"text": "📊 بروزرسانی مصرف", "callback_data": "admin_update_usage"}],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def list_panel_users(self, callback: types.CallbackQuery):
        """List all panel users from Hiddify"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            users = await provider.get_all_users()

            if not users:
                text = "❌ هیچ کاربری یافت نشد."
            else:
                text = f"📋 لیست کاربران ({len(users)} نفر):\n\n"

                for i, puser in enumerate(users[:10], 1):
                    status = "🟢" if puser.is_active and puser.enable else "🔴"
                    text += f"{i}. {status} {puser.name}\n"
                    if puser.usage_limit_GB:
                        used = puser.current_usage_GB or 0
                        text += f"   حجم: {used:.1f}/{puser.usage_limit_GB:.1f} GB\n"
                    if puser.package_days:
                        text += f"   روز: {puser.package_days}\n"
                    text += "\n"

                if len(users) > 10:
                    text += f"... و {len(users) - 10} کاربر دیگر"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "➕ افزودن کاربر",
                            "callback_data": "admin_add_panel_user",
                        }
                    ],
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_list_panel_users",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_panel_users"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا در دریافت لیست کاربران: {e}"
            keyboard = self.get_back_keyboard("admin_panel_users")

        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_add_panel_user(self, callback: types.CallbackQuery):
        """Start process of adding a new panel user"""
        user, _ = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "add_panel_user", "step": "name"},
        )

        text = """
➕ افزودن کاربر جدید به پنل

لطفاً نام کاربر را وارد کنید:
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "❌ انصراف", "callback_data": "admin_panel_users"}],
            ]
        )

        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def update_user_usage(self, callback: types.CallbackQuery):
        """Trigger user usage update in Hiddify"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            result = await provider.update_user_usage()

            if result:
                text = "✅ مصرف کاربران با موفقیت بروزرسانی شد."
            else:
                text = "❌ خطا در بروزرسانی مصرف کاربران."

        except Exception as e:
            text = f"❌ خطا: {e}"

        finally:
            await provider.close()

        keyboard = self.get_back_keyboard("admin_panel_users")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    def _format_bytes(self, bytes_val: int) -> str:
        """Format bytes to human readable format"""
        if bytes_val < 1024:
            return f"{bytes_val} B"
        elif bytes_val < 1024**2:
            return f"{bytes_val / 1024:.2f} KB"
        elif bytes_val < 1024**3:
            return f"{bytes_val / (1024**2):.2f} MB"
        elif bytes_val < 1024**4:
            return f"{bytes_val / (1024**3):.2f} GB"
        else:
            return f"{bytes_val / (1024**4):.2f} TB"

    async def show_users_management(self, callback: types.CallbackQuery):
        """Show user management menu"""
        await self.show_panel_users_menu(callback)

    async def show_orders_management(self, callback: types.CallbackQuery):
        """Show orders management menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        try:
            pending_count = await Order.objects.filter(
                brand=self.brand, status="pending"
            ).acount()
            completed_count = await Order.objects.filter(
                brand=self.brand, status="completed"
            ).acount()
            failed_count = await Order.objects.filter(
                brand=self.brand, status="failed"
            ).acount()

        except Exception as e:
            logger.error(f"Error fetching orders: {e}")
            await callback.answer("❌ خطا در بارگذاری سفارش\u200cها")
            return

        text = f"""
🛒 مدیریت سفارش\u200cها

📊 وضعیت سفارش\u200cها:
• در انتظار: {pending_count}
• تکمیل شده: {completed_count}
• ناموفق: {failed_count}

چه کاری می\u200cخواهید انجام دهید؟
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "⏳ سفارش\u200cهای در انتظار",
                        "callback_data": "admin_pending_orders",
                    }
                ],
                [
                    {
                        "text": "✅ سفارش\u200cهای تکمیل شده",
                        "callback_data": "admin_completed_orders",
                    }
                ],
                [
                    {
                        "text": "❌ سفارش\u200cهای ناموفق",
                        "callback_data": "admin_failed_orders",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_tickets_management(self, callback: types.CallbackQuery):
        """Show support tickets management"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        try:
            open_count = await SupportTicket.objects.filter(
                brand=self.brand, status="open"
            ).acount()
            in_progress_count = await SupportTicket.objects.filter(
                brand=self.brand, status="in_progress"
            ).acount()
            resolved_count = await SupportTicket.objects.filter(
                brand=self.brand, status="resolved"
            ).acount()

        except Exception as e:
            logger.error(f"Error fetching tickets: {e}")
            await callback.answer("❌ خطا در بارگذاری تیکت\u200cها")
            return

        text = f"""
🎫 مدیریت تیکت\u200cهای پشتیبانی

📊 وضعیت تیکت\u200cها:
• باز: {open_count}
• در حال انجام: {in_progress_count}
• حل شده: {resolved_count}
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🔴 تیکت\u200cهای باز",
                        "callback_data": "admin_open_tickets",
                    }
                ],
                [
                    {
                        "text": "🟡 تیکت\u200cهای در حال انجام",
                        "callback_data": "admin_in_progress_tickets",
                    }
                ],
                [
                    {
                        "text": "🟢 تیکت\u200cهای حل شده",
                        "callback_data": "admin_resolved_tickets",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_broadcast_menu(self, callback: types.CallbackQuery):
        """Show broadcast menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        role = await self.check_admin_role(user)

        if role not in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            await callback.answer("❌ این بخش فقط برای ادمین\u200cها است")
            return

        text = (
            "📢 ارسال پیام جمعی\n\nکدام گروه کاربران را می\u200cخواهید پیام ارسال کنید؟"
        )

        keyboard = self.create_keyboard(
            [
                [{"text": "👥 تمام کاربران", "callback_data": "admin_broadcast_all"}],
                [
                    {
                        "text": "🟢 کاربران فعال",
                        "callback_data": "admin_broadcast_active",
                    }
                ],
                [
                    {
                        "text": "🔴 کاربران غیرفعال",
                        "callback_data": "admin_broadcast_inactive",
                    }
                ],
                [
                    {
                        "text": "💰 کاربران با اشتراک",
                        "callback_data": "admin_broadcast_premium",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_settings(self, callback: types.CallbackQuery):
        """Show settings menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        role = await self.check_admin_role(user)

        if role not in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            await callback.answer("❌ این بخش فقط برای ادمین\u200cها است")
            return

        text = f"""
⚙️ تنظیمات

🏢 برند: {self.brand.name}
🔑 شناسه: {self.brand.slug}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📝 تنظیمات برند", "callback_data": "admin_edit_brand_info"}],
                [
                    {
                        "text": "💰 تنظیمات پرداخت",
                        "callback_data": "admin_payment_settings",
                    }
                ],
                [
                    {
                        "text": "🌐 تنظیمات پنل VPN",
                        "callback_data": "admin_panel_settings",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def request_search_username(self, callback: types.CallbackQuery):
        """Request admin to enter username for search"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user, BotState.StateType.MAIN_MENU, {"admin_action": "search_user"}
        )

        text = "🔍 جستجوی کاربر\n\nلطفاً نام کاربری یا شماره تلگرام کاربر را وارد کنید:"

        keyboard = self.get_back_keyboard("admin")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_admin_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle admin-related text messages"""
        action = state.state_data.get("action")
        step = state.state_data.get("step")

        if action == "add_panel_admin":
            await self.handle_add_panel_admin_step(message, user, state)
        elif action == "add_panel_user":
            await self.handle_add_panel_user_step(message, user, state)
        elif state.state_data.get("admin_action") == "search_user":
            query = message.text.strip()
            await message.reply(f"🔍 جستجو برای: {query}")
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    async def handle_add_panel_user_step(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle steps for adding a panel user"""
        step = state.state_data.get("step")
        user_data = state.state_data.get("user_data", {})

        if step == "name":
            user_data["name"] = message.text.strip()
            state.state_data["user_data"] = user_data
            state.state_data["step"] = "usage_limit"
            await state.asave()

            text = "حجم ترافیک به گیگابایت را وارد کنید (یا 0 برای نامحدود):"
            keyboard = self.get_back_keyboard("admin_panel_users")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "usage_limit":
            try:
                limit = float(message.text.strip())
                user_data["usage_limit_GB"] = limit if limit > 0 else None
            except ValueError:
                await message.reply("❌ لطفاً یک عدد معتبر وارد کنید.")
                return

            state.state_data["user_data"] = user_data
            state.state_data["step"] = "package_days"
            await state.asave()

            text = "تعداد روزهای اشتراک را وارد کنید (یا 0 برای نامحدود):"
            keyboard = self.get_back_keyboard("admin_panel_users")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "package_days":
            try:
                days = int(message.text.strip())
                user_data["package_days"] = days if days > 0 else None
            except ValueError:
                await message.reply("❌ لطفاً یک عدد معتبر وارد کنید.")
                return

            provider = await self.get_hiddify_provider()
            if not provider:
                await message.reply("❌ پنل Hiddify تنظیم نشده")
                return

            try:
                new_user = HiddifyUser(
                    name=user_data["name"],
                    usage_limit_GB=user_data.get("usage_limit_GB"),
                    package_days=user_data.get("package_days"),
                    enable=True,
                    is_active=True,
                )

                created = await provider.create_hiddify_user(new_user)

                if created:
                    text = f"""
✅ کاربر با موفقیت ایجاد شد!

👤 نام: {created.name}
🔑 UUID: {created.uuid}
📊 حجم: {created.usage_limit_GB or "نامحدود"} GB
📅 روزها: {created.package_days or "نامحدود"}
                    """
                else:
                    text = "❌ خطا در ایجاد کاربر. لطفاً دوباره تلاش کنید."

            except Exception as e:
                text = f"❌ خطا: {e}"

            finally:
                await provider.close()

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "📋 لیست کاربران",
                            "callback_data": "admin_list_panel_users",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_panel_users"}],
                ]
            )

            await self.send_message_with_keyboard(message.chat.id, text, keyboard)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
