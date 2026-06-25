"""
Help Handler for Multi-Tenant VPN Bot
Handles help, information, terms, and about sections
"""

import logging

from aiogram import types

from .base import BaseHandler

logger = logging.getLogger(__name__)


class HelpHandler(BaseHandler):
    """Handle help, information, and general bot usage"""

    async def show_help_menu(self, callback: types.CallbackQuery):
        """Show help and information menu"""
        text = f"""
📚 راهنما و اطلاعات

سلام! به <b>{self.brand.name}</b> خوش آمدید.

لطفاً موضوع مورد نظر خود را انتخاب کنید:
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "❓ راهنمای استفاده", "callback_data": "help_usage"}],
                [{"text": "ℹ️ درباره برنامه", "callback_data": "about"}],
                [{"text": "📋 شرایط استفاده", "callback_data": "terms"}],
                [{"text": "🔒 سیاست حفاظت از حریم", "callback_data": "privacy"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit help menu: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_usage_guide(self, callback: types.CallbackQuery):
        """Show usage guide and getting started"""
        text = f"""
❓ راهنمای استفاده {self.brand.name}

<b>🚀 شروع سریع:</b>

۱. <b>خرید اشتراک:</b>
   • به منوی اصلی بروید
   • گزینه "خرید اشتراک" را انتخاب کنید
   • یک پلن را انتخاب کنید
   • روش پرداخت را انتخاب کنید
   • پس از تأیید پرداخت، اشتراک فعال می‌شود

۲. <b>دریافت تنظیمات:</b>
   • در بخش "اشتراک‌های من" وارد شوید
   • اشتراک خود را انتخاب کنید
   • دریافت تنظیمات (Clash, Clash Meta, V2Ray, etc.)

۳. <b>راه‌اندازی VPN:</b>
   • یکی از اپلیکیشن‌های پشتیبانی شده را نصب کنید
   • تنظیمات را کپی کنید
   • در اپلیکیشن اضافه کنید
   • اتصال برقرار کنید

<b>📱 اپلیکیشن‌های پشتیبانی:</b>
   • Clash (iOS/Android)
   • Clash Meta (iOS/Android)
   • V2Ray (iOS/Android)
   • Shadowrocket (iOS)
   • Quantumult (iOS)
   • Surge (iOS)
   • Surfboard (Android)
   • SSR (Android)

<b>⏱️ مدت زمان سرویس:</b>
   • ۱ روز، ۱ هفته، ۱ ماه، ۳ ماه، ۱ سال
   • بعد از انقضا می‌توانید تمدید کنید

<b>💡 نکات مفید:</b>
   • همه اشتراک‌ها نامحدود هستند
   • می‌توانید چند اشتراک داشته باشید
   • تغییر سرور بدون محدودیت
   • پشتیبانی ۲۴/۷

<b>🆘 مشکل دارید؟</b>
   • بخش پشتیبانی را ببینید
   • سوالات متداول را بررسی کنید
   • تیکت پشتیبانی ایجاد کنید
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🛟 پشتیبانی", "callback_data": "support"}],
                [{"text": "❓ سوالات متداول", "callback_data": "faq"}],
                [{"text": "🔙 بازگشت", "callback_data": "help"}],
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

    async def show_about(self, callback: types.CallbackQuery):
        """Show about information"""
        text = f"""
ℹ️ درباره {self.brand.name}

<b>👋 خوش آمدید!</b>

ما یک سرویس VPN پیشرفته هستیم که بیش از {self.brand.name} کاربر را سرو می‌کنیم.

<b>📊 مشخصات:</b>
• سرورهای پرسرعت در سراسر جهان
• ترافیک نامحدود
• اتصالات متعدد همزمان
• سرعت تا ۱ گیگابایت بر ثانیه
• رمزنگاری نسل ۵
• بدون کار در آپ‌لاگ (No-Log Policy)

<b>🌍 پوشش:</b>
• ۵۰+ سرور در ۲۰+ کشور
• بهترین سرعت در منطقه خاورمیانه
• سرورهای سریع برای استرمینگ
• سرورهای مخصوص گیمینگ

<b>🔒 امنیت:</b>
• رمزنگاری OpenVPN
• Wireguard Protocol
• V2Ray Advanced
• کوکی‌های داینامیکی
• کلید تبادلی نامحدود

<b>💰 قیمت‌گذاری:**
منصفانه و رقابتی
قابل دسترسی برای همه
اشتراک‌های مختلف برای نیازهای مختلف

<b>🎯 حمایت:</b>
پشتیبانی ۲۴/۷ از طریق:
• تیکت پشتیبانی
• چت زنده
• ایمیل

<b>📱 دنبال کنید:</b>
• تلگرام: @{self.brand.bot_username or self.brand.slug}
• وبسایت: {self.brand.website or "www.example.com"}

با تشکر از اعتماد شما! 🙏
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🛍️ خرید اشتراک", "callback_data": "purchase_subscription"}],
                [{"text": "📞 تماس با ما", "callback_data": "contact_info"}],
                [{"text": "🔙 بازگشت", "callback_data": "help"}],
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

    async def show_terms(self, callback: types.CallbackQuery):
        """Show terms of service"""
        text = f"""
