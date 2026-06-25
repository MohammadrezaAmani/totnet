"""
Multi-Tenant Bot Dispatcher for VPN Platform
Manages multiple brand bots and routes messages appropriately
"""

import asyncio
import logging
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, Update
from aiohttp import web
from django.conf import settings

from apps.bot.handlers.admin import AdminHandler
from apps.bot.handlers.admin_hiddify import HiddifyAdminHandler
from apps.bot.handlers.base import BaseHandler
from apps.bot.handlers.help import HelpHandler
from apps.bot.handlers.profile import ProfileHandler
from apps.bot.handlers.purchase import PurchaseHandler, PurchaseStep
from apps.bot.handlers.referrals import ReferralsHandler
from apps.bot.handlers.rewards import RewardsHandler
from apps.bot.handlers.start import StartHandler
from apps.bot.handlers.stats import StatsHandler
from apps.bot.handlers.subscription_hiddify import SubscriptionHiddifyHandler
from apps.bot.handlers.subscriptions import SubscriptionHandler
from apps.bot.handlers.support import SupportHandler
from apps.bot.handlers.wallet import WalletHandler
from apps.bot.models import BotState
from apps.bot.services.broadcaster import BroadcastSubscriber
from apps.bot.services.telegram_sender import TelegramSender
from apps.brands.models import Brand

logger = logging.getLogger(__name__)


