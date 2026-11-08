"""Propzino participation, cash receipts and user-facing challenge screens."""
from __future__ import annotations

import math
from datetime import timedelta
from html import escape

from aiogram import types
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.bot.models import BotState
from apps.orders.models import AdminReceiptMessage, Payment, PaymentCard
from apps.referrals.gamification import (
    ChallengeError, choose_challenge_tier, create_challenge_entry_payment,
    decline_challenge,
)
from apps.referrals.models import UserChallenge

from .base import BaseHandler


class ChallengesHandler(BaseHandler):
    def _intro(self, program):
        return (
            f"👨🏾‍⚕️<b>{escape(program.name)}، قدمی بزرگ برای رایگان شدن دائمی خدمات ما!</b>\n\n"
            "🚨این چالش فقط یک‌بار انجام می‌شود؛ پس نهایت استفاده را از آن ببرید."
        )

    def _conditions(self, program):
        success_text = (
            "دو برابر" if program.success_multiplier == 2
            else f"{program.success_multiplier:g} برابر"
        )
        failure_text = (
            "نصف" if program.failure_multiplier == 0.5
            else f"{program.failure_multiplier:g} برابر"
        )
        return (
            "📑<b>شرایط چالش:</b>\n"
            f"۱- مبلغ ورودی پس از پایان چالش ({program.duration_days} روز)، "
            "بدون قید و شرط به کیف پول شما برمی‌گردد.\n"
            f"۲- ارزش امتیازات این چالش {success_text} حالت عادی است.\n"
            "۳- در صورت تکمیل سطح انتخابی، امتیازات چالش را با ارزش ویژه دریافت می‌کنید.\n"
            f"۴- در صورت تکمیل‌نشدن سطح، معرفی‌های موفق با {failure_text} نرخ عادی محاسبه می‌شوند.\n\n"
            "🦠معرفی موفق یعنی کاربر یکتایی که در زمان چالش با لینک شما وارد شده "
            "و اولین خرید موفقش را پیش از پایان چالش انجام داده باشد. "
            "خریدهای بعدی او با نرخ عادی محاسبه می‌شوند."
        )

    def _tips(self):
        return (
            "🔬<b>دو نکته:</b>\n"
            "💉اشتراکی که هنوز با آن اتصال انجام نشده باشد، زمان و حجم مصرف‌نشده‌اش حفظ می‌شود.\n\n"
            "💉اگر کاربر با لینک شما وارد شود اما بعد از پایان چالش خرید کند، "
            "پاداش خرید مطابق حالت عادی محاسبه می‌شود.\n\n"
            "سطح چالش خودت رو با دقت انتخاب کن! 😉"
        )

    async def _current(self, user):
        return await UserChallenge.objects.filter(user=user, brand=self.brand).select_related(
            "program", "tier", "entry_payment"
        ).order_by("-created_at").afirst()

    async def show_active_challenges(self, callback: types.CallbackQuery):
        user, _ = await self.get_or_create_user(callback.from_user)
        challenge = await self._current(user)
        if challenge and challenge.status in {UserChallenge.Status.SUCCEEDED, UserChallenge.Status.FAILED}:
            if challenge.settled_at and challenge.settled_at < timezone.now() - timedelta(hours=72):
                challenge = None
        if challenge is None:
            text = "🏆 <b>چالش‌ها</b>\n\nدر حال حاضر چالش فعالی ندارید."
            rows = [[{"text": "🔙 بازگشت", "callback_data": "main_menu"}]]
        elif challenge.status == UserChallenge.Status.OFFERED:
            text = (f"🏆 <b>چالش ویژه {escape(challenge.program.name)}</b>\n\n"
                    "این فرصت فقط یک‌بار به شما ارائه می‌شود.\n"
                    "مایل به مشاهده شرایط هستید؟")
            rows = [
                [{"text": "✅ بله", "callback_data": f"challenge_accept_{challenge.pk}"},
                 {"text": "❌ خیر", "callback_data": f"challenge_decline_{challenge.pk}"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        elif challenge.status == UserChallenge.Status.AWAITING_FUNDS:
            text = (f"🏆 <b>{escape(challenge.program.name)}</b>\n\n"
                    f"🎯 هدف: {challenge.target_referrals} معرفی موفق\n"
                    f"💳 مبلغ ورودی نقدی: {self.format_price(challenge.entry_amount, self.brand.currency)}")
            if challenge.entry_payment_id and challenge.entry_payment.status in {
                Payment.PaymentStatus.PENDING, Payment.PaymentStatus.AWAITING_CONFIRMATION,
            }:
                text += "\n⏳ رسید در انتظار تأیید ادمین است."
                rows = [[{"text": "🔙 بازگشت", "callback_data": "main_menu"}]]
            else:
                rows = [
                    [{"text": "💳 پرداخت نقدی ورودی", "callback_data": f"challenge_pay_{challenge.pk}"}],
                    [{"text": "📋 تغییر سطح", "callback_data": f"challenge_page_{challenge.pk}_3"}],
                    [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
                ]
        elif challenge.status == UserChallenge.Status.ACTIVE:
            remaining = max(0, math.ceil((challenge.ends_at - timezone.now()).total_seconds() / 86400))
            text = (
                f"🏆 <b>{escape(challenge.program.name)} — فعال</b>\n\n"
                f"🎯 هدف: {challenge.target_referrals} معرفی موفق\n"
                f"✅ پیشرفت: {challenge.successful_referrals} از {challenge.target_referrals}\n"
                f"⏳ زمان باقی‌مانده: {remaining} روز\n"
                f"🧊 وجه ورودی: {self.format_price(challenge.entry_amount, self.brand.currency)}\n\n"
                "قرص‌های چالش تا پایان آن جدا نگهداری می‌شوند."
            )
            rows = [
                [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                [{"text": "📑 توضیحات کامل چالش", "callback_data": f"challenge_terms_{challenge.pk}"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        elif challenge.status == UserChallenge.Status.DECLINED:
            text = "این فرصت قبلاً رد شده است."
            rows = [[{"text": "🔙 بازگشت", "callback_data": "main_menu"}]]
        else:
            result = "✅ تکمیل شد" if challenge.status == UserChallenge.Status.SUCCEEDED else "⏳ مهلت به پایان رسید"
            text = (f"🏆 <b>{escape(challenge.program.name)}</b>\n\n{result}\n"
                    f"🦠 قرص‌ها: {challenge.successful_referrals}\n"
                    "وجه ورودی به امتیازات نقد شما بازگشته است.")
            rows = [[{"text": "🏅 امتیازات من", "callback_data": "rewards"}],
                    [{"text": "🔙 بازگشت", "callback_data": "main_menu"}]]
        await self.edit_message_with_keyboard(callback.message.chat.id, callback.message.message_id,
                                              text, self.create_keyboard(rows))
        await callback.answer()

    async def accept_challenge(self, callback: types.CallbackQuery, challenge_id: int):
        await self.show_challenge_page(callback, challenge_id, 1)

    async def show_challenge_page(self, callback: types.CallbackQuery, challenge_id: int, page: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        challenge = await UserChallenge.objects.filter(
            pk=challenge_id, user=user, brand=self.brand,
            status__in=[UserChallenge.Status.OFFERED, UserChallenge.Status.AWAITING_FUNDS]
        ).select_related("program").afirst()
        if not challenge or page not in (1, 2, 3):
            await callback.answer("این صفحه در دسترس نیست.", show_alert=True)
            return
        if page == 1:
            text = self._intro(challenge.program)
            rows = [[{"text": "بعدی", "callback_data": f"challenge_page_{challenge.pk}_2"}]]
        elif page == 2:
            text = self._conditions(challenge.program)
            rows = [[{"text": "بعدی", "callback_data": f"challenge_page_{challenge.pk}_3"}],
                    [{"text": "🔙 بازگشت", "callback_data": f"challenge_page_{challenge.pk}_1"}]]
        else:
            text = self._tips() + "\n\n<b>سطح چالش را انتخاب کنید:</b>"
            rows = []
            async for tier in challenge.program.tiers.filter(is_active=True).order_by("display_order", "target_referrals"):
                rows.append([{"text": (f"معرفی {tier.target_referrals} نفر — ورودی "
                                        f"{self.format_price(tier.entry_fee, self.brand.currency)}"),
                              "callback_data": f"challenge_tier_{tier.pk}"}])
            if not rows:
                text += "\nسطح فعالی تعریف نشده است."
            rows.append([{"text": "🔙 بازگشت", "callback_data": f"challenge_page_{challenge.pk}_2"}])
        rows.append([{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}])
        await self.edit_message_with_keyboard(callback.message.chat.id, callback.message.message_id,
                                              text, self.create_keyboard(rows))
        await callback.answer()

    async def show_challenge_terms(self, callback: types.CallbackQuery, challenge_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        challenge = await UserChallenge.objects.filter(pk=challenge_id, user=user, brand=self.brand).select_related("program").afirst()
        if not challenge:
            await callback.answer("چالش پیدا نشد.", show_alert=True)
            return
        await self.send_message_with_keyboard(
            callback.message.chat.id,
            self._intro(challenge.program) + "\n\n" + self._conditions(challenge.program)
            + "\n\n" + self._tips(),
            self.get_back_keyboard("active_challenges"),
        )
        await callback.answer()

    async def decline_challenge(self, callback: types.CallbackQuery, challenge_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        if not await UserChallenge.objects.filter(
            pk=challenge_id, user=user, brand=self.brand, status=UserChallenge.Status.OFFERED
        ).aexists():
            await callback.answer("این پیشنهاد در دسترس نیست.", show_alert=True)
            return
        try:
            await sync_to_async(decline_challenge, thread_sensitive=True)(user_id=user.pk, brand_id=self.brand.pk)
        except ChallengeError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await self.show_active_challenges(callback)

    async def select_tier(self, callback: types.CallbackQuery, tier_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            challenge = await sync_to_async(choose_challenge_tier, thread_sensitive=True)(
                user_id=user.pk, brand_id=self.brand.pk, tier_id=tier_id,
            )
        except ChallengeError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await self.show_challenge_payment(callback, challenge.pk)

    async def show_challenge_payment(self, callback: types.CallbackQuery, challenge_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        challenge = await UserChallenge.objects.filter(
            pk=challenge_id, user=user, brand=self.brand, status=UserChallenge.Status.AWAITING_FUNDS,
        ).select_related("entry_payment", "program").afirst()
        if not challenge:
            await callback.answer("چالش در انتظار پرداخت نیست.", show_alert=True)
            return
        if challenge.entry_payment_id and challenge.entry_payment.status in {
            Payment.PaymentStatus.PENDING, Payment.PaymentStatus.AWAITING_CONFIRMATION,
        }:
            await callback.answer("رسید شما در انتظار تأیید است.", show_alert=True)
            return
        text = (f"💳 <b>واریز ورودی {escape(challenge.program.name)}</b>\n\n"
                f"مبلغ: {self.format_price(challenge.entry_amount, self.brand.currency)}\n"
                "برای پرداخت مستقیم، کارت مقصد را انتخاب کنید:")
        rows = [[{"text": f"💳 {card.bank_name} • {card.card_number[-4:]}",
                  "callback_data": f"challenge_card_{challenge.pk}_{card.pk}"}]
                async for card in PaymentCard.objects.filter(brand=self.brand, is_active=True).order_by("pk")]
        if not rows:
            text += "\nدر حال حاضر کارت پرداختی فعال نیست."
        rows.append([{"text": "🔙 بازگشت", "callback_data": "active_challenges"}])
        await self.edit_message_with_keyboard(callback.message.chat.id, callback.message.message_id,
                                              text, self.create_keyboard(rows))
        await callback.answer()

    async def show_challenge_card(self, callback: types.CallbackQuery, challenge_id: int, card_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        challenge = await UserChallenge.objects.filter(
            pk=challenge_id, user=user, brand=self.brand, status=UserChallenge.Status.AWAITING_FUNDS
        ).select_related("entry_payment", "program").afirst()
        card = await PaymentCard.objects.filter(pk=card_id, brand=self.brand, is_active=True).afirst()
        if not challenge or not card or (challenge.entry_payment_id and challenge.entry_payment.status in {
            Payment.PaymentStatus.PENDING, Payment.PaymentStatus.AWAITING_CONFIRMATION,
        }):
            await callback.answer("پرداخت در دسترس نیست.", show_alert=True)
            return
        await self.update_user_state(user, BotState.StateType.PAYMENT_PROCESS, {
            "action": "challenge_entry", "step": "waiting_receipt",
            "challenge_id": challenge.pk, "card_id": card.pk,
        })
        text = (f"💳 <b>واریز ورودی {escape(challenge.program.name)}</b>\n\n"
                f"مبلغ دقیق: {self.format_price(challenge.entry_amount, self.brand.currency)}\n"
                f"شماره کارت: <code>{escape(card.card_number)}</code>\n"
                f"صاحب حساب: {escape(card.cardholder_name)}\n\n"
                "پس از واریز نقدی، تصویر رسید را همین‌جا ارسال کنید.")
        await self.edit_message_with_keyboard(callback.message.chat.id, callback.message.message_id,
                                              text, self.get_back_keyboard("active_challenges"))
        await callback.answer()

    async def handle_receipt_photo(self, message: types.Message, user, state: BotState):
        data = state.state_data or {}
        if not message.photo:
            await message.reply("لطفاً تصویر رسید واریز را ارسال کنید.")
            return
        try:
            payment = await sync_to_async(create_challenge_entry_payment, thread_sensitive=True)(
                user_id=user.pk, brand_id=self.brand.pk,
                challenge_id=data.get("challenge_id"), card_id=data.get("card_id"),
                receipt_file=message.photo[-1].file_id,
            )
        except ChallengeError as exc:
            await message.reply(f"❌ {escape(str(exc))}")
            return
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        await self._remove_user_input(message)
        await self.send_message_with_keyboard(
            message.chat.id,
            "✅ رسید واریز ورودی پراپزینو ثبت شد.\n"
            "پس از تأیید ادمین، زمان چالش آغاز و پیام شروع برای شما ارسال می‌شود.",
            self.get_back_keyboard("active_challenges"),
        )
        from apps.accounts.models import User
        referrer_id = await User.objects.filter(pk=user.pk).values_list(
            "referred_by__telegram_id", flat=True,
        ).afirst()
        caption = (
            "🧾 <b>واریز ورودی پراپزینو</b>\n"
            f"مبلغ: {self.format_price(payment.amount, payment.currency)}\n"
            f"آیدی عددی واریزکننده: <a href=\"tg://user?id={user.telegram_id}\">{user.telegram_id}</a>\n"
            f"آیدی عددی معرف: {referrer_id or 'ندارد'}\n"
            f"شناسه پرداخت: <code>{payment.payment_id}</code>\n"
            f"شناسه رکورد: <code>{payment.pk}</code>"
        )
        kb = self.create_keyboard([[{
            "text": "✅ تأیید", "callback_data": f"admin_confirm_wallet_{payment.pk}",
        }, {
            "text": "❌ رد", "callback_data": f"admin_reject_wallet_{payment.pk}",
        }]])
        async for admin in self.get_brand_admin_recipients():
            try:
                sent = await self.bot.send_photo(
                    chat_id=admin.telegram_id, photo=payment.receipt_file,
                    caption=caption, parse_mode="HTML", reply_markup=kb,
                )
                if isinstance(getattr(sent, "message_id", None), int):
                    await AdminReceiptMessage.objects.aget_or_create(
                        payment=payment, chat_id=admin.telegram_id, message_id=sent.message_id,
                    )
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Challenge receipt delivery failed, payment %s", payment.pk)
