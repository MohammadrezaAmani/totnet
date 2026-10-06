"""Propzino challenge presentation and user actions."""

from __future__ import annotations

import math
from html import escape

from aiogram import types
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.orders.models import Wallet
from apps.referrals.gamification import (
    ChallengeError,
    activate_challenge_from_wallet,
    choose_challenge_tier,
    decline_challenge,
)
from apps.referrals.models import ChallengeProgram, UserChallenge

from .base import BaseHandler


class ChallengesHandler(BaseHandler):
    def _conditions(self, program: ChallengeProgram) -> str:
        if program.require_referral_join_during_challenge:
            referral_rule = (
                "معرفی موفق یعنی کاربر در مهلت چالش با لینک شما وارد ربات شود و "
                "خرید موفق ثبت کند."
            )
        else:
            referral_rule = (
                "معرفی موفق یعنی کاربر معرفی‌شده در مهلت چالش خرید موفق ثبت کند؛ "
                "زمان ورود اولیه او به ربات محدودکننده نیست."
            )

        custom_intro = ""
        if program.intro_text:
            custom_intro = f"\n\n{escape(program.intro_text[:1200])}"

        return (
            f"🏆 <b>ویژگی‌های چالش {escape(program.name)}</b>\n\n"
            "• شرکت در چالش هیچ ضرری برای شما ندارد؛ اصل وجه ورودی در پایان برمی‌گردد.\n"
            f"• پاداش معرفی تا سقف تارگت با نرخ ویژه <b>{program.reward_percent:g}٪</b> محاسبه می‌شود.\n"
            "• با پاس کردن کامل چالش، ارزش نقدی امتیازهای چالش مستقیم به موجودی نقد کیف پول منتقل می‌شود.\n\n"
            "<b>توضیح چالش:</b>\n"
            "1- بعد از مطالعه شرایط، سطح چالش خود را انتخاب کنید.\n"
            f"2- از زمان پرداخت ورودی، <b>{program.duration_days} روز</b> برای تکمیل چالش فرصت دارید.\n"
            "3- اگر تارگت را کامل کنید، امتیازهای چالش بدون شرط سه‌امتیازی مستقیماً نقد می‌شوند.\n"
            "4- اگر چالش کامل نشود، برای معرفی‌های داخل تارگت چالش امتیاز یا درصدی پرداخت نمی‌شود.\n"
            f"5- {referral_rule}\n"
            "6- مبلغ ورودی بر اساس سطح انتخابی از موجودی نقد کیف پول کسر و در «وجه فیریز شده» نگهداری می‌شود.\n"
            "7- در موفقیت یا عدم موفقیت، اصل مبلغ ورودی به موجودی نقد کیف پول بازمی‌گردد.\n"
            "8- معرفی‌های بیشتر از تارگت انتخابی با درصد عادی سیستم و قانون سه امتیاز محاسبه می‌شوند.\n"
            "9- این فرصت فقط یک بار ارائه می‌شود؛ سطحی را انتخاب کنید که واقعاً می‌توانید کامل کنید."
            f"{custom_intro}"
        )

    async def _current(self, user):
        return await (
            UserChallenge.objects.filter(user=user, brand=self.brand)
            .select_related("program", "tier")
            .order_by("-created_at")
            .afirst()
        )

    async def _frozen_balance(self, user):
        wallet = await Wallet.objects.filter(user=user, brand=self.brand).afirst()
        return wallet.challenge_frozen_balance if wallet else 0

    async def show_active_challenges(self, callback: types.CallbackQuery):
        user, _ = await self.get_or_create_user(callback.from_user)
        challenge = await self._current(user)
        frozen_balance = await self._frozen_balance(user)

        if not challenge:
            text = f"""
🏆 <b>چالش‌های فعال</b>

🧊 <b>وجه فیریز شده:</b> {self.format_price(frozen_balance, self.brand.currency)}

در حال حاضر چالش فعالی برای شما ثبت نشده است.
            """
            rows = [[{"text": "🔙 بازگشت", "callback_data": "main_menu"}]]
        elif challenge.status == UserChallenge.Status.OFFERED:
            text = f"""
🏆 <b>چالش ویژه {escape(challenge.program.name)}</b>

امکان شرکت در این چالش فقط و فقط همین یکبار برای شما امکان‌پذیر است.
مایل به دریافت شرایط شرکت در این چالش هستید؟
            """
            rows = [
                [
                    {"text": "✅ بله", "callback_data": f"challenge_accept_{challenge.pk}"},
                    {"text": "❌ خیر", "callback_data": f"challenge_decline_{challenge.pk}"},
                ],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        elif challenge.status == UserChallenge.Status.AWAITING_FUNDS:
            tier_text = (
                f"{challenge.target_referrals or challenge.tier.target_referrals} معرفی موفق"
                if challenge.tier_id
                else "هنوز انتخاب نشده"
            )
            text = f"""
🏆 <b>{escape(challenge.program.name)}</b>

سطح انتخابی: {tier_text}
💳 مبلغ ورودی: {self.format_price(challenge.entry_amount, self.brand.currency)}
🧊 وجه فیریز شده فعلی: {self.format_price(frozen_balance, self.brand.currency)}

برای شروع مهلت چالش، مبلغ ورودی باید از موجودی نقد کیف پول به وجه فیریز شده منتقل شود.
            """
            rows = []
            if challenge.tier_id:
                rows.append([{"text": "💳 پرداخت از کیف پول و شروع چالش", "callback_data": f"challenge_pay_{challenge.pk}"}])
            rows.extend(
                [
                    [{"text": "💰 شارژ کیف پول", "callback_data": "charge_wallet"}],
                    [{"text": "📋 تغییر سطح چالش", "callback_data": f"challenge_accept_{challenge.pk}"}],
                    [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
                ]
            )
        elif challenge.status == UserChallenge.Status.ACTIVE:
            now = timezone.now()
            remaining_days = max(0, math.ceil((challenge.ends_at - now).total_seconds() / 86400)) if challenge.ends_at else 0
            target = challenge.target_referrals or (challenge.tier.target_referrals if challenge.tier_id else 0)
            referral_rule = (
                "فقط کاربرانی که پس از شروع چالش با لینک شما وارد ربات شوند و در مهلت چالش خرید کنند، داخل تارگت حساب می‌شوند."
                if challenge.program.require_referral_join_during_challenge
                else "خرید موفق کاربران معرفی‌شده در مهلت چالش داخل تارگت حساب می‌شود."
            )
            text = f"""
🏆 <b>{escape(challenge.program.name)} — فعال</b>

🎯 هدف: {target} معرفی موفق
✅ پیشرفت: {challenge.successful_referrals} از {target}
⏳ زمان باقی‌مانده: {remaining_days} روز
💎 درصد پاداش چالش: {(challenge.reward_percent or challenge.program.reward_percent):g}٪ از خرید مستقیم
🧊 <b>وجه فیریز شده:</b> {self.format_price(frozen_balance, self.brand.currency)}

{referral_rule}
بعد از تکمیل تارگت، معرفی‌های بیشتر با درصد عادی سیستم محاسبه خواهند شد.
            """
            rows = [
                [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                [{"text": "🏅 درجه پزشکی و امتیازات", "callback_data": "rewards"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        elif challenge.status == UserChallenge.Status.DECLINED:
            text = f"""
🏆 <b>چالش ویژه {escape(challenge.program.name)}</b>

این فرصت قبلاً رد شده و مطابق قانون یک‌بار ارائه‌شدن چالش، از دسترس شما خارج شده است.
🧊 <b>وجه فیریز شده:</b> {self.format_price(frozen_balance, self.brand.currency)}
            """
            rows = [[{"text": "🔙 بازگشت", "callback_data": "main_menu"}]]
        else:
            status_text = (
                "✅ با موفقیت تکمیل شد"
                if challenge.status == UserChallenge.Status.SUCCEEDED
                else "⛔ بدون تکمیل تارگت به پایان رسید"
            )
            reward_line = (
                f"\n💰 پاداش نقدی: {self.format_price(challenge.reward_amount, self.brand.currency)}"
                if challenge.status == UserChallenge.Status.SUCCEEDED
                else ""
            )
            text = f"""
🏆 <b>{escape(challenge.program.name)}</b>

{status_text}{reward_line}
🧊 <b>وجه فیریز شده:</b> {self.format_price(frozen_balance, self.brand.currency)}

فرصت این چالش یک‌باره بوده و وضعیت نهایی آن ثبت شده است.
            """
            rows = [
                [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def accept_challenge(self, callback: types.CallbackQuery, challenge_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        challenge = await UserChallenge.objects.filter(
            pk=challenge_id,
            user=user,
            brand=self.brand,
            status__in=[UserChallenge.Status.OFFERED, UserChallenge.Status.AWAITING_FUNDS],
        ).select_related("program").afirst()
        if not challenge:
            await callback.answer("این پیشنهاد دیگر در دسترس نیست.", show_alert=True)
            return
        tiers = []
        async for tier in challenge.program.tiers.filter(is_active=True).order_by("display_order", "target_referrals"):
            tiers.append(tier)
        text = (
            f"{self._conditions(challenge.program)}\n\n"
            f"💎 درصد ویژه این دوره: <b>{challenge.program.reward_percent:g}٪</b>\n\n"
            "<b>سطح چالش را انتخاب کنید:</b>"
        )
        rows = [
            [
                {
                    "text": f"{tier.target_referrals} معرفی موفق — ورودی {self.format_price(tier.entry_fee, self.brand.currency)}",
                    "callback_data": f"challenge_tier_{tier.pk}",
                }
            ]
            for tier in tiers
        ]
        if not tiers:
            text += "\n\n⚠️ هنوز سطح فعالی برای این چالش تعریف نشده است."
        rows.append([{"text": "🔙 بازگشت", "callback_data": "active_challenges"}])
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def decline_challenge(self, callback: types.CallbackQuery, challenge_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        challenge = await UserChallenge.objects.filter(
            pk=challenge_id, user=user, brand=self.brand, status=UserChallenge.Status.OFFERED
        ).select_related("program").afirst()
        if not challenge:
            await callback.answer("این پیشنهاد دیگر در دسترس نیست.", show_alert=True)
            return
        try:
            await sync_to_async(decline_challenge, thread_sensitive=True)(
                user_id=user.pk, brand_id=self.brand.pk
            )
        except ChallengeError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            f"چالش {escape(challenge.program.name)} از دسترس شما خارج شد.",
            self.create_keyboard([[{"text": "🔙 منوی اصلی", "callback_data": "main_menu"}]]),
        )
        await callback.answer()

    async def select_tier(self, callback: types.CallbackQuery, tier_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            challenge = await sync_to_async(choose_challenge_tier, thread_sensitive=True)(
                user_id=user.pk, brand_id=self.brand.pk, tier_id=tier_id
            )
        except ChallengeError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        challenge = await UserChallenge.objects.select_related("tier", "program").aget(pk=challenge.pk)
        text = f"""
✅ <b>سطح چالش انتخاب شد</b>

🎯 هدف: {challenge.target_referrals or challenge.tier.target_referrals} معرفی موفق
💳 مبلغ ورودی: {self.format_price(challenge.entry_amount, self.brand.currency)}
⏳ مهلت: {challenge.program.duration_days} روز از زمان پرداخت

مبلغ ورودی از موجودی نقد کیف پول کسر و تا پایان چالش در بخش «وجه فیریز شده» نگهداری می‌شود. در هر دو حالت موفقیت یا عدم موفقیت، اصل مبلغ ورودی به کیف پول شما بازمی‌گردد.
        """
        rows = [
            [{"text": "💳 پرداخت از کیف پول و شروع", "callback_data": f"challenge_pay_{challenge.pk}"}],
            [{"text": "💰 شارژ کیف پول", "callback_data": "charge_wallet"}],
            [{"text": "🔙 تغییر سطح", "callback_data": f"challenge_accept_{challenge.pk}"}],
        ]
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def pay_challenge(self, callback: types.CallbackQuery, challenge_id: int):
        user, _ = await self.get_or_create_user(callback.from_user)
        exists = await UserChallenge.objects.filter(
            pk=challenge_id,
            user=user,
            brand=self.brand,
            status=UserChallenge.Status.AWAITING_FUNDS,
        ).aexists()
        if not exists:
            await callback.answer("چالشی در انتظار پرداخت پیدا نشد.", show_alert=True)
            return
        try:
            challenge = await sync_to_async(activate_challenge_from_wallet, thread_sensitive=True)(
                user_id=user.pk, brand_id=self.brand.pk
            )
        except ChallengeError as exc:
            text = str(exc)
            if "کافی نیست" in text:
                await callback.answer(text, show_alert=True)
                return
            await callback.answer(text, show_alert=True)
            return
        challenge = await UserChallenge.objects.select_related("program", "tier").aget(pk=challenge.pk)
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            (
                f"✅ <b>چالش {escape(challenge.program.name)} شروع شد.</b>\n\n"
                f"🎯 هدف: {challenge.target_referrals or challenge.tier.target_referrals} معرفی موفق\n"
                f"⏳ مهلت: {challenge.program.duration_days} روز\n"
                f"🧊 وجه ورودی {self.format_price(challenge.entry_amount, self.brand.currency)} فیریز شد.\n\n"
                "از همین لینک معرفی فعلی خودتان استفاده کنید."
            ),
            self.create_keyboard(
                [
                    [{"text": "👥 معرفی دوستان", "callback_data": "referral_system"}],
                    [{"text": "🏆 وضعیت چالش", "callback_data": "active_challenges"}],
                ]
            ),
        )
        await callback.answer("چالش شروع شد.")
