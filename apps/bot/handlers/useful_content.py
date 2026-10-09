"""Academy, platform-specific app downloads and explicit installer delivery."""

import logging
import os
from html import escape
from urllib.parse import urlparse

from aiogram.types import FSInputFile
from asgiref.sync import sync_to_async
from django.conf import settings

from apps.support.models import UsefulContent
from .base import BaseHandler

logger = logging.getLogger(__name__)


class UsefulContentHandler(BaseHandler):
    CATEGORY_LABELS = {
        UsefulContent.Category.DOWNLOADS: "📥 فایل و لینک دانلود برنامه‌ها",
        UsefulContent.Category.INSTALL: "🧩 آموزش نصب و وارد کردن اشتراک",
        UsefulContent.Category.APP_USAGE: "📘 آموزش کار با برنامه",
        UsefulContent.Category.FAQ: "❓ سوالات متداول",
        UsefulContent.Category.INTERNET_TIPS: "نکاتی برای افزایش کیفیت و سرعت اینترنت شما",
    }
    PLATFORM_LABELS = {
        "iphone": "آیفون",
        "android": "اندروید",
        "windows": "ویندوز",
        "macos": "مکینتاش",
        "linux": "لینوکس",
    }
    APP_LABELS = {"connectix": "کانکتیکس", "v2rayng": "v2rayNG", "happ": "Happ"}

    async def _render(self, callback, text, rows):
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def show_menu(self, callback):
        rows = [
            [{"text": label, "callback_data": f"useful_category_{key}"}]
            for key, label in self.CATEGORY_LABELS.items()
        ]
        rows.append([{"text": "🔙 بازگشت", "callback_data": "main_menu"}])
        await self._render(
            callback,
            "📚 <b>آکادمی(محتواهای کاربردی)</b>\n\nبخش موردنظر را انتخاب کنید:",
            rows,
        )

    async def show_category(self, callback, category):
        if category not in self.CATEGORY_LABELS:
            await callback.answer("دسته‌بندی معتبر نیست.", show_alert=True)
            return
        if category == UsefulContent.Category.DOWNLOADS:
            await self._render(
                callback,
                self.CATEGORY_LABELS[category],
                [
                    [
                        {
                            "text": "۱. دریافت اپلیکیشن اصلی ما(کانکتیکس)",
                            "callback_data": "academy_app_connectix",
                        }
                    ],
                    [
                        {
                            "text": "۲. دریافت سایر اپلیکیشن‌های کاربردی",
                            "callback_data": "academy_other_apps",
                            "style": "primary",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "useful_content"}],
                ],
            )
            return
        items = [
            item
            async for item in UsefulContent.objects.filter(
                brand=self.brand, category=category, is_active=True
            ).order_by("display_order", "title")
        ]
        rows = [
            [{"text": item.title[:100], "callback_data": f"useful_item_{item.pk}"}]
            for item in items[:60]
        ]
        rows.append([{"text": "🔙 بازگشت", "callback_data": "useful_content"}])
        text = (
            self.CATEGORY_LABELS[category]
            + "\n\n"
            + (
                "مورد موردنظر را انتخاب کنید:"
                if items
                else "هنوز محتوایی برای این بخش ثبت نشده است."
            )
        )
        await self._render(callback, text, rows)

    async def show_other_apps(self, callback):
        rows = [
            [{"text": self.APP_LABELS[key], "callback_data": f"academy_app_{key}"}]
            for key in ("v2rayng", "happ")
        ]
        legacy = UsefulContent.objects.filter(
            brand=self.brand, category="downloads", app_family="", is_active=True
        ).order_by("display_order", "title")
        rows.extend(
            [
                [{"text": item.title[:100], "callback_data": f"useful_item_{item.pk}"}]
                async for item in legacy[:40]
            ]
        )
        rows.append(
            [{"text": "🔙 بازگشت", "callback_data": "useful_category_downloads"}]
        )
        await self._render(
            callback,
            "📥 <b>دریافت سایر اپلیکیشن‌های کاربردی</b>\n\nاپلیکیشن را انتخاب کنید:",
            rows,
        )

    async def show_app(self, callback, family, platform=""):
        if family not in self.APP_LABELS or (
            platform and platform not in self.PLATFORM_LABELS
        ):
            await callback.answer("گزینه معتبر نیست.", show_alert=True)
            return
        queryset = UsefulContent.objects.filter(
            brand=self.brand, category="downloads", app_family=family, is_active=True
        ).order_by("display_order", "title")
        items = [item async for item in queryset]
        if platform:
            items = [item for item in items if item.platform == platform]
        if platform and len(items) == 1 and family != "v2rayng":
            await self.show_item(callback, items[0].pk)
            return
        rows = []
        if platform:
            rows = [
                [{"text": item.title[:100], "callback_data": f"useful_item_{item.pk}"}]
                for item in items
            ]
            back = f"academy_app_{family}"
        else:
            for key, label in self.PLATFORM_LABELS.items():
                matching = [item for item in items if item.platform == key]
                if not matching:
                    continue
                rows.append(
                    [
                        {
                            "text": f"{self.APP_LABELS[family]} {label}",
                            "callback_data": f"academy_platform_{family}_{key}"
                            if len(matching) > 1
                            else f"useful_item_{matching[0].pk}",
                        }
                    ]
                )
            back = (
                "useful_category_downloads"
                if family == "connectix"
                else "academy_other_apps"
            )
        rows.append([{"text": "🔙 بازگشت", "callback_data": back}])
        await self._render(
            callback,
            f"📥 <b>{self.APP_LABELS[family]}</b>\n\nنسخه مناسب دستگاه خود را انتخاب کنید:",
            rows,
        )

    async def _item(self, item_id):
        return await UsefulContent.objects.filter(
            pk=item_id, brand=self.brand, is_active=True
        ).afirst()

    def _item_back(self, item):
        if item.app_family in {"connectix", "v2rayng"} and item.platform == "android":
            return f"academy_platform_{item.app_family}_android"
        if item.app_family:
            return f"academy_app_{item.app_family}"
        return f"useful_category_{item.category}"

    def _environment_file_id(self, item):
        if self.brand.bot_token != settings.TELEGRAM_BOT_TOKEN:
            return ""
        return settings.ACADEMY_TELEGRAM_FILE_IDS.get(
            (item.app_family, item.platform, item.variant), ""
        )

    def _environment_link(self, item):
        if (
            item.app_family == "connectix"
            and item.platform == "linux"
            and self.brand.bot_token == settings.TELEGRAM_BOT_TOKEN
            and settings.CONNECTIX_LINUX_DOWNLOAD_URL
        ):
            return settings.CONNECTIX_LINUX_DOWNLOAD_URL
        return item.download_url

    async def show_item(self, callback, item_id):
        item = await self._item(item_id)
        if not item:
            await callback.answer("محتوا یافت نشد.", show_alert=True)
            return
        item.download_url = self._environment_link(item)
        lines = [f"<b>{escape(item.title)}</b>"]
        if item.description:
            lines.extend(["", escape(item.description[:700])])
        if item.content:
            lines.extend(["", escape(item.content[:2300])])
        rows = []
        if self._safe_public_url(item.download_url):
            lines.extend(["", "🔗 لینک دریافت:", item.download_url])
            rows.append([{"text": "🔗 دریافت از لینک", "url": item.download_url}])
        if item.platform not in {"iphone", "macos"} and (
            item.app_family or item.file or item.installer_url or item.telegram_file_id
        ):
            rows.append(
                [
                    {
                        "text": "📥 دریافت مستقیم فایل نصب",
                        "callback_data": f"academy_file_{item.pk}",
                    }
                ]
            )
        rows.append([{"text": "🔙 بازگشت", "callback_data": self._item_back(item)}])
        await self._render(callback, "\n".join(lines), rows)

    async def send_installer(self, callback, item_id):
        item = await self._item(item_id)
        if not item or item.platform in {"iphone", "macos"}:
            await callback.answer(
                "برای این نسخه از لینک نصب استفاده کنید.", show_alert=True
            )
            return
        item.download_url = self._environment_link(item)
        environment_file_id = self._environment_file_id(item)
        await callback.answer("📥 در حال آماده‌سازی فایل…")
        try:
            if environment_file_id or item.telegram_file_id:
                document = environment_file_id or item.telegram_file_id
            else:
                from apps.support.downloads import prepare_installer

                await sync_to_async(prepare_installer)(item)
                if not item.file:
                    await self.send_message_with_keyboard(
                        callback.message.chat.id,
                        "فایل نصب این نسخه هنوز آماده نشده است. از لینک دریافت استفاده کنید.",
                        self.create_keyboard(
                            [[{"text": "🔗 لینک دریافت", "url": item.download_url}]]
                        ),
                    )
                    return
                document = FSInputFile(
                    item.file.path, filename=os.path.basename(item.file.name)
                )
            sent = await self.bot.send_document(
                callback.message.chat.id, document, caption=escape(item.title[:255])
            )
            if sent.document and not environment_file_id:
                await UsefulContent.objects.filter(
                    pk=item.pk,
                    installer_url=item.installer_url,
                    file=item.file.name if item.file else "",
                ).aupdate(telegram_file_id=sent.document.file_id)
        except Exception as exc:
            from apps.support.downloads import InstallerTooLarge

            logger.warning(
                "Installer delivery for content %s failed (%s)",
                item.pk,
                type(exc).__name__,
            )
            text = "این فایل برای ارسال مستقیم آماده نیست؛ لطفاً از لینک دانلود استفاده کنید."
            if isinstance(exc, InstallerTooLarge):
                text = "نسخه کامل این برنامه را از لینک زیر دریافت کنید. برای ارسال فایل آن در ربات، با پشتیبانی تماس بگیرید."
            rows = [
                [{"text": "🔗 دریافت از لینک", "url": item.download_url}],
                [{"text": "🛟 پشتیبانی", "callback_data": "support"}],
            ]
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, self.create_keyboard(rows)
            )

    @staticmethod
    def _safe_public_url(value):
        try:
            parsed = urlparse(value or "")
        except ValueError:
            return False
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
