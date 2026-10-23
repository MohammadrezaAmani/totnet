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
)
from apps.vpn_providers.models import VPNProvider
from apps.subscriptions.usage import remaining_usage
from apps.subscriptions.presentation import compact_plan_specs
from apps.subscriptions.policy import temporary_duration_blocked
from apps.subscriptions.catalog import get_cached_plans

from .base import BaseHandler
from utils.jalali import jalali_date

logger = logging.getLogger(__name__)


class SubscriptionHandler(BaseHandler):
    """Handle subscription management and delivery"""

    async def show_my_subscriptions(self, callback: types.CallbackQuery, page: int = 1):
        """List subscriptions; selecting one opens its details (not all renewal plans)."""
        user, _ = await self.get_or_create_user(callback.from_user)
        queryset = Subscription.objects.filter(Q(user=user) | Q(owner=user), brand=self.brand).exclude(status=Subscription.SubscriptionStatus.CANCELLED)
        total = await queryset.acount()
        per_page = 8
        last_page = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, last_page))
        subscriptions = []
        async for sub in (
            Subscription.objects.filter(Q(user=user) | Q(owner=user), brand=self.brand)
            .select_related("plan", "owner", "user", "vpn_provider", "remote_account")
            .exclude(status=Subscription.SubscriptionStatus.CANCELLED)
            .order_by("-created_at")[(page - 1) * per_page:page * per_page]
        ):
            subscriptions.append(sub)

        claims = []
        async for claim in (
            SubscriptionClaim.objects.filter(
                user=user,
                brand=self.brand,
                status__in=[
                    SubscriptionClaim.Status.PENDING,
                    SubscriptionClaim.Status.REJECTED,
                ],
            )
            .order_by("-created_at")[:20]
        ):
            claims.append(claim)

        text = "📱 <b>اشتراک‌های من</b>\n\nبرای مشاهده اطلاعات، اشتراک موردنظر را انتخاب کنید."
        if not subscriptions and not claims:
            text += "\n\nهنوز اشتراکی برای شما ثبت نشده است."
            rows = [
                [{"text": "📎 ثبت اشتراک فعال", "callback_data": "onboarding_existing_subscription"}],
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
                username = (
                    sub.connectix_username
                    or sub.vpn_user_email
                    or str(sub.subscription_id)[:8]
                )
                service_type = category_labels.get(
                    sub.plan.service_category, sub.plan.name
                )
                remaining_traffic, remaining_time = remaining_usage(sub)
                label = f"{sub.display_name or username} • {service_type}"
                text += f"\n\n👤 <code>{escape(sub.display_name or username)}</code> • {escape(service_type)}\n📦 حجم باقیمانده: {escape(remaining_traffic)}\n⏳ زمان باقیمانده: {escape(remaining_time)}"
                if len(label) > 58:
                    label = label[:57] + "…"
                rows.append(
                    [
                        {
                            "text": label,
                            "callback_data": f"subscription_details_{sub.pk}",
                        }
                    ]
                )
            for claim in claims:
                icon = "⏳" if claim.status == SubscriptionClaim.Status.PENDING else "❌"
                status = "در انتظار بررسی" if claim.status == SubscriptionClaim.Status.PENDING else "رد شده"
                label = f"{icon} {claim.username} • {status}"
                if len(label) > 58:
                    label = label[:57] + "…"
                rows.append(
                    [{"text": label, "callback_data": f"subscription_claim_{claim.pk}"}]
                )
            if last_page > 1:
                text += f"\n\nصفحه {page} از {last_page} · {total} اشتراک"
                rows.append([
                    {"text": label, "callback_data": f"subscriptions_page_{target}"}
                    for target, label in ((page - 1, "◀️ قبلی"), (page + 1, "بعدی ▶️"))
                    if 1 <= target <= last_page
                ])
            rows.extend(
                [
                    [{"text": "📎 ثبت اشتراک فعال", "callback_data": "onboarding_existing_subscription"}],
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
                    [{"text": "📎 ثبت/ارسال مجدد یوزرنیم", "callback_data": "onboarding_existing_subscription"}],
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
        """Page through active renewal plans without exceeding Telegram markup limits."""
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            subscription = (
                await Subscription.objects.select_related(
                    "plan", "owner", "user", "vpn_provider", "remote_account"
                )
                .filter(Q(user=user) | Q(owner=user), brand=self.brand)
                .aget(pk=subscription_id)
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
            return

        plan = subscription.plan
        username = (
            subscription.connectix_username
            or subscription.vpn_user_email
            or str(subscription.subscription_id)[:8]
        )
        category_labels = {
            SubscriptionPlan.ServiceCategory.NORMAL: "نرمال",
            SubscriptionPlan.ServiceCategory.ROYAL: "رویال",
            SubscriptionPlan.ServiceCategory.IRAN_IP: "آی‌پی ایران",
        }
        current_category = category_labels.get(plan.service_category, plan.name)
        current_traffic = (
            f"{subscription.traffic_limit_gb:g} گیگ"
            if subscription.traffic_limit_gb is not None
            else "نامحدود"
        )
        expiry = (
            jalali_date(subscription.expires_at, time=True)
            if subscription.expires_at
            else "نامحدود"
        )

        plans = await get_cached_plans(self.brand.pk)
        plans.sort(key=lambda candidate: candidate.pk != plan.pk)
        current_plan_available = any(candidate.pk == plan.pk for candidate in plans)
        # A brand can have hundreds of Connectix plans. Sending one button per
        # plan in a single inline keyboard is rejected by Telegram as too long.
        page_size = 8
        total_pages = max(1, (len(plans) + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        visible_plans = plans[(page - 1) * page_size:page * page_size]
        renewal_hint = (
            "طرح موردنظر را انتخاب کنید. طرح فعلی شما در ابتدای فهرست قرار گرفته است."
            if current_plan_available
            else "طرح فعلی شما دیگر برای فروش فعال نیست؛ یکی از طرح‌های فعال زیر را انتخاب کنید."
        )

        remaining_traffic, remaining_time = remaining_usage(subscription)
        text = f"""
🔄 <b>تمدید / تغییر طرح</b>

👤 اشتراک: <code>{escape(subscription.display_name or username)}</code>
🏷 نوع فعلی: {escape(current_category)}
📦 حجم فعلی: {current_traffic}
📊 حجم باقیمانده: {escape(remaining_traffic)}
⏳ زمان باقیمانده: {escape(remaining_time)}
⏰ انقضا: {expiry}

{renewal_hint}
        """

        rows = []
        if total_pages > 1:
            text += f"\n📄 صفحه {page} از {total_pages} · {len(plans)} طرح فعال\n"
        credits = {
            currency: await self.purchase_credit(user, currency)
            for currency in {p.currency for p in visible_plans}
        }
        if any(credits.values()):
            text += "\n💰 مبالغ پلن‌ها پس از کسر موجودی نقد قابل‌استفاده شماست.\n"
        for candidate in visible_plans:
            category = category_labels.get(candidate.service_category, candidate.name)
            prefix = "♻️" if candidate.pk == plan.pk else ""
            blocked = temporary_duration_blocked(candidate)
            label = f"{'🔴' if blocked else ''}{prefix}{category} {compact_plan_specs(candidate)} 💰{self.format_price(self.payable_plan_price(candidate, credits[candidate.currency]), candidate.currency)}"
            callback_data = (
                f"repurchase_subscription_{subscription.pk}"
                if candidate.pk == plan.pk
                else f"renew_with_plan_{subscription.pk}_{candidate.pk}"
            )
            rows.append([{"text": label, "callback_data": callback_data,
                          "style": "danger" if blocked else None}])

        if not plans:
            text += "\n⚠️ در حال حاضر هیچ پلن فعالی برای خرید وجود ندارد."
        if total_pages > 1:
            navigation = []
            if page > 1:
                navigation.append({
                    "text": "◀️ قبلی",
                    "callback_data": f"renewal_plans_page_{subscription.pk}_{page - 1}",
                })
            if page < total_pages:
                navigation.append({
                    "text": "بعدی ▶️",
                    "callback_data": f"renewal_plans_page_{subscription.pk}_{page + 1}",
                })
            rows.append(navigation)

        rows.extend(
            [
                [
                    {
                        "text": "📱 جزئیات و کانفیگ این اشتراک",
                        "callback_data": f"subscription_details_{subscription.pk}",
                    }
                ],
                [{"text": "✏️ نام‌گذاری این اشتراک", "callback_data": f"rename_subscription_{subscription.pk}"}],
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
            "✏️ نام دلخواه اشتراک را ارسال کنید (۱ تا ۴۰ نویسه).\n"
            "این نام در ربات ذخیره می‌شود؛ تغییر نام اکانت موجود در پنل "
            "پس از پشتیبانی API ویرایش Connectix امکان‌پذیر است.",
            self.get_back_keyboard(f"renewal_details_{subscription_id}"),
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
        shown_name = escape(name) if name else "هنوز نام‌گذاری نشده؛ از دکمه «نام‌گذاری اشتراک» استفاده کنید."
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
        keyboard_buttons.append([{"text": "✏️ نام‌گذاری اشتراک", "callback_data": f"rename_subscription_{subscription.pk}"}])
        keyboard_buttons.append([{"text": "🔙 بازگشت", "callback_data": "my_subscriptions"}])

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

        text = f"""
📱 اطلاعات اتصال - {escape(subscription.display_name or subscription.plan.name)}

👤 نام کاربری: <code>{escape(subscription.connectix_username or '—')}</code>
🔑 رمز عبور: <code>{escape(subscription.connectix_password or 'هنوز از پنل دریافت نشده')}</code>

🔗 لینک اشتراک:
<code>{escape(subscription_url)}</code>

لینک اشتراک یا QR را در برنامه VPN مورد نظرتان وارد کنید.
✏️ اگر هنوز نامی انتخاب نکردید، از بخش «اشتراک‌های من» نام اشتراک خود را تعیین کنید.
مثال: آیفون خودم
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
                await Subscription.objects.select_related("plan", "vpn_provider", "remote_account")
                .filter(Q(user=user) | Q(owner=user))
                .aget(id=subscription_id, brand=self.brand)
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
            return

        from apps.subscriptions.usage import consumed_traffic_gb

        used = consumed_traffic_gb(subscription)
        label = (
            f"{format(used.normalize(), 'f')} گیگابایت"
            if used is not None else "در انتظار اطلاعات مصرف از سرویس‌دهنده"
        )
        text = (
            "📊 <b>آمار مصرف اشتراک</b>\n\n"
            f"📈 ترافیک مصرف‌شده: {escape(label)}"
        )

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
