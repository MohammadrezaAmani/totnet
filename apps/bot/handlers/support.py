"""
Support Handler for Multi-Tenant VPN Bot
Handles support tickets, FAQ, and customer service
"""

import logging

from aiogram import types

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.support.models import SupportCategory, SupportKnowledgeBase, SupportTicket

from .base import BaseHandler

logger = logging.getLogger(__name__)


class SupportHandler(BaseHandler):
    """Handle support and customer service operations"""

    async def show_support_menu(self, callback: types.CallbackQuery):
        """Show main support menu"""
        user, _ = await self.get_or_create_user(callback.from_user)

        open_tickets = await SupportTicket.objects.filter(
            customer=user,
            brand=self.brand,
            status__in=["open", "in_progress", "pending_customer"],
        ).acount()

        text = f"""
🛟 مرکز پشتیبانی

👨‍💼 ما اینجا هستیم تا کمکتان کنیم!

📊 وضعیت شما:
• تیکت‌های باز: {open_tickets}
• ساعت کاری: ۲۴/۷
• میانگین جواب: کمتر از ۱ ساعت

چگونه می‌تونیم کمک کنیم؟
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🎫 ایجاد تیکت جدید", "callback_data": "create_ticket"}],
                [{"text": "📋 تیکت‌های من", "callback_data": "my_tickets"}],
                [{"text": "❓ سوالات متداول", "callback_data": "faq"}],
                [{"text": "📞 اطلاعات تماس", "callback_data": "contact_info"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit support menu: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_create_ticket(self, callback: types.CallbackQuery):
        """Show ticket category selection"""
        try:
            categories = []
            async for category in SupportCategory.objects.filter(
                brand=self.brand, is_active=True
            ):
                categories.append(category)
        except Exception as e:
            logger.error(f"Error fetching categories: {e}")
            await callback.answer("❌ خطا در بارگذاری دسته‌بندی‌ها")
            return

        if not categories:
            text = """
❌ در حال حاضر دسته‌بندی‌های پشتیبانی در دسترس نیست.
لطفاً بعداً دوباره امتحان کنید یا از طریق ایمیل تماس بگیرید.
            """
            keyboard = self.get_back_keyboard("support")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )
            await callback.answer()
            return

        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user, BotState.StateType.SUPPORT_TICKET, {"step": "category"}
        )

        text = """
🎫 ایجاد تیکت جدید

