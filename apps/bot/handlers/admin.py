"""
Admin Handler for Multi-Tenant VPN Bot
Handles admin dashboard and management functions with role-based access
"""

import logging
from enum import Enum

from aiogram import types

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.orders.models import Order, Wallet
from apps.subscriptions.models import Subscription
from apps.support.models import SupportTicket

from .base import BaseHandler

logger = logging.getLogger(__name__)


class AdminRole(str, Enum):
    """Admin role levels"""

    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MODERATOR = "moderator"
    SUPPORT = "support"


class AdminHandler(BaseHandler):
    """Handle admin operations and dashboard"""

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

    async def show_admin_menu(self, callback: types.CallbackQuery):
        """Show admin main menu"""
        user, _ = await self.get_or_create_user(callback.from_user)

        has_access = await self.check_admin_access(user)
        if not has_access:
            await callback.answer("❌ شما دسترسی ادمین ندارید")
            return

        role = await self.check_admin_role(user)
        role_name = {
            AdminRole.SUPER_ADMIN: "🔴 سوپر ادمین",
            AdminRole.ADMIN: "🟠 ادمین",
            AdminRole.MODERATOR: "🟡 مدیریت‌کننده",
            AdminRole.SUPPORT: "🟢 پشتیبانی",
        }.get(role, "نامشخص")

        text = f"""
🛠️ پنل مدیریت

👤 نام کاربری: {user.username}
📊 نقش: {role_name}
🏢 برند: {self.brand.name}

چه کاری می‌خواهید انجام دهید؟
        """

        buttons = []

        if role in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            buttons.extend(
                [
                    [{"text": "📊 داشبورد", "callback_data": "admin_dashboard"}],
                    [{"text": "👥 مدیریت کاربران", "callback_data": "admin_users"}],
                    [{"text": "🛒 مدیریت سفارش‌ها", "callback_data": "admin_orders"}],
                    [
                        {
                            "text": "🎫 مدیریت تیکت‌های پشتیبانی",
                            "callback_data": "admin_tickets",
                        }
                    ],
                    [
                        {
                            "text": "📢 ارسال پیام جمعی",
                            "callback_data": "admin_broadcast",
                        }
                    ],
                    [{"text": "⚙️ تنظیمات برند", "callback_data": "admin_settings"}],
                ]
            )

        elif role == AdminRole.MODERATOR:
            buttons.extend(
                [
                    [{"text": "📊 آمار", "callback_data": "admin_stats"}],
                    [{"text": "👥 کاربران", "callback_data": "admin_users"}],
                    [{"text": "🛒 سفارش‌ها", "callback_data": "admin_orders"}],
                    [{"text": "🎫 تیکت‌ها", "callback_data": "admin_tickets"}],
                ]
            )

        elif role == AdminRole.SUPPORT:
            buttons.extend(
                [
                    [
                        {
                            "text": "🎫 تیکت‌های پشتیبانی",
                            "callback_data": "admin_tickets",
                        }
                    ],
                    [{"text": "👥 جستجوی کاربر", "callback_data": "admin_search_user"}],
                    [
                        {
                            "text": "📊 آمار تیکت‌ها",
                            "callback_data": "admin_ticket_stats",
                        }
                    ],
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
        """Show admin dashboard with statistics"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        try:
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

            completed_orders = []
            async for order in Order.objects.filter(
                brand=self.brand, status="completed"
            ):
                completed_orders.append(order)
                if len(completed_orders) >= 100:
                    break

            total_revenue = sum(o.amount for o in completed_orders if o.amount)

        except Exception as e:
            logger.error(f"Error fetching dashboard stats: {e}")
            await callback.answer("❌ خطا در بارگذاری آمار")
            return

        text = f"""
📊 داشبورد مدیریت

🎯 آمار کلی:
• تعداد کاربران: {total_users}
• اشتراک‌های فعال: {active_subs}
• سفارش‌های در انتظار: {pending_orders}
• تیکت‌های باز: {open_tickets}

💰 مالی:
• کل درآمد: {self.format_price(total_revenue, self.brand.currency)}

📈 عملکرد:
• نرخ تبدیل: {(active_subs / max(total_users, 1) * 100):.1f}%
• میانگین سفارش: {self.format_price(total_revenue / max(len(completed_orders), 1), self.brand.currency)}
        """

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

    async def show_users_management(self, callback: types.CallbackQuery):
        """Show user management menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        text = """
👥 مدیریت کاربران

چه کاری می‌خواهید انجام دهید؟
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🔍 جستجوی کاربر", "callback_data": "admin_search_user"}],
                [{"text": "📊 آمار کاربران", "callback_data": "admin_user_stats"}],
                [{"text": "🔒 مسدود کردن کاربر", "callback_data": "admin_ban_user"}],
                [{"text": "🟢 فعال کردن کاربر", "callback_data": "admin_unban_user"}],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit users menu: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

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
            await callback.answer("❌ خطا در بارگذاری سفارش‌ها")
            return

        text = f"""
🛒 مدیریت سفارش‌ها

📊 وضعیت سفارش‌ها:
• در انتظار: {pending_count}
• تکمیل شده: {completed_count}
• ناموفق: {failed_count}

چه کاری می‌خواهید انجام دهید؟
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "⏳ سفارش‌های در انتظار",
                        "callback_data": "admin_pending_orders",
                    }
                ],
                [
                    {
                        "text": "✅ سفارش‌های تکمیل شده",
                        "callback_data": "admin_completed_orders",
                    }
                ],
                [
                    {
                        "text": "❌ سفارش‌های ناموفق",
                        "callback_data": "admin_failed_orders",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit orders menu: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
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
            await callback.answer("❌ خطا در بارگذاری تیکت‌ها")
            return

        text = f"""
🎫 مدیریت تیکت‌های پشتیبانی

📊 وضعیت تیکت‌ها:
• باز: {open_count}
• در حال انجام: {in_progress_count}
• حل شده: {resolved_count}

چه کاری می‌خواهید انجام دهید؟
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🔴 تیکت‌های باز", "callback_data": "admin_open_tickets"}],
                [
                    {
                        "text": "🟡 تیکت‌های در حال انجام",
                        "callback_data": "admin_in_progress_tickets",
                    }
                ],
                [
                    {
                        "text": "🟢 تیکت‌های حل شده",
                        "callback_data": "admin_resolved_tickets",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit tickets menu: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_broadcast_menu(self, callback: types.CallbackQuery):
        """Show broadcast (bulk messaging) menu"""
        user, _ = await self.get_or_create_user(callback.from_user)

        role = await self.check_admin_role(user)
        if role not in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            await callback.answer("❌ این بخش فقط برای ادمین‌ها است")
            return

        text = """
📢 ارسال پیام جمعی

کدام گروه کاربران را می‌خواهید پیام ارسال کنید؟
        """

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
                        "text": "🔴 کاربران نامفعال",
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

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit broadcast menu: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_settings(self, callback: types.CallbackQuery):
        """Show brand settings"""
        user, _ = await self.get_or_create_user(callback.from_user)

        role = await self.check_admin_role(user)
        if role not in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            await callback.answer("❌ این بخش فقط برای ادمین‌ها است")
            return

        text = f"""
⚙️ تنظیمات برند

🏢 برند: {self.brand.name}
🔑 شناسه: {self.brand.slug}
📞 شماره تماس: {self.brand.contact_email or "نامشخص"}

چه تنظیمی می‌خواهید تغییر دهید؟
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📝 درباره برند", "callback_data": "admin_edit_brand_info"}],
                [
                    {
                        "text": "💰 تنظیمات پرداخت",
                        "callback_data": "admin_payment_settings",
                    }
                ],
                [{"text": "📧 ایمیل‌های مهم", "callback_data": "admin_email_settings"}],
                [{"text": "🤖 تنظیمات بات", "callback_data": "admin_bot_settings"}],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit settings: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def request_search_username(self, callback: types.CallbackQuery):
        """Request admin to enter username for search"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user, BotState.StateType.MAIN_MENU, {"admin_action": "search_user"}
        )

        text = """
🔍 جستجوی کاربر

لطفاً نام کاربری یا شماره تلگرام کاربر را وارد کنید:
        """

        keyboard = self.get_back_keyboard("admin_users")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_admin_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle admin-related text messages"""
        admin_action = state.state_data.get("admin_action")

        if admin_action == "search_user":
            query = message.text.strip()

            try:
                search_user = None
                try:
                    search_user = await User.objects.aget(
                        username=query, brand=self.brand
                    )
                except User.DoesNotExist:
                    try:
                        search_user = await User.objects.aget(
                            telegram_id=int(query), brand=self.brand
                        )
                    except ValueError, User.DoesNotExist:
                        pass

                if not search_user:
                    await message.reply("❌ کاربر یافت نشد")
                    return

                subs_count = await Subscription.objects.filter(
                    user=search_user, brand=self.brand
                ).acount()

                try:
                    wallet = await Wallet.objects.aget(
                        user=search_user, brand=self.brand
                    )
                    wallet_balance = wallet.balance
                except Wallet.DoesNotExist:
                    wallet_balance = 0

                text = f"""
👤 اطلاعات کاربر

👨‍💼 نام: {search_user.full_name or "نامشخص"}
📱 نام کاربری: {search_user.username}
📞 تلگرام ID: {search_user.telegram_id}
📧 ایمیل: {search_user.email or "نامشخص"}
📅 تاریخ عضویت: {search_user.created_at.strftime("%Y/%m/%d") if search_user.created_at else "نامشخص"}

📊 وضعیت:
• اشتراک‌های فعال: {subs_count}
• موجودی کیف پول: {self.format_price(wallet_balance, self.brand.currency)}
• سطح: {search_user.level}
• امتیازات: {search_user.reward_points}

🔄 عملیات:
                """

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "🔒 مسدود کردن",
                                "callback_data": f"admin_ban_{search_user.id}",
                            }
                        ],
                        [
                            {
                                "text": "💳 افزودن اعتبار",
                                "callback_data": f"admin_add_credit_{search_user.id}",
                            }
                        ],
                        [
                            {
                                "text": "📧 ارسال پیام",
                                "callback_data": f"admin_send_msg_{search_user.id}",
                            }
                        ],
                        [{"text": "🔙 بازگشت", "callback_data": "admin_users"}],
                    ]
                )

                await self.send_message_with_keyboard(message.chat.id, text, keyboard)

            except Exception as e:
                logger.error(f"Error searching user: {e}")
                await message.reply("❌ خطایی در جستجو رخ داد")

            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
