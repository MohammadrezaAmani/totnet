"""Three-step service survey shown after the first purchase."""

from aiogram import types
from django.utils import timezone

from apps.referrals.models import ServiceSurvey

from .base import BaseHandler


class SurveyHandler(BaseHandler):
    STEPS = (
        ("quality", "quality_rating", "کیفیت، سرعت و پایداری سرویس"),
        ("app", "app_rating", "امکانات برنامه و راحتی استفاده"),
        ("support", "support_rating", "پشتیبانی"),
    )

    async def _survey(self, user):
        survey, _ = await ServiceSurvey.objects.aget_or_create(user=user, brand=self.brand)
        return survey

    def _rating_keyboard(self, key: str):
        return self.create_keyboard(
            [
                [
                    {"text": str(score), "callback_data": f"survey_rate_{key}_{score}"}
                    for score in range(1, 6)
                ],
                [{"text": "🔙 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

    async def start(self, callback: types.CallbackQuery):
        user, _ = await self.get_or_create_user(callback.from_user)
        survey = await self._survey(user)
        if survey.completed_at:
            await callback.answer("این نظرسنجی قبلاً ثبت شده است.", show_alert=True)
            return
        if not survey.started_at:
            survey.started_at = timezone.now()
            await survey.asave(update_fields=["started_at", "updated_at"])
        await self._show_step(callback, "quality", answer=False)
        await callback.answer()

    async def _show_step(self, callback: types.CallbackQuery, key: str, *, answer: bool = True):
        title = next(item[2] for item in self.STEPS if item[0] == key)
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            f"⭐ <b>{title}</b>\n\nاز ۱ تا ۵ چه امتیازی می‌دهید؟",
            self._rating_keyboard(key),
        )
        if answer:
            await callback.answer()

    async def rate(self, callback: types.CallbackQuery, key: str, score: int):
        if key not in {item[0] for item in self.STEPS} or score not in range(1, 6):
            await callback.answer("امتیاز نامعتبر است.", show_alert=True)
            return
        user, _ = await self.get_or_create_user(callback.from_user)
        survey = await self._survey(user)
        if survey.completed_at:
            await callback.answer("این نظرسنجی قبلاً ثبت شده است.", show_alert=True)
            return
        field = next(item[1] for item in self.STEPS if item[0] == key)
        setattr(survey, field, score)
        if not survey.started_at:
            survey.started_at = timezone.now()
        await survey.asave(update_fields=[field, "started_at", "updated_at"])

        order = [item[0] for item in self.STEPS]
        index = order.index(key)
        if index + 1 < len(order):
            await self._show_step(callback, order[index + 1], answer=False)
            await callback.answer("ثبت شد.")
            return

        # Only complete once all three values exist; callbacks can be opened out of sequence.
        await survey.arefresh_from_db()
        if all((survey.quality_rating, survey.app_rating, survey.support_rating)):
            survey.completed_at = timezone.now()
            await survey.asave(update_fields=["completed_at", "updated_at"])
            await self.edit_message_with_keyboard(
                callback.message.chat.id,
                callback.message.message_id,
                (
                    "✅ بابت نظرات ارزشمند شما ممنونیم.\n"
                    "تلاش ما همواره ارائه بهترین سرویس ممکن بوده و هست.\n\n"
                    "در صورت نیاز به راهنمایی تیکت ثبت کنید."
                ),
                self.create_keyboard(
                    [
                        [{"text": "🎫 ثبت تیکت", "callback_data": "create_ticket"}],
                        [{"text": "🔙 منوی اصلی", "callback_data": "main_menu"}],
                    ]
                ),
            )
            await callback.answer("نظرسنجی ثبت شد.")
        else:
            await self._show_step(callback, "quality", answer=False)
            await callback.answer("برای تکمیل، هر سه بخش را امتیاز دهید.")