لطفاً دسته‌بندی مشکل خود را انتخاب کنید:
        """

        button_rows = []
        for category in categories:
            button_rows.append(
                [
                    {
                        "text": f"• {category.name}",
                        "callback_data": f"ticket_cat_{category.id}",
                    }
                ]
            )

        button_rows.append([{"text": "🔙 بازگشت", "callback_data": "support"}])
        keyboard = self.create_keyboard(button_rows)

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

    async def handle_ticket_category(
        self, callback: types.CallbackQuery, category_id: int
    ):
        """Handle category selection for ticket"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            category = await SupportCategory.objects.aget(
                id=category_id, brand=self.brand
            )
        except SupportCategory.DoesNotExist:
            await callback.answer("❌ دسته‌بندی یافت نشد")
            return

        await self.update_user_state(
            user,
            BotState.StateType.SUPPORT_TICKET,
            {"step": "subject", "category_id": category_id},
        )

        text = f"""
🎫 ایجاد تیکت جدید

دسته‌بندی: {category.name}

لطفاً موضوع مشکل خود را به‌طور خلاصه بنویسید (۱۰ تا ۱۰۰ کاراکتر):
        """

        keyboard = self.get_back_keyboard("create_ticket")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_ticket_subject(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle ticket subject entry"""
        subject = message.text.strip()

        if len(subject) < 10:
            await message.reply("❌ موضوع باید حداقل ۱۰ کاراکتر باشد.")
            return

        if len(subject) > 100:
            await message.reply("❌ موضوع نباید بیش از ۱۰۰ کاراکتر باشد.")
            return

        await self.update_user_state(
            user,
            BotState.StateType.SUPPORT_TICKET,
            {**state.state_data, "step": "description", "subject": subject},
        )

        text = f"""
🎫 ایجاد تیکت جدید

موضوع: {subject}

لطفاً توضیح دقیق مشکل خود را بنویسید:
        """

        keyboard = self.get_back_keyboard("create_ticket")
        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    async def handle_ticket_description(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle ticket description entry and create ticket"""
        description = message.text.strip()

        if len(description) < 20:
            await message.reply("❌ توضیح باید حداقل ۲۰ کاراکتر باشد.")
            return

        if len(description) > 1000:
            await message.reply("❌ توضیح نباید بیش از ۱۰۰۰ کاراکتر باشد.")
            return

        try:
            category = await SupportCategory.objects.aget(
                id=state.state_data.get("category_id"), brand=self.brand
            )

            subject = state.state_data.get("subject", "")

            ticket = await SupportTicket.objects.acreate(
                brand=self.brand,
                customer=user,
                category=category,
                subject=subject,
                description=description,
                status=SupportTicket.TicketStatus.OPEN,
                priority=category.default_priority,
                source=SupportTicket.TicketSource.TELEGRAM,
            )

            await self.update_user_state(user, BotState.StateType.MAIN_MENU)

            text = f"""
✅ تیکت شما با موفقیت ثبت شد!

🎫 شماره تیکت: <code>{ticket.ticket_number}</code>
📌 موضوع: {ticket.subject}
⏰ زمان ایجاد: {ticket.created_at.strftime("%Y/%m/%d %H:%M")}

👍 تیم پشتیبانی ما بزودی به شما پاسخ خواهد داد.
            """

            keyboard = self.create_keyboard(
                [
                    [{"text": "📋 تیکت‌های من", "callback_data": "my_tickets"}],
                    [{"text": "🔙 منو اصلی", "callback_data": "main_menu"}],
                ]
            )

            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

            logger.info(
                f"Support ticket created: {ticket.ticket_number} by {user.telegram_id}"
            )

        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            await message.reply(
                "❌ خطایی در ایجاد تیکت رخ داد. لطفاً دوباره امتحان کنید."
            )

    async def show_my_tickets(self, callback: types.CallbackQuery):
        """Show user's support tickets"""
        user, _ = await self.get_or_create_user(callback.from_user)

        tickets = []
        async for ticket in SupportTicket.objects.filter(
            customer=user, brand=self.brand
        ).order_by("-created_at"):
            tickets.append(ticket)
            if len(tickets) >= 10:
                break

        if not tickets:
            text = """
📋 تیکت‌های من

شما هیچ تیکتی ندارید.

می‌خواهید تیکت جدیدی ایجاد کنید؟
            """
            keyboard = self.create_keyboard(
                [
                    [{"text": "🎫 ایجاد تیکت جدید", "callback_data": "create_ticket"}],
                    [{"text": "🔙 بازگشت", "callback_data": "support"}],
                ]
            )
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )
            await callback.answer()
            return

        text = """
📋 تیکت‌های شما

"""
        for ticket in tickets:
            status_icon = {
                "open": "🔵",
                "in_progress": "🟡",
                "pending_customer": "⏳",
                "resolved": "✅",
                "closed": "⚫",
                "cancelled": "❌",
            }.get(ticket.status, "❓")

            text += f"\n{status_icon} <b>{ticket.ticket_number}</b> - {ticket.subject[:30]}\n"
            text += f"   ۹ تاریخ: {ticket.created_at.strftime('%Y/%m/%d')}\n"

        button_rows = [
            [
                {
                    "text": f"📌 {tickets[0].ticket_number}",
                    "callback_data": f"ticket_details_{tickets[0].id}",
                }
            ]
        ]
        if len(tickets) > 1:
            button_rows.append(
                [
                    {
                        "text": "➡️ تیکت‌های بیشتر",
                        "callback_data": "tickets_more",
                    }
                ]
            )

        button_rows.append([{"text": "🔙 بازگشت", "callback_data": "support"}])
        keyboard = self.create_keyboard(button_rows)

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

    async def show_faq(self, callback: types.CallbackQuery):
        """Show frequently asked questions"""
        try:
            articles = await SupportKnowledgeBase.objects.filter(
                brand=self.brand, status="published", is_featured=True
            ).order_by("-view_count")[:5]
        except Exception as e:
            logger.error(f"Error fetching FAQ: {e}")
            articles = []

        text = """
❓ سوالات متداول

        """

        if articles:
            for idx, article in enumerate(articles, 1):
                text += f"\n{idx}. <b>{article.title}</b>\n"
        else:
            text += "\nدر حال حاضر مقالات معمول زیادی وجود ندارد."

        button_rows = []
        if articles:
            for article in articles:
                button_rows.append(
                    [
                        {
                            "text": f"📖 {article.title[:25]}...",
                            "callback_data": f"faq_article_{article.id}",
                        }
                    ]
                )

        button_rows.append([{"text": "🔙 بازگشت", "callback_data": "support"}])
        keyboard = self.create_keyboard(button_rows)

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

    async def show_faq_article(self, callback: types.CallbackQuery, article_id: int):
        """Show FAQ article content"""
        try:
            article = await SupportKnowledgeBase.objects.aget(
                id=article_id, brand=self.brand
            )
        except SupportKnowledgeBase.DoesNotExist:
            await callback.answer("❌ مقاله یافت نشد")
            return

        article.view_count += 1
        await article.asave()

        text = f"""
📖 {article.title}

{article.content}

👍 کمکی بود؟
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "👍 بله", "callback_data": f"faq_helpful_{article.id}"}],
                [{"text": "👎 خیر", "callback_data": f"faq_not_helpful_{article.id}"}],
                [{"text": "🔙 بازگشت", "callback_data": "faq"}],
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

    async def show_contact_info(self, callback: types.CallbackQuery):
        """Show contact information"""
        text = f"""
📞 اطلاعات تماس

سلام! اگر نیاز به کمک داشتید:

📧 <b>ایمیل:</b>
<code>{self.brand.support_email or self.brand.contact_email or "support@example.com"}</code>

💬 <b>تلگرام:</b>
@{self.brand.bot_username or self.brand.slug}

⏰ <b>ساعت کاری:</b>
۲۴ ساعت / ۷ روز هفته

🚀 <b>میانگین جواب:</b>
کمتر از ۱ ساعت

لطفاً تیکت ایجاد کنید یا مستقیماً از طریق ایمیل تماس بگیرید.
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🎫 ایجاد تیکت", "callback_data": "create_ticket"}],
                [{"text": "🔙 بازگشت", "callback_data": "support"}],
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
