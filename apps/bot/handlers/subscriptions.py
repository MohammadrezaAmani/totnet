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

from .base import BaseHandler

logger = logging.getLogger(__name__)


class SubscriptionHandler(BaseHandler):
    """Handle subscription management and delivery"""

    async def show_my_subscriptions(self, callback: types.CallbackQuery, page: int = 1):
        """List subscriptions directly; selecting one opens its renewal choices."""
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

        text = "📱 <b>اشتراک‌های من</b>\n\nاشتراک موردنظر را برای تمدید یا تغییر طرح انتخاب کنید."
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
                label = f"{username} • {service_type}"
                text += f"\n\n👤 <code>{escape(username)}</code> • {escape(service_type)}\n📦 حجم باقیمانده: {escape(remaining_traffic)}\n⏳ زمان باقیمانده: {escape(remaining_time)}"
                if len(label) > 58:
                    label = label[:57] + "…"
                rows.append(
                    [
                        {
                            "text": label,
                            "callback_data": f"renewal_details_{sub.pk}",
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
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Show all purchasable plans for one subscription, with its current plan first."""
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
            subscription.expires_at.strftime("%Y/%m/%d %H:%M")
            if subscription.expires_at
            else "نامحدود"
        )

        plans = []
        async for candidate in SubscriptionPlan.objects.filter(
            brand=self.brand, is_active=True, is_visible=True
        ).order_by("display_order", "price", "id"):
            plans.append(candidate)
        plans.sort(key=lambda candidate: candidate.pk != plan.pk)
        current_plan_available = any(candidate.pk == plan.pk for candidate in plans)
        renewal_hint = (
            "طرح موردنظر را انتخاب کنید. طرح فعلی شما در ابتدای فهرست قرار گرفته است."
            if current_plan_available
            else "طرح فعلی شما دیگر برای فروش فعال نیست؛ یکی از طرح‌های فعال زیر را انتخاب کنید."
        )

        remaining_traffic, remaining_time = remaining_usage(subscription)
        text = f"""
🔄 <b>تمدید / تغییر طرح</b>

👤 یوزرنیم: <code>{escape(username)}</code>
🏷 نوع فعلی: {escape(current_category)}
📦 حجم فعلی: {current_traffic}
📊 حجم باقیمانده: {escape(remaining_traffic)}
⏳ زمان باقیمانده: {escape(remaining_time)}
⏰ انقضا: {expiry}

{renewal_hint}
        """

        rows = []
        for candidate in plans:
            category = category_labels.get(candidate.service_category, candidate.name)
            prefix = "♻️" if candidate.pk == plan.pk else ""
            label = f"{prefix}{category} {compact_plan_specs(candidate)} 💰{self.format_price(candidate.discounted_price, candidate.currency)}"
            callback_data = (
                f"repurchase_subscription_{subscription.pk}"
                if candidate.pk == plan.pk
                else f"renew_with_plan_{subscription.pk}_{candidate.pk}"
            )
            rows.append([{"text": label, "callback_data": callback_data}])

        if not plans:
            text += "\n⚠️ در حال حاضر هیچ پلن فعالی برای خرید وجود ندارد."

        rows.extend(
            [
                [
                    {
                        "text": "📱 جزئیات و کانفیگ این اشتراک",
                        "callback_data": f"subscription_details_{subscription.pk}",
                    }
                ],
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

    async def show_subscription_details(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Show detailed subscription information"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            subscription = (
                await Subscription.objects.select_related("plan", "vpn_provider", "owner", "user")
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
📱 اطلاعات اتصال - {subscription.plan.name}

👤 نام کاربری: <code>{escape(subscription.connectix_username or '—')}</code>

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
