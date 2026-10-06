"""User-facing practical content: apps/downloads, setup guides, usage guides and FAQ."""

import os
from html import escape
from urllib.parse import urlparse

from aiogram import types
from aiogram.types import FSInputFile

from apps.support.models import UsefulContent

from .base import BaseHandler


class UsefulContentHandler(BaseHandler):
    CATEGORY_LABELS = {
        UsefulContent.Category.DOWNLOADS: "📥 فایل و لینک دانلود برنامه‌ها",
        UsefulContent.Category.INSTALL: "🧩 آموزش نصب و وارد کردن اشتراک",
        UsefulContent.Category.APP_USAGE: "📘 آموزش کار با برنامه",
        UsefulContent.Category.FAQ: "❓ سوالات متداول",
    }

    async def show_menu(self, callback: types.CallbackQuery):
        text = (
            "📚 <b>محتواهای کاربردی</b>\n\n"
            "بخش موردنظر را انتخاب کنید:"
        )
        rows = [
            [{"text": label, "callback_data": f"useful_category_{category}"}]
            for category, label in self.CATEGORY_LABELS.items()
        ]
        rows.append([{"text": "🔙 بازگشت", "callback_data": "main_menu"}])
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def show_category(self, callback: types.CallbackQuery, category: str):
        if category not in self.CATEGORY_LABELS:
            await callback.answer("❌ دسته‌بندی نامعتبر است.", show_alert=True)
            return

        items = []
        async for item in UsefulContent.objects.filter(
            brand=self.brand,
            category=category,
            is_active=True,
        ).order_by("display_order", "title")[:40]:
            items.append(item)

        title = self.CATEGORY_LABELS[category]
        if not items:
            text = f"{title}\n\nهنوز محتوایی برای این بخش ثبت نشده است."
            rows = [[{"text": "🔙 بازگشت", "callback_data": "useful_content"}]]
        else:
            text = f"{title}\n\nمورد موردنظر را انتخاب کنید:"
            rows = [
                [
                    {
                        "text": item.title[:55],
                        "callback_data": f"useful_item_{item.pk}",
                    }
                ]
                for item in items
            ]
            rows.append([{"text": "🔙 بازگشت", "callback_data": "useful_content"}])

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def show_item(self, callback: types.CallbackQuery, item_id: int):
        try:
            item = await UsefulContent.objects.aget(
                pk=item_id, brand=self.brand, is_active=True
            )
        except UsefulContent.DoesNotExist:
            await callback.answer("❌ محتوا یافت نشد.", show_alert=True)
            return

        label = self.CATEGORY_LABELS.get(item.category, "📚 محتوا")
        lines = [label, "", f"<b>{escape(item.title)}</b>"]
        if item.description:
            lines.extend(["", escape(item.description[:900])])
        if item.content:
            body = item.content[:2600]
            if len(item.content) > len(body):
                body += "…"
            lines.extend(["", escape(body)])

        rows = []
        if self._safe_public_url(item.download_url):
            rows.append([{"text": "🔗 باز کردن لینک / دانلود", "url": item.download_url}])

        remote_file_url = ""
        if item.file:
            try:
                candidate = item.file.url
            except Exception:
                candidate = ""
            if self._safe_public_url(candidate):
                remote_file_url = candidate
                rows.append([{"text": "📥 دانلود فایل", "url": candidate}])

        rows.append(
            [
                {
                    "text": "🔙 بازگشت",
                    "callback_data": f"useful_category_{item.category}",
                }
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            "\n".join(lines),
            self.create_keyboard(rows),
        )

        # Local storage files can be delivered directly even when MEDIA_URL is not public.
        if item.file and not remote_file_url:
            try:
                local_path = item.file.path
            except Exception:
                local_path = ""
            if local_path and os.path.isfile(local_path):
                await self.bot.send_document(
                    callback.message.chat.id,
                    FSInputFile(local_path, filename=os.path.basename(item.file.name)),
                    caption=escape(item.title),
                )
        await callback.answer()

    @staticmethod
    def _safe_public_url(value: str) -> bool:
        if not value:
            return False
        try:
            parsed = urlparse(value)
        except ValueError:
            return False
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
