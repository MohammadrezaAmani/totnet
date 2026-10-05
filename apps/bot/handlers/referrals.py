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
from django.db.models import F

from apps.referrals.models import MarketingMaterial, Referral, ReferralLink
from apps.referrals.selectors import referral_level_progress

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
            return await ReferralLink.objects.aget(user=user, brand=self.brand)
        except ReferralLink.DoesNotExist:
            if not user.referral_code:
                user.referral_code = secrets.token_hex(4).upper()
                await user.asave(update_fields=["referral_code", "updated_at"])
            referral_link, _ = await ReferralLink.objects.aget_or_create(
                user=user,
                brand=self.brand,
                defaults={"code": user.referral_code},
            )
            return referral_link

    async def show_referral_menu(self, callback: types.CallbackQuery):
        """Show referral system menu"""
        user, _ = await self.get_or_create_user(callback.from_user)

        referral_link = await self.get_or_create_referral_link(user)

        bot_username = await self.get_bot_username()
        referral_url = f"https://t.me/{bot_username}?start={referral_link.code}"
        progress = await referral_level_progress(
            user_id=user.pk, brand_id=self.brand.pk
        )
        successful_referrals = await Referral.objects.filter(
            referrer=user,
            brand=self.brand,
            status=Referral.ReferralStatus.REWARDED,
        ).acount()
        account = progress["account"]
        program = progress["program"]
        lifetime_points = progress["lifetime_points"]
        current_level = progress["current_level"]
        level_label = (
            f"{current_level.badge} {current_level.name}" if current_level else "—"
        )
        program_notice = (
            "امتیازها پس از خرید سودآور دوستان معرفی‌شده محاسبه می‌شوند."
            if program and program.reference_service_id
            else "لینک معرفی فعال است؛ امتیاز و پاداش این برند هنوز پیکربندی نشده است."
        )

        text = f"""
👥 سیستم معرفی دوستان

🔗 لینک معرفی شما:
{referral_url}

📊 آمار معرفی:
• ورودهای یکتا از لینک: {referral_link.click_count}
• تعداد ثبت‌نام: {referral_link.conversion_count}
• تعداد کل معرفی‌ها: {progress["referral_count"]}
• معرفی‌های موفق دارای خرید: {successful_referrals}

💰 درآمد از معرفی:
• امتیاز مادام‌العمر: {lifetime_points:g}
• امتیاز کامل قابل استفاده: {account.liquid_points if account else 0:g}
• سطح فعلی: {level_label}

{program_notice}

این سیستم راه‌اندازی شده تا افراد به بالاترین کیفیت اینترنت بدون هزینه دسترسی پیدا کنند. فعالیت و معرفی بیشتر توسط شما، ضمن کاهش محسوس و دائمی هزینه‌های شما، به دسترسی افراد بیشتری به این امکان کمک می‌کند.
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📤 اشتراک‌گذاری لینک", "callback_data": "share_referral"}],
                [{"text": "🧰 محتواهای کمکی", "callback_data": "referral_materials"}],
                [{"text": "📈 آمار معرفی‌های موفق", "callback_data": "referral_stats"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
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

    async def show_referral_stats(self, callback: types.CallbackQuery):
        """Show detailed referral statistics"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            referral_link = await ReferralLink.objects.aget(user=user, brand=self.brand)
        except ReferralLink.DoesNotExist:
            await callback.answer("❌ لینک معرفی یافت نشد.")
            return

        all_referrals = Referral.objects.filter(referrer=user, brand=self.brand)
        total_referrals = await all_referrals.acount()
        completed_referrals = await all_referrals.filter(
            status=Referral.ReferralStatus.REWARDED
        ).acount()
        pending_referrals = await all_referrals.filter(
            status=Referral.ReferralStatus.PENDING
        ).acount()
        progress = await referral_level_progress(
            user_id=user.pk, brand_id=self.brand.pk
        )
        account = progress["account"]
        lifetime_points = progress["lifetime_points"]
        program = progress["program"]
        level_lines = []
        if program and program.enable_level_rewards:
            current_level = progress["current_level"]
            async for level in program.levels.order_by("level"):
                done = current_level and level.level <= current_level.level
                level_lines.append(
                    f"• {level.badge} {level.name}: {level.min_referrals} معرفی، "
                    f"{level.min_lifetime_points:g} امتیاز و تبدیل {level.min_conversion_rate:g}% "
                    f"{'✓' if done else ''}"
                )
        elif program:
            level_lines.append("سطح‌ها برای این برند غیرفعال هستند.")
        levels_text = "\n".join(level_lines) or "سطحی برای این برند تنظیم نشده است."
        text = f"""
📈 آمار تفصیلی معرفی

📊 آمار کلیک و ثبت:
• ورودهای یکتا از لینک: {referral_link.click_count or 0}
• تعداد ثبت‌نام: {total_referrals}
• نرخ تبدیل به خرید: {(completed_referrals / max(referral_link.click_count or 1, 1)) * 100:.1f}%

✅ معرفی‌های دارای خرید: {completed_referrals}
⏳ معرفی‌های در انتظار: {pending_referrals}

💰 درآمد:
• امتیاز مادام‌العمر: {lifetime_points:g}
• امتیاز کامل قابل استفاده: {account.liquid_points if account else 0:g}

🏆 سطح‌ها:
{levels_text}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🔙 بازگشت", "callback_data": "referral_system"}],
            ]
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
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
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

        keyboard = self.create_keyboard(
            [[{"text": "🔙 بازگشت به محتواها", "callback_data": "referral_materials"}]]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )

        media = (
            material.video
            if material.material_type == MarketingMaterial.MaterialType.VIDEO
            else material.image
        )
        if media:
            source = None
            try:
                path = media.path
                if path and os.path.isfile(path):
                    source = FSInputFile(path)
            except AttributeError, NotImplementedError, ValueError:
                source = None
            if source is None:
                try:
                    url = media.url
                except AttributeError, ValueError:
                    url = ""
                if url.startswith(("http://", "https://")):
                    source = url
            if source is not None:
                try:
                    if material.material_type == MarketingMaterial.MaterialType.VIDEO:
                        await self.bot.send_video(
                            callback.message.chat.id, video=source
                        )
                    else:
                        await self.bot.send_photo(
                            callback.message.chat.id, photo=source
                        )
                except Exception as exc:
                    logger.warning(
                        "Could not send referral material %s: %s", material.pk, exc
                    )

        await callback.answer()

    async def share_referral_link(self, callback: types.CallbackQuery):
        """Share referral link with user"""
        user, _ = await self.get_or_create_user(callback.from_user)

        referral_link = await self.get_or_create_referral_link(user)

        bot_username = await self.get_bot_username()
        referral_url = f"https://t.me/{bot_username}?start={referral_link.code}"

        text = f"""
📤 لینک معرفی شما برای اشتراک‌گذاری:

<code>{referral_url}</code>

با اشتراک‌گذاری این لینک، دوستان شما می‌توانند از بات استفاده کنند و شما هم امتیاز کسب خواهید کرد!
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🔗 کپی کردن لینک",
                        "callback_data": "copy_referral_link",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "referral_system"}],
            ]
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

        await callback.answer("✅ لینک معرفی شما آماده است!")

    async def copy_referral_link(self, callback: types.CallbackQuery):
        """Copy referral link to clipboard"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            referral_link = await ReferralLink.objects.aget(user=user, brand=self.brand)
        except ReferralLink.DoesNotExist:
            await callback.answer("❌ لینک معرفی یافت نشد.")
            return

        bot_username = await self.get_bot_username()
        referral_url = f"https://t.me/{bot_username}?start={referral_link.code}"

        await callback.answer(
            f"لینک شما: {referral_url}\n\nتوجه: لطفاً لینک را کپی کنید و برای دوستانتان بفرستید.",
            show_alert=False,
        )