📋 شرایط استفاده {self.brand.name}

<b>۱. قبول شرایط:</b>
با استفاده از سرویس ما، شما موافقت می‌کنید با تمام شرایط و قوانین.

<b>۲. استفاده قانونی:</b>
• سرویس تنها برای اهداف قانونی استفاده شود
• هکینگ، اسپم، و فعالیت‌های مخرب ممنوع است
• نقض حقوق دیگران ممنوع است

<b>۳. دقت کالا:</b>
• ما تضمین می‌دهیم سرویس ۹۹.۹% بالا است
• خسارات ناشی از قطع یا کند سرویس محدود است
• ما برای دسترسی نامشروع پاسخگو نیستیم

<b>۴. حریم خصوصی:</b>
• ما سیاست عدم ثبت (No-Log) داریم
• داده‌های شخصی محفوظ است
• بیش‌تر اطلاعات: بخش سیاست حفاظت از حریم

<b>۵. سرویس دهنده:</b>
• ما می‌توانیم سرویس را بدون اطلاع قطع کنیم
• به‌روزرسانی‌های فنی می‌تواند سرویس را متوقف کند
• ما مسئول داده‌های از دست رفته نیستیم

<b>۶. پرداخت‌ها:</b>
• تمام پرداخت‌ها نهایی هستند
• هیچ بازپرداختی پس از خریدی نیست
• اما اعتبارات می‌توانند برای خریدهای آتی استفاده شوند

<b>۷. محدودیت‌های مسئولیت:</b>
• ما برای خسارات غیرمستقیم پاسخگو نیستیم
• ما برای دقت محتوای شخص ثالث پاسخگو نیستیم

<b>۸. تغییرات:</b>
• ما می‌توانیم شرایط را هر زمان تغییر دهیم
• تغییرات از طریق بات اعلام می‌شود

درج‌ بروز: {self.brand.updated_at.strftime("%Y/%m/%d") if hasattr(self.brand, "updated_at") else "2024"}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🔒 سیاست حفاظت", "callback_data": "privacy"}],
                [{"text": "🔙 بازگشت", "callback_data": "help"}],
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

    async def show_privacy(self, callback: types.CallbackQuery):
        """Show privacy policy"""
        text = f"""
🔒 سیاست حفاظت از حریم {self.brand.name}

<b>۱. اطلاعات که جمع‌آوری می‌کنیم:</b>

الف) <b>در هنگام ثبت‌نام:</b>
   • شماره تلفن (اختیاری)
   • نام کاربری
   • آدرس ایمیل
   • اطلاعات IP (فقط برای منطقه)

ب) <b>هنگام استفاده سرویس:</b>
   • زمان اتصال و قطع
   • سرور انتخابی
   • میزان داده‌های مصرفی
   • اطلاعات دستگاه (نوع OS)

ج) <b>جهت بهبود سرویس:</b>
   • آمار استفاده
   • پیام‌های خرابی
   • بازخورد کاربر

<b>۲. اطلاعات که جمع‌آوری نمی‌کنیم:</b>
   • محتوای صفحات بازدید شده ❌
   • سایت‌های مشاهده‌شده ❌
   • جستجوهای اینترنتی ❌
   • اطلاعات دانلودی ❌
   • جریان ترافیک ❌

<b>۳. استفاده اطلاعات:</b>
   • بهبود سرویس و تجربه کاربر
   • پشتیبانی و تعمیر
   • پیام‌های اطلاعات مهم
   • محافظت از تقلب و سوء استفاده

<b>۴. کوکی‌ها:</b>
   • از کوکی‌های ضروری استفاده می‌کنیم
   • کوکی‌های تبلیغاتی استفاده نمی‌کنیم
   • می‌توانید کوکی‌ها را غیرفعال کنید

<b>۵. اشتراک اطلاعات:</b>
   • معمولاً اطلاعات را اشتراک نمی‌کنیم
   • فقط برای نیازهای قانونی اشتراک می‌کنیم
   • شرکای معتمد فقط

<b>۶. حفاظت داده‌ها:</b>
   • رمزنگاری SSL/TLS
   • سرورهای ایمن
   • نیروی کار محدود دسترسی
   • بکاپ منظم

<b>۷. حقوق شما:</b>
   • حق دسترسی به داده‌های خود
   • حق تصحیح داده‌های نادرست
   • حق حذف اطلاعات شخصی
   • حق مخالفت با پردازش

<b>۸. تماس با ما:</b>
درباره حریم خصوصی اطلاعات داریید؟
   📧 privacy@{self.brand.contact_email or "example.com"}

بروز‌رسانی: 2024
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📋 شرایط استفاده", "callback_data": "terms"}],
                [{"text": "🔙 بازگشت", "callback_data": "help"}],
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
