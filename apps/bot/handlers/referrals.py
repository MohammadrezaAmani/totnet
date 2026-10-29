"""
Referrals Handler for Multi-Tenant VPN Bot
Handles referral system and marketing
"""

import logging
import os
import secrets
from html import escape

from aiogram import types
from aiogram.types import FSInputFile
from django.db.models import Exists, F, OuterRef, Q, Sum
from asgiref.sync import sync_to_async
from apps.bot.models import BotState
from apps.brands.models import BrandConfiguration
from django.utils import timezone

from apps.referrals.models import MarketingMaterial, Referral, ReferralLink
from apps.orders.models import Order, Payment
from apps.subscriptions.models import Subscription
from apps.referrals.selectors import pill_progress

from .base import BaseHandler

logger = logging.getLogger(__name__)


class ReferralsHandler(BaseHandler):
    """Handle referral system operations"""

    async def get_bot_username(self) -> str:
        """Get bot username, preferring brand config over API call"""

        if hasattr(self.brand, "bot_username") and self.brand.bot_username:
            return self.brand.bot_username

        try:
            me = await self.bot.me()
            return me.username or self.brand.slug
        except Exception as e:
            logger.warning(f"Could not fetch bot username: {e}")
            return self.brand.slug

    async def get_or_create_referral_link(self, user):
        try:
            return await ReferralLink.objects.aget(user=user, brand=self.brand, is_default=True)
        except ReferralLink.DoesNotExist:
            if not user.referral_code:
                user.referral_code = secrets.token_hex(4).upper()
                await user.asave(update_fields=["referral_code", "updated_at"])
            referral_link, _ = await ReferralLink.objects.aget_or_create(
                user=user,
                brand=self.brand,
                is_default=True,
                defaults={"code": user.referral_code},
            )
            return referral_link

    def _successful_referrals_queryset(self, user):
        """Referrals whose referee has at least one fully-paid positive purchase."""
        paid_orders = (
            Order.objects.filter(
                Q(user_id=OuterRef("referee_id")) | Q(recipient_id=OuterRef("referee_id"), recipient_claimed_at__isnull=False),
                brand=self.brand,
                final_price__gt=0,
                status__in=[
                    Order.OrderStatus.PAID,
                    Order.OrderStatus.PROCESSING,
                    Order.OrderStatus.COMPLETED,
                ],
            )
            .annotate(
                confirmed_total=Sum(
                    "payments__amount",
                    filter=Q(payments__status=Payment.PaymentStatus.CONFIRMED),
                )
            )
            .filter(confirmed_total__gte=F("final_price"))
        )
        return (
            Referral.objects.filter(referrer=user, brand=self.brand)
            .annotate(has_successful_purchase=Exists(paid_orders))
            .filter(has_successful_purchase=True)
        )

    async def show_referral_menu(self, callback: types.CallbackQuery):
        """Show the referral link and the two user-facing referral counters."""
        user, _ = await self.get_or_create_user(callback.from_user)
        referral_link = await self.get_or_create_referral_link(user)
        bot_username = await self.get_bot_username()
        referral_url = f"https://t.me/{bot_username}?start={referral_link.code}"

        successful_qs = self._successful_referrals_queryset(user)
        successful_referrals = await successful_qs.acount()
        joined_referrals = await Referral.objects.filter(
            referrer=user, brand=self.brand
        ).exclude(status=Referral.ReferralStatus.REJECTED).acount()
        now = timezone.now()
        active_referrals = await successful_qs.filter(
            referee__owned_subscriptions__brand=self.brand,
            referee__owned_subscriptions__status=Subscription.SubscriptionStatus.ACTIVE,
        ).filter(
            Q(referee__owned_subscriptions__expires_at__isnull=True)
            | Q(referee__owned_subscriptions__expires_at__gt=now)
        ).distinct().acount()

        pills = await pill_progress(user_id=user.pk, brand_id=self.brand.pk)
        added_line = (
            "این بستر راه اندازی شده تا هزینه vpn شما برای همیشه صفر شود،از این سیستم نهایت استفاده را ببرید!"
            if pills["outstanding"] > 0
            else "با معرفی و کسب قرص های بیشتر ، باخیال راحت تری خرید کنید"
        )
        text = f"""
👥 <b>معرفی دوستان</b>

🔗 <b>لینک معرفی شما:</b>
{referral_url}

📊 <b>آمار معرفی:</b>
• عضوشده با لینک شما: {joined_referrals}
• معرفی‌های دارای خرید تأییدشده: {successful_referrals}
• معرفی‌های دارای اشتراک فعال: {active_referrals}

👨🏽‍⚕ این سیستم راه اندازی شده تا افراد به بهترین کیفیت اینترنت اما بدون هزینه دسترسی داشته باشند.
{added_line}
فعالیت و معرفی بیشتر توسط شما ضمن کاهش محسوس و دائمی هزینه های شما منجر به احقاق این حق مسلم برای افراد بیشتری میشود،
شما عضو اصلی این تیم پزشکی هستید نه یک بیمار ساده!
        """

        rows = [
            [{"text": "📤 اشتراک‌گذاری لینک", "copy_text": {"text": referral_url}}],
            [{"text": "🧰 محتواهای کمکی", "callback_data": "referral_materials"}],
            [{"text": "📈 آمار معرفی", "callback_data": "referral_stats"}],
        ]
        if not user.referred_by_id and await BrandConfiguration.objects.filter(
            brand=self.brand, allow_manual_referrer=True,
        ).aexists():
            rows.append([{"text": "👥 ثبت دستی معرف", "callback_data": "manual_referrer"}])
        rows.append([{"text": "🔙 بازگشت", "callback_data": "main_menu"}])
        keyboard = self.create_keyboard(rows)

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

    async def start_manual_referrer(self, callback):
        user, _ = await self.get_or_create_user(callback.from_user)
        allowed = await BrandConfiguration.objects.filter(
            brand=self.brand, allow_manual_referrer=True,
        ).aexists()
        if not allowed or user.referred_by_id:
            await callback.answer("ثبت معرف برای شما فعال نیست یا قبلاً معرف ثبت کرده‌اید.", show_alert=True)
            return
        await self.update_user_state(user, BotState.StateType.REFERRAL_SETUP,
                                     {"step": "manual_referrer"})
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id,
            "👥 کد معرفی شخص را ارسال کنید. این انتخاب تنها یک بار قابل ثبت است.",
            self.get_back_keyboard("referral_system"),
        )
        await callback.answer()

    async def handle_manual_referrer_message(self, message, user):
        allowed = await BrandConfiguration.objects.filter(
            brand=self.brand, allow_manual_referrer=True,
        ).aexists()
        if not allowed or user.referred_by_id:
            await message.answer("امکان ثبت معرف وجود ندارد.")
            return
        code = (message.text or "").strip()
        if not 1 <= len(code) <= 50:
            await message.answer("کد معتبر نیست.")
            return
        from apps.referrals.services import attribute_referral
        success = await sync_to_async(attribute_referral)(
            user_id=user.pk, brand_id=self.brand.pk, code=code,
        )
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        await self._remove_user_input(message)
        await self.send_message_with_keyboard(
            message.chat.id,
            "✅ معرف ثبت شد." if success else "❌ کد معتبر نیست یا قبلاً معرف ثبت کرده‌اید.",
            self.get_back_keyboard("referral_system"),
        )

    async def show_referral_stats(self, callback: types.CallbackQuery):
        """Show the two product-defined referral counters."""
        user, _ = await self.get_or_create_user(callback.from_user)
        successful_qs = self._successful_referrals_queryset(user)
        successful_referrals = await successful_qs.acount()
        joined_referrals = await Referral.objects.filter(
            referrer=user, brand=self.brand
        ).exclude(status=Referral.ReferralStatus.REJECTED).acount()
        now = timezone.now()
        active_referrals = await successful_qs.filter(
            referee__owned_subscriptions__brand=self.brand,
            referee__owned_subscriptions__status=Subscription.SubscriptionStatus.ACTIVE,
        ).filter(
            Q(referee__owned_subscriptions__expires_at__isnull=True)
            | Q(referee__owned_subscriptions__expires_at__gt=now)
        ).distinct().acount()

        text = f"""
📈 <b>آمار معرفی</b>

• عضوشده با لینک شما: {joined_referrals}
• معرفی‌های دارای خرید تأییدشده: {successful_referrals}
• معرفی‌های دارای اشتراک فعال: {active_referrals}
        """

        keyboard = self.create_keyboard(
            [[{"text": "🔙 بازگشت", "callback_data": "referral_system"}]]
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

    async def show_referral_materials(self, callback: types.CallbackQuery):
        """List brand-provided videos, banners and suggested copy."""
        materials = []
        async for material in MarketingMaterial.objects.filter(
            brand=self.brand, is_active=True
        ).order_by("material_type", "name")[:30]:
            materials.append(material)

        if not materials:
            text = (
                "🧰 <b>محتواهای کمکی</b>\n\n"
                "در حال حاضر ویدیو، بنر یا متن پیشنهادی فعالی ثبت نشده است."
            )
            rows = [[{"text": "🔙 بازگشت", "callback_data": "referral_system"}]]
        else:
            labels = {
                MarketingMaterial.MaterialType.VIDEO: "🎬",
                MarketingMaterial.MaterialType.BANNER: "🖼",
                MarketingMaterial.MaterialType.TEXT_TEMPLATE: "📝",
                MarketingMaterial.MaterialType.SOCIAL_POST: "📣",
                MarketingMaterial.MaterialType.EMAIL_TEMPLATE: "✉️",
            }
            text = (
                "🧰 <b>محتواهای کمکی معرفی دوستان</b>\n\n"
                "ویدیوها، بنرها و متن‌های پیشنهادی آماده را انتخاب کنید:"
            )
            rows = [
                [
                    {
                        "text": f"{labels.get(item.material_type, '📄')} {item.name}",
                        "callback_data": f"referral_material_{item.pk}",
                    }
                ]
                for item in materials
            ]
            rows.append([{"text": "🔙 بازگشت", "callback_data": "referral_system"}])

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, self.create_keyboard(rows)
        )
        await callback.answer()

    async def show_referral_material(
        self, callback: types.CallbackQuery, material_id: int
    ):
        """Show one helper asset and send its configured media when available."""
        try:
            material = await MarketingMaterial.objects.aget(
                pk=material_id, brand=self.brand, is_active=True
            )
        except MarketingMaterial.DoesNotExist:
            await callback.answer("❌ محتوای موردنظر یافت نشد.", show_alert=True)
            return

        user, _ = await self.get_or_create_user(callback.from_user)
        referral_link = await self.get_or_create_referral_link(user)
        bot_username = await self.get_bot_username()
        referral_url = f"https://t.me/{bot_username}?start={referral_link.code}"

        await MarketingMaterial.objects.filter(pk=material.pk).aupdate(
            usage_count=F("usage_count") + 1
        )
        type_label = material.get_material_type_display()
        text = f"🧰 <b>{escape(material.name)}</b>\nنوع: {escape(type_label)}"
        if material.description:
            text += f"\n\n{escape(material.description[:700])}"
        if material.content:
            # Bound user-visible content to stay below Telegram's 4096-char limit.
            content = material.content[:2600]
            if len(material.content) > len(content):
                content += "…"
            text += f"\n\n<code>{escape(content)}</code>"
        text += f"\n\n🔗 لینک معرفی شما:\n{escape(referral_url)}"

        keyboard = self.create_keyboard(
            [[{"text": "🔙 بازگشت به محتواها", "callback_data": "referral_materials"}]]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )

        media = material.video if material.material_type == MarketingMaterial.MaterialType.VIDEO else material.image
        if media:
            source = None
            try:
                path = media.path
                if path and os.path.isfile(path):
                    source = FSInputFile(path)
            except (AttributeError, NotImplementedError, ValueError):
                source = None
            if source is None:
                try:
                    url = media.url
                except (AttributeError, ValueError):
                    url = ""
                if url.startswith(("http://", "https://")):
                    source = url
            if source is not None:
                caption_suffix = f"\n\n🔗 لینک معرفی:\n{referral_url}"
                body = material.content or material.description or material.name
                # Telegram captions have a 1024-character limit. Count UTF-16
                # units conservatively so emoji cannot push the link past it.
                budget = 1024 - len(caption_suffix.encode("utf-16-le")) // 2
                encoded = body.encode("utf-16-le")
                if len(encoded) // 2 > budget:
                    body = encoded[: (budget - 1) * 2].decode("utf-16-le", errors="ignore") + "…"
                caption = escape(body + caption_suffix)
                try:
                    if material.material_type == MarketingMaterial.MaterialType.VIDEO:
                        await self.bot.send_video(callback.message.chat.id, video=source, caption=caption, parse_mode="HTML")
                    else:
                        await self.bot.send_photo(callback.message.chat.id, photo=source, caption=caption, parse_mode="HTML")
                except Exception as exc:
                    logger.warning("Could not send referral material %s: %s", material.pk, exc)

        await callback.answer()

    async def share_referral_link(self, callback: types.CallbackQuery):
        # Retire older keyboards; Telegram's native CopyTextButton owns clipboard access.
        await self.show_referral_menu(callback)

    async def copy_referral_link(self, callback: types.CallbackQuery):
        await self.show_referral_menu(callback)
