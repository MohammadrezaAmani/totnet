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

from apps.subscriptions.models import (
    Subscription,
    SubscriptionClaim,
    SubscriptionConfig,
    SubscriptionPlan,
    HiddenSubscription,
)
from apps.vpn_providers.models import VPNProvider
from apps.subscriptions.usage import remaining_usage, is_subscription_expired
from apps.subscriptions.presentation import compact_plan_specs, number
from apps.subscriptions.policy import temporary_duration_blocked
from apps.subscriptions.catalog import get_cached_plans

from .base import BaseHandler
from utils.jalali import jalali_date
from utils.display_numbers import format_amount

logger = logging.getLogger(__name__)


class SubscriptionHandler(BaseHandler):
    """Handle subscription management and delivery"""

    async def show_my_subscriptions(self, callback: types.CallbackQuery, page: int = 1):
        """List subscriptions; selecting one opens its details (not all renewal plans)."""
        user, _ = await self.get_or_create_user(callback.from_user)
        queryset = Subscription.objects.filter(Q(user=user) | Q(owner=user), brand=self.brand).exclude(hidden_for_users__user=user)
        total = await queryset.acount()
        per_page = 8
        last_page = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, last_page))
        subscriptions = []
        async for sub in (
            Subscription.objects.filter(Q(user=user) | Q(owner=user), brand=self.brand)
            .select_related("plan", "owner", "user", "vpn_provider", "remote_account")
            .exclude(hidden_for_users__user=user)
            .order_by("-created_at")[(page - 1) * per_page:page * per_page]
        ):
            subscriptions.append(sub)

        text = "📱 <b>اشتراک‌های من</b>"
        if not subscriptions:
            rows = [
                [{"text": "🛒 خرید اشتراک", "callback_data": "purchase_subscription"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        else:
            rows = []
            category_labels = {
                SubscriptionPlan.ServiceCategory.NORMAL: "نرمال",
                SubscriptionPlan.ServiceCategory.ROYAL: "رویال",
                SubscriptionPlan.ServiceCategory.IRAN_IP: "آی‌پی ایران",
            }
            for sub in subscriptions:
                username = sub.connectix_username or sub.vpn_user_email or str(sub.subscription_id)[:8]
                service_type = category_labels.get(sub.plan.service_category, sub.plan.name)
                if sub.plan.plan_type == SubscriptionPlan.PlanType.UNLIMITED:
                    volume = "نامحدود"
                else:
                    volume = (
                        f"{number(sub.plan.traffic_limit_gb)}گ"
                        if sub.plan.traffic_limit_gb is not None else "—"
                    )
                label = f"{service_type} {volume} {sub.plan.max_users}کاربر _ {username}"
                expired = is_subscription_expired(sub)
                if expired:
                    label = f"{label} (منقضی شده · تمدید/حذف)"
                if len(label) > 62:
                    label = label[:48] + "…" + (" (منقضی شده)" if expired else "")
                rows.append([{"text": label, "callback_data": f"subscription_details_{sub.pk}",
                              **({"style": "danger"} if expired else {})}])
            if last_page > 1:
                rows.append([
                    {"text": label, "callback_data": f"subscriptions_page_{target}"}
                    for target, label in ((page - 1, "◀️ قبلی"), (page + 1, "بعدی ▶️"))
                    if 1 <= target <= last_page
                ])
            rows.extend(
                [
                    [{"text": "🛒 خرید اشتراک جدید", "callback_data": "purchase_subscription"}],
                    [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
                ]
            )

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def show_subscription_claim(
        self, callback: types.CallbackQuery, claim_id: int
    ):
        """Show the review state for a safely-submitted legacy subscription claim."""
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            claim = await SubscriptionClaim.objects.aget(
                pk=claim_id, user=user, brand=self.brand
            )
        except SubscriptionClaim.DoesNotExist:
            await callback.answer("❌ درخواست ثبت اشتراک یافت نشد.", show_alert=True)
            return

        status_text = {
            SubscriptionClaim.Status.PENDING: "⏳ در انتظار بررسی",
            SubscriptionClaim.Status.APPROVED: "✅ تأیید شده",
            SubscriptionClaim.Status.REJECTED: "❌ رد شده",
        }.get(claim.status, claim.status)
        note = ""
        if claim.admin_note:
            note = f"\n\n📝 توضیح بررسی: {escape(claim.admin_note[:800])}"
        text = (
            "📎 <b>ثبت اشتراک فعال</b>\n\n"
            f"👤 یوزرنیم: <code>{escape(claim.username)}</code>\n"
            f"📊 وضعیت: {status_text}"
            f"{note}\n\n"
            "برای امنیت، مالکیت هیچ اشتراکی فقط با دانستن یوزرنیم منتقل نمی‌شود."
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(
                [
                    [{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}],
                ]
            ),
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
                    remaining = (
                        f" • {days_left} روز" if days_left > 0 else " • منقضی"
                    )
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
            rows.append(
                [{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}]
            )

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def show_renewal_page(self, callback: types.CallbackQuery):
        """Legacy callback: the standalone renewal page was removed."""
        await self.show_my_subscriptions(callback)

    async def show_renewal_details(
        self, callback: types.CallbackQuery, subscription_id: int, page: int = 1
    ):
        """Continue checkout using this subscription's original plan only."""
        from .purchase import PurchaseHandler

        await PurchaseHandler(self.bot, self.brand).repurchase_subscription(
            callback, subscription_id
        )

    async def start_rename_subscription(self, callback, subscription_id: int):
        from apps.bot.models import BotState
        user, _ = await self.get_or_create_user(callback.from_user)
        allowed = await Subscription.objects.filter(
            Q(user=user) | Q(owner=user), brand=self.brand, pk=subscription_id
        ).aexists()
        if not allowed:
            await callback.answer("❌ اشتراک پیدا نشد.", show_alert=True)
            return
        await self.update_user_state(
            user, BotState.StateType.SUBSCRIPTION_MANAGEMENT,
            {"step": "rename_subscription", "subscription_id": subscription_id},
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id,
            "✏️ نام دلخواه اشتراک را ارسال کنید (۱ تا ۴۰ نویسه).",
            self.get_back_keyboard(f"subscription_details_{subscription_id}"),
        )
        await callback.answer()

    async def handle_rename_message(self, message, user, state):
        from apps.bot.models import BotState
        name = (message.text or "").strip()
        if not 1 <= len(name) <= 40 or any(ord(ch) < 32 for ch in name):
            await message.reply("❌ نام اشتراک باید ۱ تا ۴۰ حرف باشد.")
            return
        subscription_id = (state.state_data or {}).get("subscription_id")
        count = await Subscription.objects.filter(
            Q(user=user) | Q(owner=user), brand=self.brand, pk=subscription_id
        ).aupdate(display_name=name)
        if not count:
            await message.reply("❌ این اشتراک در دسترس نیست.")
            return
        await self._remove_user_input(message)
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        await self.send_message_with_keyboard(
            message.chat.id,
            f"✅ نام اشتراک در ربات به «{escape(name)}» تغییر کرد.",
            self.create_keyboard([
                [{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]),
        )

    async def show_subscription_details(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Show detailed subscription information"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related("plan", "vpn_provider", "owner", "user", "remote_account")
                .filter(Q(user=user) | Q(owner=user))
                .exclude(hidden_for_users__user=user)
                .aget(id=subscription_id, brand=self.brand)
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
            return

        category_names = {
            SubscriptionPlan.ServiceCategory.NORMAL: "نرمال",
            SubscriptionPlan.ServiceCategory.ROYAL: "رویال",
            SubscriptionPlan.ServiceCategory.IRAN_IP: "آی‌پی ایران",
        }
        service = category_names.get(subscription.plan.service_category, "سایر")
        traffic, days = remaining_usage(subscription)
        name = (subscription.display_name or "").strip()
        shown_name = escape(name) if name else "از منو پایین نام اشتراک را تعیین کنید،مثلا (رویال خودم)"
        specs = compact_plan_specs(subscription.plan)
        text = (
            "📱 <b>جزئیات اشتراک</b>\n\n"
            f"🏷️ نام اشتراک: {shown_name}\n"
            f"🩺 نوع سرویس: {escape(service)}\n"
            f"📋 نوع اشتراک: {escape(service)} {escape(specs)}\n"
            f"📦 حجم باقی‌مانده: {escape(traffic)}\n"
            f"⏳ زمان باقی‌مانده: {escape(days)}"
        )

        keyboard_buttons = []

        if subscription.status == Subscription.SubscriptionStatus.ACTIVE:
            keyboard_buttons.extend(
                [
                    [
                        {
                            "text": "📥 دریافت کانفیگ",
                            "callback_data": f"get_config_{subscription_id}",
                        },
                    ],
                ]
            )
        if is_subscription_expired(subscription):
            keyboard_buttons.append([
                {"text": "🔄 تمدید", "callback_data": f"renewal_details_{subscription_id}"},
                {"text": "🗑 حذف", "callback_data": f"hide_subscription_{subscription_id}",
                 "style": "danger"},
            ])
        if subscription.status != Subscription.SubscriptionStatus.CANCELLED and not is_subscription_expired(subscription):
            keyboard_buttons.append(
                [
                    {
                        "text": "🔄 تمدید / خرید مجدد",
                        "callback_data": f"renewal_details_{subscription_id}",
                    }
                ]
            )
        keyboard_buttons.append([{"text": "✏️ نام‌گذاری اشتراک", "callback_data": f"rename_subscription_{subscription.pk}", "style": "success"}])
        keyboard_buttons.append([{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}])

        keyboard = self.create_keyboard(keyboard_buttons)

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def hide_expired_subscription(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        user, _ = await self.get_or_create_user(callback.from_user)
        subscription = await (
            Subscription.objects.select_related("vpn_provider", "remote_account")
            .filter(Q(user=user) | Q(owner=user), brand=self.brand, pk=subscription_id)
            .afirst()
        )
        if subscription is None or not is_subscription_expired(subscription):
            await callback.answer("❌ امکان حذف این اشتراک وجود ندارد.", show_alert=True)
            return
        await HiddenSubscription.objects.aget_or_create(
            user=user, subscription=subscription
        )
        await self.show_my_subscriptions(callback)

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
            from apps.bot.handlers.subscription_hiddify import SubscriptionHiddifyHandler

            await SubscriptionHiddifyHandler(self.bot, self.brand).get_subscription_config(
                callback, subscription_id
            )
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

        name = escape(subscription.display_name or "نام‌گذاری نشده")
        username = escape(subscription.connectix_username or "—")
        password = escape(subscription.connectix_password or "—")
        text = (
            "📱 <b>اطلاعات اشتراک</b>\n\n"
            f"🏷 نام اشتراک: {name}\n"
            f"👤 نام کاربری: <code>{username}</code>\n"
            f"🔑 رمز عبور: <code>{password}</code>\n"
            "با این نام کاربری و رمز عبور وارد برنامه ما،کانکتیکس شوید و به اشتراکتون وصل بشید\n\n"
            f"🔗 لینک اشتراک V2Ray:\n<code>{escape(subscription_url)}</code>"
        )
        keyboard = self.create_keyboard([
            [{"text": "✏️ نام‌گذاری این اشتراک", "callback_data": f"rename_subscription_{subscription.pk}", "style": "success"}],
            [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            [{"text": "📚 آکادمی", "callback_data": "useful_content"}],
            [{"text": "🧩 نحوه وارد کردن اشتراک", "callback_data": "useful_category_install"}],
            [{"text": "📥 دانلود و نصب برنامه", "callback_data": "useful_category_downloads"}],
        ])
        qr_image = self.generate_qr_code(subscription_url)
        if qr_image:
            try:
                await self.bot.send_photo(
                    callback.message.chat.id,
                    photo=BufferedInputFile(qr_image, filename="qr_code.png"),
                    caption="📱 QR Code اتصال",
                )
            except Exception:
                logger.exception("QR delivery failed for subscription %s", subscription.pk)
        await self.bot.send_message(
            callback.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard
        )
        await callback.answer()

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
                [{"text": "✏️ نام‌گذاری این اشتراک", "callback_data": f"rename_subscription_{subscription.pk}", "style": "success"}],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
                [{"text": "📚 آکادمی", "callback_data": "useful_content"}],
                [{"text": "🧩 نحوه وارد کردن اشتراک", "callback_data": "useful_category_install"}],
                [{"text": "📥 دانلود و نصب برنامه", "callback_data": "useful_category_downloads"}],
            ]
        )

        keyboard = self.create_keyboard(keyboards)

        if config.subscription_url:
            qr_image = self.generate_qr_code(config.subscription_url)
            if qr_image:
                try:
                    await self.bot.send_photo(
                        callback.message.chat.id,
                        photo=BufferedInputFile(qr_image, filename="subscription_qr.png"),
                        caption="📱 QR Code لینک اشتراک",
                    )
                except Exception:
                    logger.exception("QR delivery failed for subscription %s", subscription.pk)

        await self.bot.send_message(
            callback.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard
        )
        await callback.answer()

    async def show_usage_statistics(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        await self.show_my_subscriptions(callback)

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