class MultiBrandDispatcher:
    """Manages multiple bot instances for different brands"""

    def __init__(self):
        self.brand_bots: Dict[str, Bot] = {}
        self.brand_dispatchers: Dict[str, Dispatcher] = {}
        self.brand_handlers: Dict[str, Dict[str, BaseHandler]] = {}

    async def initialize_brands(self):
        """Initialize bots for all active brands"""
        async for brand in Brand.objects.filter(
            status=Brand.BrandStatus.ACTIVE, bot_token__isnull=False
        ):
            await self.add_brand(brand)

    async def add_brand(self, brand: Brand):
        """Add a new brand bot"""
        try:
            bot = Bot(token=brand.bot_token)
            dp = Dispatcher()
            subscriber = BroadcastSubscriber(bot, brand.id)
            asyncio.create_task(subscriber.start_listening())
            handlers = {
                "start": StartHandler(bot, brand),
                "purchase": PurchaseHandler(bot, brand),
                "subscriptions": SubscriptionHandler(bot, brand),
                "subscription_hiddify": SubscriptionHiddifyHandler(bot, brand),
                "profile": ProfileHandler(bot, brand),
                "referrals": ReferralsHandler(bot, brand),
                "stats": StatsHandler(bot, brand),
                "support": SupportHandler(bot, brand),
                "wallet": WalletHandler(bot, brand),
                "rewards": RewardsHandler(bot, brand),
                "help": HelpHandler(bot, brand),
                "admin": AdminHandler(bot, brand),
                "admin_hiddify": HiddifyAdminHandler(bot, brand),
                "telegram_sender": TelegramSender(bot, brand),
                "subscriber": subscriber,
            }

            await self.setup_brand_routes(dp, brand, handlers)

            self.brand_bots[brand.slug] = bot

            self.brand_dispatchers[brand.slug] = dp
            self.brand_handlers[brand.slug] = handlers

            logger.info(f"Brand bot initialized: {brand.name} ({brand.slug})")

        except Exception as e:
            logger.error(f"Failed to initialize brand {brand.name}: {e}")

    async def setup_brand_routes(self, dp: Dispatcher, brand: Brand, handlers: dict):
        """Setup routes for a brand's bot"""
        router = Router()

        @router.message(CommandStart())
        async def start_command(message: Message, command: Command):
            await handlers["start"].handle_start_command(message, command)

        @router.message(Command("help"))
        async def help_command(message: Message):
            await handlers["start"].show_main_menu(
                message.chat.id,
                (await handlers["start"].get_or_create_user(message.from_user))[0],
            )

        @router.message(F.text)
        async def handle_text_messages(message: Message):
            user, _ = await handlers["start"].get_or_create_user(message.from_user)
            state = await handlers["start"].get_user_state(user)

            if state.current_state == "profile_setup":
                await handlers["start"].handle_profile_setup_message(
                    message, user, state
                )
            elif state.current_state == BotState.StateType.PROFILE_EDIT:
                await handlers["profile"].handle_profile_field_message(
                    message, user, state
                )
            elif state.current_state == BotState.StateType.SUPPORT_TICKET:
                step = state.state_data.get("step")
                if step == "subject":
                    await handlers["support"].handle_ticket_subject(
                        message, user, state
                    )
                elif step == "description":
                    await handlers["support"].handle_ticket_description(
                        message, user, state
                    )
            elif state.state_data.get("admin_action"):
                await handlers["admin_hiddify"].handle_admin_message(
                    message, user, state
                )
            elif state.state_data.get("action"):
                action = state.state_data.get("action")
                if action in ["add_panel_admin", "add_panel_user"]:
                    await handlers["admin_hiddify"].handle_admin_message(
                        message, user, state
                    )
                else:
                    await message.reply(
                        "لطفاً از منوی زیر استفاده کنید:",
                        reply_markup=await handlers["start"].get_main_menu_keyboard(
                            user
                        ),
                    )
            else:
                await message.reply(
                    "لطفاً از منوی زیر استفاده کنید:",
                    reply_markup=await handlers["start"].get_main_menu_keyboard(user),
                )

        @router.callback_query()
        async def handle_callbacks(callback: CallbackQuery):
            await self.route_callback(callback, handlers)

        @router.message(F.photo)
        async def handle_photo_messages(message: Message):
            """Handle photo messages (e.g., payment receipts)"""
            user, _ = await handlers["start"].get_or_create_user(message.from_user)
            state = await handlers["start"].get_user_state(user)

            if state.current_state == BotState.StateType.PAYMENT_PROCESS:
                step = (state.state_data or {}).get("step")
                if step == PurchaseStep.WAITING_RECEIPT:
                    await handlers["purchase"].handle_photo_message(message, state)
                    return

            await message.reply(
                "❌ در حال حاضر منتظر عکس نیستم.\nلطفاً از منوی زیر استفاده کنید:",
                reply_markup=await handlers["start"].get_main_menu_keyboard(user),
            )

        dp.include_router(router)

    async def route_callback(self, callback: CallbackQuery, handlers: dict):
        """Route callback queries to appropriate handlers"""
        try:
            data = callback.data

            if data == "main_menu":
                user, _ = await handlers["start"].get_or_create_user(callback.from_user)
                await handlers["start"].show_main_menu(
                    callback.message.chat.id, user, callback
                )

            elif data == "my_profile":
                await handlers["profile"].show_my_profile(callback)
            elif data == "edit_profile":
                await handlers["profile"].edit_profile(callback)
            elif data in ["edit_full_name", "edit_phone", "edit_email"]:
                field = data.replace("edit_", "")
                await handlers["profile"].request_field_update(callback, field)
            elif data in [
                "setup_profile",
                "skip_profile",
                "request_phone",
                "skip_phone",
            ]:
                await handlers["start"].handle_profile_setup_callback(callback)

            elif data == "referral_system":
                await handlers["referrals"].show_referral_menu(callback)
            elif data == "referral_stats":
                await handlers["referrals"].show_referral_stats(callback)
            elif data == "share_referral":
                await handlers["referrals"].share_referral_link(callback)
            elif data == "copy_referral_link":
                await handlers["referrals"].copy_referral_link(callback)
            elif data in [
                "referral_link",
                "share_referral_old",
            ]:
                await handlers["referrals"].show_referral_menu(callback)

            elif data == "purchase_subscription":
                await handlers["purchase"].show_subscription_plans(callback)
            elif data.startswith("select_plan_"):
                plan_id = int(data.split("_")[2])
                await handlers["purchase"].show_plan_details(callback, plan_id)
            elif data.startswith("purchase_plan_"):
                plan_id = int(data.split("_")[2])
                await handlers["purchase"].initiate_purchase(callback, plan_id)
            elif data.startswith("gift_plan_"):
                plan_id = int(data.split("_")[2])
                await handlers["purchase"].initiate_purchase(callback, plan_id, "gift")
            elif data.startswith("buy_for_other_"):
                plan_id = int(data.split("_")[3])
                await handlers["purchase"].initiate_purchase(callback, plan_id, "other")
            elif data.startswith("payment_done_"):
                parts = data.split("_")
                if len(parts) >= 2:
                    order_id = parts[2]
                    await handlers["purchase"].payment_done(callback, order_id)
            elif data.startswith("payment_not_done_"):
                parts = data.split("_")
                if len(parts) >= 3:
                    order_id = parts[3]
                    await handlers["purchase"].payment_not_done(callback, order_id)
            elif data.startswith("payment_"):
                parts = data.split("_")
                if len(parts) >= 3:
                    order_id = parts[3]
                    await handlers["purchase"].show_card_transfer_payment(
                        callback, order_id
                    )
            elif data.startswith("select_card_"):
                parts = data.split("_")
                if len(parts) >= 3:
                    order_id = parts[3]

                    await handlers["purchase"].show_card_transfer_payment(
                        callback, order_id
                    )

            elif data == "wallet":
                await handlers["wallet"].show_wallet(callback)
            elif data == "charge_wallet":
                await handlers["wallet"].show_charge_options(callback)
            elif data.startswith("charge_amount_"):
                parts = data.split("_")
                if len(parts) >= 3:
                    amount = int(parts[2])
                    await handlers["wallet"].initiate_charge(callback, amount)
            elif data == "wallet_history":
                await handlers["wallet"].show_wallet_history(callback)

            elif data == "my_subscriptions":
                await handlers["subscription_hiddify"].show_my_subscriptions(callback)
            elif data.startswith("subscription_details_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_subscription_details(
                    callback, sub_id
                )
            elif data.startswith("get_config_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].get_subscription_config(
                    callback, sub_id
                )
            elif data.startswith("usage_stats_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_usage_statistics(
                    callback, sub_id
                )
            elif data.startswith("renew_"):
                sub_id = int(data.split("_")[1])
                await handlers["subscription_hiddify"].show_subscription_details(
                    callback, sub_id
                )
            elif data.startswith("upgrade_"):
                sub_id = int(data.split("_")[1])
                await handlers["subscription_hiddify"].show_subscription_details(
                    callback, sub_id
                )
            elif data.startswith("transfer_"):
                sub_id = int(data.split("_")[1])
                await handlers["subscription_hiddify"].show_subscription_details(
                    callback, sub_id
                )
            elif data.startswith("copy_config_"):
                parts = data.split("_", 2)
                if len(parts) >= 3:
                    try:
                        config_type = parts[2].rsplit("_", 1)[0]
                        sub_id = int(parts[2].rsplit("_", 1)[1])
                        await handlers["subscription_hiddify"].copy_config(
                            callback, sub_id, config_type
                        )
                    except ValueError, IndexError:
                        logger.error(f"Error parsing copy_config callback: {data}")
                        await callback.answer("❌ خطا در پردازش درخواست")
            elif data.startswith("download_"):
                parts = data.split("_", 2)
                if len(parts) >= 3:
                    try:
                        config_type = parts[1]
                        sub_id = int(parts[2])
                        await handlers["subscription_hiddify"].download_config(
                            callback, sub_id, config_type
                        )
                    except ValueError, IndexError:
                        logger.error(f"Error parsing download callback: {data}")
                        await callback.answer("❌ خطا در پردازش درخواست")
            elif data.startswith("qr_codes_"):
                parts = data.split("_")
                if len(parts) >= 2:
                    try:
                        sub_id = int(parts[2])
                        await handlers["subscription_hiddify"].show_qr_codes(
                            callback, sub_id
                        )
                    except ValueError, IndexError:
                        logger.error(f"Error parsing qr_codes callback: {data}")
                        await callback.answer("❌ خطا در پردازش درخواست")
            elif data.startswith("config_file_"):
                parts = data.split("_")
                if len(parts) >= 2:
                    try:
                        sub_id = int(parts[2])
                        await handlers["subscription_hiddify"].send_config_file(
                            callback, sub_id
                        )
                    except ValueError, IndexError:
                        logger.error(f"Error parsing config_file callback: {data}")
                        await callback.answer("❌ خطا در پردازش درخواست")

            elif data.startswith("get_apps_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_apps_menu(callback, sub_id)
            elif data.startswith("apps_android_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_apps_by_platform(
                    callback, sub_id, "android"
                )
            elif data.startswith("apps_ios_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_apps_by_platform(
                    callback, sub_id, "ios"
                )
            elif data.startswith("apps_windows_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_apps_by_platform(
                    callback, sub_id, "windows"
                )
            elif data.startswith("apps_linux_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_apps_by_platform(
                    callback, sub_id, "linux"
                )
            elif data.startswith("apps_mac_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_apps_by_platform(
                    callback, sub_id, "mac"
                )
            elif data.startswith("apps_all_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_apps_by_platform(
                    callback, sub_id, "all"
                )

            elif data.startswith("show_config_"):
                sub_id = int(data.split("_")[2])
                idx = int(data.split("_")[3])
                await handlers["subscription_hiddify"].show_config_by_protocol(
                    callback, sub_id, idx
                )
            elif data.startswith("copy_vless_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].copy_config(
                    callback, sub_id, "vless"
                )
            elif data.startswith("copy_vmess_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].copy_config(
                    callback, sub_id, "vmess"
                )
            elif data.startswith("copy_trojan_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].copy_config(
                    callback, sub_id, "trojan"
                )
            elif data.startswith("copy_sub_link_"):
                sub_id = int(data.split("_")[3])
                await callback.answer("📋 لینک اشتراک کپی شد!")
            elif data.startswith("qr_"):
                sub_id = int(data.split("_")[1])
                idx = int(data.split("_")[2])
                await handlers["subscription_hiddify"].show_qr_code(
                    callback, sub_id, idx
                )

            elif data.startswith("mtproxies_"):
                sub_id = int(data.split("_")[1])
                await handlers["subscription_hiddify"].show_mtproxies(callback, sub_id)

            elif data.startswith("short_url_"):
                sub_id = int(data.split("_")[2])
                await handlers["subscription_hiddify"].get_short_url(callback, sub_id)

            elif data == "wallet":
                await self.show_wallet(callback, handlers["start"])

            elif data == "support":
                await handlers["support"].show_support_menu(callback)
            elif data == "create_ticket":
                await handlers["support"].show_create_ticket(callback)
            elif data.startswith("ticket_cat_"):
                parts = data.split("_")
                if len(parts) >= 3:
                    category_id = int(parts[2])
                    await handlers["support"].handle_ticket_category(
                        callback, category_id
                    )
            elif data == "my_tickets":
                await handlers["support"].show_my_tickets(callback)
            elif data == "faq":
                await handlers["support"].show_faq(callback)
            elif data.startswith("faq_article_"):
                parts = data.split("_")
                if len(parts) >= 3:
                    article_id = int(parts[2])
                    await handlers["support"].show_faq_article(callback, article_id)
            elif data.startswith("faq_helpful"):
                await callback.answer("✅ متشکریم برای نظر شما!")
            elif data == "contact_info":
                await handlers["support"].show_contact_info(callback)

            elif data == "rewards":
                await handlers["rewards"].show_rewards(callback)
            elif data == "how_to_earn":
                await handlers["rewards"].show_how_to_earn(callback)
            elif data == "leaderboard":
                await handlers["rewards"].show_leaderboard(callback)
            elif data == "upgrade_level":
                await callback.answer("🌟 امتیاز شما افزایش یافت!")

            elif data == "statistics":
                await handlers["stats"].show_statistics(callback)
            elif data == "detailed_stats":
                await handlers["stats"].show_detailed_stats(callback)
            elif data == "subscription_stats":
                await handlers["stats"].show_subscription_stats(callback)

            elif data == "help":
                await handlers["help"].show_help_menu(callback)
            elif data == "help_usage":
                await handlers["help"].show_usage_guide(callback)
            elif data == "about":
                await handlers["help"].show_about(callback)
            elif data == "terms":
                await handlers["help"].show_terms(callback)
            elif data == "privacy":
                await handlers["help"].show_privacy(callback)

            elif data == "admin":
                await handlers["admin_hiddify"].show_admin_menu(callback)
            elif data == "admin_dashboard":
                await handlers["admin_hiddify"].show_dashboard(callback)
            elif data == "admin_users":
                await handlers["admin_hiddify"].show_users_management(callback)
            elif data == "admin_orders":
                await handlers["admin_hiddify"].show_orders_management(callback)
            elif data == "admin_tickets":
                await handlers["admin_hiddify"].show_tickets_management(callback)
            elif data == "admin_broadcast":
                await handlers["admin_hiddify"].show_broadcast_menu(callback)
            elif data == "admin_settings":
                await handlers["admin_hiddify"].show_settings(callback)
            elif data == "admin_search_user":
                await handlers["admin_hiddify"].request_search_username(callback)
            elif data == "admin_stats":
                await handlers["admin_hiddify"].show_dashboard(callback)
            elif data == "admin_ticket_stats":
                await handlers["admin_hiddify"].show_tickets_management(callback)

            elif data == "admin_panel_admins":
                await handlers["admin_hiddify"].show_panel_admins_menu(callback)
            elif data == "admin_list_panel_admins":
                await handlers["admin_hiddify"].list_panel_admins(callback)
            elif data == "admin_add_panel_admin":
                await handlers["admin_hiddify"].start_add_panel_admin(callback)
            elif data == "admin_search_panel_admin":
                await callback.answer("🔍 جستجوی ادمین پنل")
            elif data == "admin_sync_panel_admins":
                await callback.answer("🔄 همگام‌سازی ادمین‌ها انجام شد")
            elif data.startswith("admin_mode_"):
                mode = data.replace("admin_mode_", "")
                await handlers["admin_hiddify"].handle_panel_admin_mode_selection(
                    callback, mode
                )
            elif data.startswith("admin_can_add_"):
                can_add = data == "admin_can_add_yes"
                await handlers["admin_hiddify"].handle_panel_admin_can_add(
                    callback, can_add
                )

            elif data == "admin_panel_users":
                await handlers["admin_hiddify"].show_panel_users_menu(callback)
            elif data == "admin_list_panel_users":
                await handlers["admin_hiddify"].list_panel_users(callback)
            elif data == "admin_add_panel_user":
                await handlers["admin_hiddify"].start_add_panel_user(callback)
            elif data == "admin_search_panel_user":
                await callback.answer("🔍 جستجوی کاربر پنل")
            elif data == "admin_update_usage":
                await handlers["admin_hiddify"].update_user_usage(callback)

            elif data == "admin_server_status":
                await handlers["admin_hiddify"].show_server_status(callback)
            elif data == "admin_panel_settings":
                await callback.answer("⚙️ تنظیمات پنل VPN")

            elif data in ["payment_methods_back", "back_to_plans"]:
                await handlers["purchase"].show_subscription_plans(callback)
            elif data in ["payment_methods_"]:
                await handlers["purchase"].show_subscription_plans(callback)

            else:
                logger.info(f"Unhandled callback: {data}")
                await callback.answer(f"⚙️ {data}")

        except ValueError as e:
            logger.error(f"ValueError handling callback {callback.data}: {e}")
            await callback.answer("❌ خطا در پردازش درخواست.")
        except Exception as e:
            logger.error(f"Error handling callback {callback.data}: {e}")
            await callback.answer("❌ خطا در پردازش درخواست.")

    async def show_wallet(self, callback: CallbackQuery, handler: BaseHandler):
        """Show wallet information"""
        user = await handler.get_or_create_user(callback.from_user)

        from apps.orders.models import Wallet

        try:
            wallet = await Wallet.objects.aget(user=user, brand=handler.brand)
        except Wallet.DoesNotExist:
            wallet = await Wallet.objects.acreate(
                user=user,
                brand=handler.brand,
                balance=0,
                currency=handler.brand.currency,
                is_active=True,
            )

        text = f"""
💰 کیف پول

موجودی فعلی: {handler.format_price(wallet.balance, wallet.currency)}
وضعیت: {"🟢 فعال" if wallet.is_active else "🔴 غیرفعال"}

برای شارژ کیف پول، از گزینه‌ی خرید اشتراک استفاده کنید.
        """

        keyboard = handler.create_keyboard(
            [
                [
                    {"text": "💳 شارژ کیف پول", "callback_data": "charge_wallet"},
                    {"text": "📊 تاریخچه", "callback_data": "wallet_history"},
                ],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit wallet message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_wallet_history(self, callback: CallbackQuery, handler: BaseHandler):
        """Show wallet transaction history"""
        user = await handler.get_or_create_user(callback.from_user)

        from apps.orders.models import Wallet

        try:
            wallet = await Wallet.objects.aget(user=user, brand=handler.brand)
        except Wallet.DoesNotExist:
            await callback.answer("❌ کیف پول یافت نشد.")
            return

        text = f"""
📊 تاریخچه کیف پول

موجودی فعلی: {handler.format_price(wallet.balance, wallet.currency)}

-traکنش‌های اخیر:
"""

        transactions = []
        async for txn in wallet.transactions.all()[:5]:
            transactions.append(txn)

        if transactions:
            for txn in transactions:
                icon = "➕" if txn.amount > 0 else "➖"
                text += f"{icon} {handler.format_price(abs(txn.amount), wallet.currency)} - {txn.description}\n"
        else:
            text += "❌ تراکنشی موجود نیست.\n"

        keyboard = handler.create_keyboard(
            [[{"text": "🔙 بازگشت", "callback_data": "wallet"}]]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit wallet history message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_support_menu(self, callback: CallbackQuery, handler: BaseHandler):
        """Show support menu"""
        text = f"""
🔧 پشتیبانی {handler.brand.name}

چگونه می‌توانیم به شما کمک کنیم؟
        """

        keyboard = handler.create_keyboard(
            [
                [
                    {"text": "🎫 ثبت تیکت", "callback_data": "create_ticket"},
                    {"text": "📋 تیکت‌های من", "callback_data": "my_tickets"},
                ],
                [
                    {"text": "❓ سوالات متداول", "callback_data": "faq"},
                    {"text": "📞 اطلاعات تماس", "callback_data": "contact_info"},
                ],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit support menu message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_create_ticket(self, callback: CallbackQuery, handler: BaseHandler):
        """Show ticket creation menu"""
        text = """
🎫 ثبت تیکت جدید

لطفاً موضوع تیکت خود را انتخاب کنید:
        """

        keyboard = handler.create_keyboard(
            [
                [
                    {"text": "🌐 مشکل در اتصال", "callback_data": "ticket_connection"},
                    {"text": "💳 مشکل در پرداخت", "callback_data": "ticket_payment"},
                ],
                [
                    {
                        "text": "📱 مشکل در اشتراک",
                        "callback_data": "ticket_subscription",
                    },
                    {"text": "📝 سایر مسائل", "callback_data": "ticket_other"},
                ],
                [{"text": "🔙 بازگشت", "callback_data": "support"}],
            ]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit create ticket message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def handle_ticket_creation(
        self, callback: CallbackQuery, handler: BaseHandler, ticket_type: str
    ):
        """Handle ticket creation based on type"""
        text = f"""
🎫 ثبت تیکت: {self.get_ticket_type_name(ticket_type)}

لطفاً توضیحات مشکل خود را وارد کنید:
        """

        await self.send_message_with_keyboard(
            callback.message.chat.id,
            text,
            handler.create_keyboard(
                [[{"text": "🔙 بازگشت", "callback_data": "create_ticket"}]]
            ),
        )
        await callback.answer()

    def get_ticket_type_name(self, ticket_type: str) -> str:
        """Get Persian name for ticket type"""
        types = {
            "connection": "مشکل در اتصال",
            "payment": "مشکل در پرداخت",
            "subscription": "مشکل در اشتراک",
            "other": "سایر مسائل",
        }
        return types.get(ticket_type, ticket_type)

    async def show_my_tickets(self, callback: CallbackQuery, handler: BaseHandler):
        """Show user's tickets"""
        text = """
📋 تیکت‌های من

شما هنوز هیچ تیکتی ندارید.

برای ثبت تیکت جدید از منوی بالا استفاده کنید.
        """

        keyboard = handler.create_keyboard(
            [
                [{"text": "🎫 ثبت تیکت جدید", "callback_data": "create_ticket"}],
                [{"text": "🔙 بازگشت", "callback_data": "support"}],
            ]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit my tickets message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_faq(self, callback: CallbackQuery, handler: BaseHandler):
        """Show frequently asked questions"""
        text = """
❓ سوالات متداول

در حال حاضر سوالات متداول در دسترس نیست.
        """

        keyboard = handler.create_keyboard(
            [[{"text": "🔙 بازگشت", "callback_data": "support"}]]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit FAQ message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_contact_info(self, callback: CallbackQuery, handler: BaseHandler):
        """Show contact information"""
        text = f"""
📞 اطلاعات تماس

برای ارتباط با پشتیبانی:
📧 ایمیل: {handler.brand.support_email or handler.brand.contact_email}
⏰ ساعت کاری: ۲۴/۷
        """

        keyboard = handler.create_keyboard(
            [[{"text": "🔙 بازگشت", "callback_data": "support"}]]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit contact info message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_rewards(self, callback: CallbackQuery, handler: BaseHandler):
        """Show rewards and achievements"""
        user = await handler.get_or_create_user(callback.from_user)

        text = f"""
🎁 جایزه‌ها و امتیازات

امتیازات شما: {user.reward_points}
سطح فعلی: {user.level}

🏆 سطوح:
- سطح ۱: {user.referral_count} معرفی
- سطح ۲: ۱۰ معرفی
- سطح ۳: ۲۵ معرفی
- سطح ۴: ۵۰ معرفی
- سطح ۵: ۱۰۰ معرفی

        """

        keyboard = handler.create_keyboard(
            [
                [{"text": "📈 ارتقا سطح", "callback_data": "upgrade_level"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit rewards message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_statistics(self, callback: CallbackQuery, handler: BaseHandler):
        """Show user statistics"""
        user = await handler.get_or_create_user(callback.from_user)

        from apps.subscriptions.models import Subscription

        subscription_count = await Subscription.objects.filter(
            user=user, brand=handler.brand, status="active"
        ).acount()

        text = f"""
📊 آمار شما

👤 نام کاربری: {user.username}
📱 تلفن: {user.phone_number or "ثبت نشده"}
📅 تاریخ عضویت: {user.created_at.strftime("%Y/%m/%d")}

اشتراک‌های فعال: {subscription_count}
معرفی شده توسط: {user.referred_by.username if user.referred_by else "خودتان"}
تعداد معرفی‌ها: {user.referral_count}
امتیازات: {user.reward_points}
سطح: {user.level}
        """

        keyboard = handler.create_keyboard(
            [
                [{"text": "📈 آمار جامع‌تر", "callback_data": "detailed_stats"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit statistics message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_detailed_stats(self, callback: CallbackQuery, handler: BaseHandler):
        """Show detailed user statistics"""
        user = await handler.get_or_create_user(callback.from_user)

        text = f"""
📈 آمار جامع شما

� نام کاربری: {user.username}
📧 ایمیل: {user.email or "ثبت نشده"}
📱 تلفن: {user.phone_number or "ثبت نشده"}
📅 تاریخ عضویت: {user.created_at.strftime("%Y/%m/%d")}
📍 شهر: {user.userprofile.city if hasattr(user, "userprofile") else "ثبت نشده"}

💰 کیف پول: {handler.format_price(user.wallet_balance, handler.brand.currency)}
🎁 امتیازات: {user.reward_points}
🏆 سطح: {user.level}

🛒 خریدهای اخیر: {user.total_purchases}
💸 مبلغ کل خرید: {handler.format_price(user.total_spent, handler.brand.currency)}

👥 معرفی‌ها: {user.referral_count}
        """

        keyboard = handler.create_keyboard(
            [[{"text": "🔙 بازگشت", "callback_data": "statistics"}]]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit detailed stats message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_marketing_tools(self, callback: CallbackQuery, handler: BaseHandler):
        """Show marketing tools for referrals"""
        user = await handler.get_or_create_user(callback.from_user)

        bot_username = (
            handler.brand.bot_username or handler.bot._me.username
            if handler.bot._me
            else handler.brand.slug
        )
        referral_url = f"https://t.me/{bot_username}?start={user.referral_code}"

        text = f"""
📣 ابزارهای بازاریابی

لینک معرفی شما:
{referral_url}

از این لینک برای معرفی دوستان خود استفاده کنید.
        """

        keyboard = handler.create_keyboard(
            [
                [{"text": "📤 اشتراک‌گذاری", "callback_data": "share_marketing"}],
                [{"text": "🔙 بازگشت", "callback_data": "referral_system"}],
            ]
        )

        try:
            await handler.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit marketing tools message: {e}")
            await handler.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def get_brand_from_token(self, token: str) -> Optional[Brand]:
        """Get brand by bot token"""
        try:
            return await Brand.objects.aget(bot_token=token)
        except Brand.DoesNotExist:
            return None

    async def process_webhook(self, token: str, update_data: dict):
        """Process webhook update for specific brand"""
        brand = await self.get_brand_from_token(token)
        if not brand or brand.slug not in self.brand_dispatchers:
            logger.warning(f"Unknown token in webhook: {token}")
            return

        try:
            update = Update(**update_data)
            bot = self.brand_bots[brand.slug]
            dp = self.brand_dispatchers[brand.slug]

            await dp.feed_webhook_update(bot, update)

        except Exception as e:
            logger.error(f"Error processing webhook for {brand.name}: {e}")

    async def start_polling(self):
        """Start polling for all brand bots"""
        tasks = []
        for brand_slug, dp in self.brand_dispatchers.items():
            bot = self.brand_bots[brand_slug]
            task = asyncio.create_task(dp.start_polling(bot))
            tasks.append(task)
            logger.info(f"Started polling for brand: {brand_slug}")

        if tasks:
            await asyncio.gather(*tasks)
        else:
            logger.warning("No bots to poll!")

    async def setup_webhooks(self):
        """Setup webhooks for all brands"""
        for brand_slug, bot in self.brand_bots.items():
            try:
                brand = await Brand.objects.aget(slug=brand_slug)
                if brand.webhook_url:
                    webhook_url = f"{brand.webhook_url}/bot/{brand.bot_token}"
                    await bot.set_webhook(webhook_url)
                    logger.info(f"Webhook set for {brand.name}: {webhook_url}")
            except Exception as e:
                logger.error(f"Failed to set webhook for {brand_slug}: {e}")


multi_dispatcher = MultiBrandDispatcher()


async def webhook_handler(request):
    """Handle incoming webhooks"""
    token = request.match_info["token"]
    update_data = await request.json()

    await multi_dispatcher.process_webhook(token, update_data)
    return web.Response(status=200)


def create_webhook_app():
    """Create aiohttp app for webhooks"""
    app = web.Application()
    app.router.add_post("/bot/{token}", webhook_handler)
    return app


async def start_bots():
    """Initialize and start all brand bots"""
    await multi_dispatcher.initialize_brands()

    if settings.USE_WEBHOOK:
        await multi_dispatcher.setup_webhooks()
        logger.info("Bots configured for webhook mode")
    else:
        await multi_dispatcher.start_polling()
        logger.info("Bots started in polling mode")
