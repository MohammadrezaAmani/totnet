"""
Support Handler for Multi-Tenant VPN Bot
Handles support tickets, FAQ, ratings, and customer service operations.
"""

import logging
import math
import uuid
from html import escape

from aiogram import types
from aiogram.types import InlineKeyboardMarkup
from asgiref.sync import sync_to_async
from django.db import transaction as db_transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.support.models import (
    SupportCategory,
    SupportKnowledgeBase,
    SupportMessage,
    SupportTicket,
)

from .base import BaseHandler

logger = logging.getLogger(__name__)


class SupportHandler(BaseHandler):
    """Handle support tickets, knowledge base, and customer service"""

    TICKETS_PER_PAGE = 5
    FAQ_PER_PAGE = 5

    # ──────────────────────────────────────────────────────────────
    # 1. Main Menu & Categories
    # ──────────────────────────────────────────────────────────────

    async def show_support_menu(self, callback: types.CallbackQuery):
        """Show main support menu with statistics"""
        user, _ = await self.get_or_create_user(callback.from_user)

        open_tickets_count = await SupportTicket.objects.filter(
            customer=user,
            brand=self.brand,
            status__in=["open", "in_progress", "pending_customer"]
        ).acount()
        
        resolved_tickets_count = await SupportTicket.objects.filter(
            customer=user,
            brand=self.brand,
            status__in=["resolved", "closed"]
        ).acount()

        text = f"""
🛟 <b>مرکز پشتیبانی {self.brand.name}</b>

محتواهای کاربردی(احتمال زیاد راه حل مشکل شما قبلاً در این بخش گفته شده)
در غیر این صورت تیکت ثبت کنید تا در اولین فرصت رسیدگی شود.

📊 <b>وضعیت تیکت‌های شما:</b>
• تیکت‌های باز: <b>{open_tickets_count}</b>
• تیکت‌های بسته/حل شده: <b>{resolved_tickets_count}</b>
        """

        keyboard = self.create_keyboard([
            [
                {"text": "🎫 ایجاد تیکت جدید", "callback_data": "create_ticket"},
                {"text": "📋 تیکت‌های من", "callback_data": "my_tickets"},
            ],
            [{"text": "📚 محتواهای کاربردی(احتمال زیاد راه حل مشکل شما قبلاً در این بخش گفته شده)", "callback_data": "useful_content"}],
            [
                {"text": "📞 اطلاعات تماس", "callback_data": "contact_info"},
                {"text": "🔙 بازگشت", "callback_data": "main_menu"},
            ]
        ])

        await self._safe_edit_or_send(callback, text, keyboard)
        await callback.answer()

    async def show_create_ticket(self, callback: types.CallbackQuery):
        """Show ticket category selection"""
        user, _ = await self.get_or_create_user(callback.from_user)

        categories = []
        async for cat in SupportCategory.objects.filter(
            brand=self.brand, is_active=True
        ).order_by("display_order", "name"):
            categories.append(cat)

        if not categories:
            await callback.answer("❌ در حال حاضر دسته‌بندی فعالی وجود ندارد.", show_alert=True)
            return

        await self.update_user_state(user, BotState.StateType.SUPPORT_TICKET)
        await self.update_user_state(user, BotState.StateType.SUPPORT_TICKET, {
            "step": "category", "ticket_id": str(uuid.uuid4()), "device_type": ""
        })

        text = """
🎫 <b>ایجاد تیکت جدید</b>

لطفاً دسته‌بندی مرتبط با مشکل خود را انتخاب کنید:
        """

        buttons = []
        for cat in categories:
            buttons.append([{"text": f"🔹 {cat.name}", "callback_data": f"ticket_cat_{cat.id}"}])

        buttons.extend(self._ticket_device_buttons(""))
        buttons.append([{"text": "🔙 بازگشت", "callback_data": "support"}])

        await self._safe_edit_or_send(callback, text, self.create_keyboard(buttons))
        await callback.answer()

    DEVICE_LABELS = {
        "android_samsung": "اندروید سامسونگ", "android_other": "شیائومی و سایر",
        "iphone": "آیفون", "windows": "ویندوز", "macos": "مکینتاش", "linux": "لینوکس",
    }

    def _ticket_device_buttons(self, selected):
        rows = [[{"text": "اگر تیکت شما مربوط به دستگاه خاصی هست، انتخابش کنید", "callback_data": "ticket_noop"}]]
        choices = [
            {"text": ("✅ " if key == selected else "") + label, "callback_data": f"ticket_device_{key}"}
            for key, label in self.DEVICE_LABELS.items()
        ]
        rows.extend([choices[i:i + 3] for i in range(0, len(choices), 3)])
        rows.append([{"text": "بدون انتخاب دستگاه (اختیاری)", "callback_data": "ticket_device_none"}])
        return rows

    async def select_ticket_device(self, callback, device):
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)
        if state.current_state != BotState.StateType.SUPPORT_TICKET or state.state_data.get("step") not in {"category", "subject", "description"}:
            await callback.answer("برای انتخاب دستگاه، تیکت جدید باز کنید.", show_alert=True)
            return
        if device != "none" and device not in self.DEVICE_LABELS:
            await callback.answer("دستگاه نامعتبر است.")
            return
        chosen = "" if device == "none" else device
        if state.state_data.get("device_type", "") == chosen:
            await callback.answer("انتخاب شما ثبت شده است.")
            return
        state.state_data["device_type"] = chosen
        await state.asave(update_fields=["state_data", "updated_at"])
        old = callback.message.reply_markup.inline_keyboard if callback.message.reply_markup else []
        rows = [[b.model_dump(exclude_none=True) for b in row] for row in old if not any((b.callback_data or "").startswith("ticket_device_") or b.callback_data == "ticket_noop" for b in row)]
        rows[-1:-1] = self._ticket_device_buttons(state.state_data["device_type"])
        await self.bot.edit_message_reply_markup(chat_id=callback.message.chat.id, message_id=callback.message.message_id, reply_markup=self.create_keyboard(rows))
        await callback.answer("✅ دستگاه انتخاب شد" if device != "none" else "انتخاب دستگاه حذف شد")

    # ──────────────────────────────────────────────────────────────
    # 2. Ticket Creation Flow
    # ──────────────────────────────────────────────────────────────

    async def handle_ticket_category(self, callback: types.CallbackQuery, category_id: int):
        """Handle category selection and ask for subject"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            category = await SupportCategory.objects.aget(
                id=category_id, brand=self.brand, is_active=True
            )
        except SupportCategory.DoesNotExist:
            await callback.answer("❌ دسته‌بندی نامعتبر است.", show_alert=True)
            return

        state = await self.get_user_state(user)
        if state.current_state != BotState.StateType.SUPPORT_TICKET or state.state_data.get("step") != "category":
            await callback.answer("این منو قدیمی است؛ تیکت جدید باز کنید.", show_alert=True)
            return
        await self.update_user_state(user, BotState.StateType.SUPPORT_TICKET, {
            "step": "subject", "category_id": category_id
        })

        text = f"""
🎫 <b>ایجاد تیکت جدید</b>

دسته‌بندی انتخابی: <b>{escape(category.name)}</b>

✍️ لطفاً موضوع مشکل خود را به طور خلاصه بنویسید:
<i>(بین ۱۰ تا ۱۰۰ کاراکتر)</i>
        """

        keyboard = self.create_keyboard(self._ticket_device_buttons(state.state_data.get("device_type", "")) + [[{"text": "🔙 بازگشت", "callback_data": "create_ticket"}]])
        await self._safe_edit_or_send(callback, text, keyboard)
        await callback.answer()

    async def handle_ticket_subject(self, message: types.Message, user: User, state: BotState):
        """Handle ticket subject entry"""
        subject = message.text.strip()

        if len(subject) < 10:
            await message.reply("❌ موضوع باید حداقل ۱۰ کاراکتر باشد. لطفاً دوباره وارد کنید:")
            return
        if len(subject) > 100:
            await message.reply("❌ موضوع نباید بیش از ۱۰۰ کاراکتر باشد. لطفاً خلاصه‌تر بنویسید:")
            return

        state_data = state.state_data or {}
        state_data["step"] = "description"
        state_data["subject"] = subject

        await self.update_user_state(user, BotState.StateType.SUPPORT_TICKET, state_data)

        text = f"""
🎫 <b>ایجاد تیکت جدید</b>

موضوع: <b>{escape(subject)}</b>

📝 لطفاً توضیحات دقیق مشکل خود را بنویسید.
<i>(حداقل ۲۰ کاراکتر. می‌توانید لاگ‌ها یا جزئیات را اضافه کنید.)</i>
        """
        keyboard = self.create_keyboard(self._ticket_device_buttons(state_data.get("device_type", "")) + [[{"text": "🔙 بازگشت", "callback_data": "create_ticket"}]])
        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    async def handle_ticket_description(self, message: types.Message, user: User, state: BotState):
        """Handle ticket description entry and create ticket"""
        description = message.text.strip()

        if len(description) < 20:
            await message.reply("❌ توضیحات باید حداقل ۲۰ کاراکتر باشد. لطفاً کامل‌تر بنویسید:")
            return

        state_data = state.state_data or {}
        category_id = state_data.get("category_id")
        subject = state_data.get("subject")

        try:
            category = await SupportCategory.objects.aget(id=category_id, brand=self.brand)
            
            # Create ticket atomically
            ticket = await self._create_ticket(user, category, subject, description,
                device_type=state_data.get("device_type", ""), ticket_id=state_data.get("ticket_id"))
            
        except Exception as exc:
            logger.error("Ticket creation failed (%s)", type(exc).__name__)
            await message.reply("❌ خطایی در ایجاد تیکت رخ داد. لطفاً دوباره تلاش کنید.")
            return
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        text = (
            "✅ <b>تیکت شما با موفقیت ثبت شد!</b>\n\n"
            f"🎫 شماره تیکت: <code>{ticket.ticket_number}</code>\n"
            f"📌 موضوع: {escape(ticket.subject)}\n\n"
            "تیکت در «تیکت‌های من» قابل مشاهده و پیگیری است."
        )
        keyboard = self.create_keyboard([
            [{"text": "👁 مشاهده تیکت", "callback_data": f"ticket_details_{ticket.pk}"}],
            [{"text": "📋 تیکت‌های من", "callback_data": "my_tickets"}],
            [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
        ])
        try:
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)
        except Exception as exc:
            logger.warning("Ticket %s saved but confirmation delivery failed (%s)", ticket.pk, type(exc).__name__)
        try:
            await self._notify_admins_new_ticket(user, ticket)
        except Exception as exc:
            logger.warning("Ticket %s saved but admin delivery failed (%s)", ticket.pk, type(exc).__name__)

    @sync_to_async
    def _create_ticket(self, user, category, subject, description, *, device_type="", ticket_id=None):
        with db_transaction.atomic():
            ticket, _ = SupportTicket.objects.get_or_create(
                ticket_id=uuid.UUID(ticket_id) if ticket_id else uuid.uuid4(),
                defaults={
                    "brand": self.brand, "customer": user, "category": category,
                    "subject": subject, "description": description,
                    "device_type": device_type if device_type in self.DEVICE_LABELS else "",
                    "status": SupportTicket.TicketStatus.OPEN,
                    "priority": category.default_priority, "source": SupportTicket.TicketSource.TELEGRAM,
                },
            )
            return ticket

    # ──────────────────────────────────────────────────────────────
    # 3. Ticket Listing & Details
    # ──────────────────────────────────────────────────────────────

    async def show_my_tickets(self, callback: types.CallbackQuery, page: int = 1):
        """Show user's support tickets with pagination"""
        user, _ = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        total_count = await SupportTicket.objects.filter(
            customer=user, brand=self.brand
        ).acount()
        total_pages = max(1, math.ceil(total_count / self.TICKETS_PER_PAGE))
        page = max(1, min(page, total_pages))

        offset = (page - 1) * self.TICKETS_PER_PAGE
        tickets = []
        async for t in SupportTicket.objects.filter(
            customer=user, brand=self.brand
        ).order_by("-created_at")[offset:offset + self.TICKETS_PER_PAGE]:
            tickets.append(t)

        text = f"""
📋 <b>تیکت‌های من</b>

📊 تعداد کل: {total_count}
        """

        if not tickets:
            text += "\n\nشما هیچ تیکتی ندارید. برای ایجاد تیکت جدید از دکمه زیر استفاده کنید."
            keyboard = self.create_keyboard([
                [{"text": "🎫 ایجاد تیکت جدید", "callback_data": "create_ticket"}],
                [{"text": "🔙 بازگشت", "callback_data": "support"}]
            ])
        else:
            text += "\n\n<i>برای مشاهده جزئیات روی تیکت مورد نظر کلیک کنید:</i>\n"
            buttons = []
            for t in tickets:
                status_emoji = self._get_status_emoji(t.status)
                buttons.append([{
                    "text": f"{status_emoji} {t.ticket_number} | {t.subject[:30]}",
                    "callback_data": f"ticket_details_{t.id}"
                }])

            # Pagination buttons
            nav_row = []
            if page > 1:
                nav_row.append({"text": "⬅️ قبلی", "callback_data": f"tickets_page_{page-1}"})
            nav_row.append({"text": f"📄 {page}/{total_pages}", "callback_data": "ticket_noop"})
            if page < total_pages:
                nav_row.append({"text": "بعدی ➡️", "callback_data": f"tickets_page_{page+1}"})
            if len(nav_row) > 1:
                buttons.append(nav_row)

            buttons.append([{"text": "🎫 ایجاد تیکت جدید", "callback_data": "create_ticket"}])
            buttons.append([{"text": "🔙 بازگشت", "callback_data": "support"}])
            keyboard = self.create_keyboard(buttons)

        await self._safe_edit_or_send(callback, text, keyboard)
        await callback.answer()

    async def show_ticket_details(self, callback: types.CallbackQuery, ticket_id: int):
        """Show detailed view of a specific ticket"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            ticket = await SupportTicket.objects.select_related("category").aget(
                id=ticket_id, customer=user, brand=self.brand
            )
        except SupportTicket.DoesNotExist:
            await callback.answer("❌ تیکت یافت نشد.", show_alert=True)
            return

        # Fetch recent messages
        messages = []
        async for msg in SupportMessage.objects.filter(
            ticket=ticket, is_public=True
        ).order_by("-created_at")[:5]:
            messages.append(msg)

        status_emoji = self._get_status_emoji(ticket.status)
        priority_emoji = self._get_priority_emoji(ticket.priority)

        sla_text = ""
        if ticket.status in ["open", "in_progress", "pending_customer"]:
            sla_text = "\n🔴 <b>SLA اوردرو شده!</b>" if ticket.is_overdue else "\n🟢 <b>SLA در زمان مجاز</b>"

        text = f"""
🎫 <b>جزئیات تیکت</b>

🆔 <b>شماره تیکت:</b> <code>{ticket.ticket_number}</code>
📌 <b>موضوع:</b> {escape(ticket.subject)}
{status_emoji} <b>وضعیت:</b> {ticket.get_status_display()}
{priority_emoji} <b>اولویت:</b> {ticket.get_priority_display()}
🏷 <b>دسته‌بندی:</b> {escape(ticket.category.name)}
📅 <b>ایجاد شده:</b> {ticket.created_at.strftime("%Y/%m/%d %H:%M")}
{sla_text}

━━━━━━━━━━━━━━━━━━━━

📝 <b>توضیحات اولیه:</b>
{self._preview(ticket.description)}
        """

        if ticket.device_type:
            text += f"\n📱 دستگاه: {self.DEVICE_LABELS.get(ticket.device_type, ticket.device_type)}"

        if messages:
            text += "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            text += "💬 <b>آخرین پیام‌ها:</b>\n"
            for msg in reversed(messages):  # Show in chronological order
                sender_role = "👤 شما" if msg.message_type == SupportMessage.MessageType.CUSTOMER else "🛠 پشتیبانی"
                date_str = msg.created_at.strftime("%m/%d %H:%M")
                text += f"\n<b>{sender_role}</b> ({date_str}):\n{self._preview(msg.content, 300)}\n"

        buttons = []
        if ticket.status in ["open", "in_progress", "pending_customer"]:
            buttons.append([{"text": "💬 پاسخ به تیکت", "callback_data": f"ticket_reply_{ticket.id}"}])
            buttons.append([{"text": "❌ بستن تیکت", "callback_data": f"ticket_close_{ticket.id}"}])
        elif ticket.status in ["resolved", "closed"] and not ticket.customer_rating:
            buttons.append([{"text": "⭐ امتیازدهی به پشتیبانی", "callback_data": f"ticket_rate_{ticket.id}"}])

        buttons.append([{"text": "🔙 بازگشت به لیست", "callback_data": "my_tickets"}])

        await self._safe_edit_or_send(callback, text, self.create_keyboard(buttons))
        await callback.answer()

    # ──────────────────────────────────────────────────────────────
    # 4. Replying & Closing Tickets
    # ──────────────────────────────────────────────────────────────

    async def start_ticket_reply(self, callback: types.CallbackQuery, ticket_id: int):
        """Prompt user to enter a reply for the ticket"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            ticket = await SupportTicket.objects.aget(
                id=ticket_id, customer=user, brand=self.brand
            )
            if ticket.status in ["closed", "cancelled"]:
                await callback.answer("❌ این تیکت بسته شده است.", show_alert=True)
                return
        except SupportTicket.DoesNotExist:
            await callback.answer("❌ تیکت یافت نشد.", show_alert=True)
            return

        await self.update_user_state(user, BotState.StateType.SUPPORT_TICKET, {
            "step": "reply",
            "ticket_id": ticket_id
        })

        text = f"""
💬 <b>پاسخ به تیکت {ticket.ticket_number}</b>

لطفاً پیام خود را بنویسید:
        """
        keyboard = self.create_keyboard([
            [{"text": "❌ انصراف", "callback_data": f"ticket_details_{ticket_id}"}]
        ])
        await self._safe_edit_or_send(callback, text, keyboard)
        await callback.answer()

    async def handle_ticket_reply(self, message: types.Message, user: User, state: BotState):
        """Save the reply message and notify admins"""
        state_data = state.state_data or {}
        ticket_id = state_data.get("ticket_id")

        if not ticket_id:
            await message.reply("❌ خطا در شناسایی تیکت.")
            return

        content = message.text.strip()
        if len(content) < 2:
            await message.reply("❌ پیام نمی‌تواند خالی باشد.")
            return

        try:
            ticket = await SupportTicket.objects.aget(id=ticket_id, customer=user, brand=self.brand)

            # Save message
            await self._save_ticket_message(ticket, user, content)

            # Update ticket status if it was pending_customer
            if ticket.status == "pending_customer":
                ticket.status = "in_progress"
                await ticket.asave()

            await self.update_user_state(user, BotState.StateType.MAIN_MENU, {})

            text = f"""
✅ <b>پیام شما ارسال شد!</b>

🎫 تیکت <code>{ticket.ticket_number}</code>
تیم پشتیبانی در اسرع وقت پاسخ خواهد داد.
            """
            keyboard = self.create_keyboard([
                [{"text": "👁 مشاهده تیکت", "callback_data": f"ticket_details_{ticket.id}"}],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}]
            ])
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

            # Notify admins
            await self._notify_admins_new_reply(user, ticket, content)

        except Exception as e:
            logger.error(f"Error replying to ticket: {e}")
            await message.reply("❌ خطا در ارسال پیام. لطفاً دوباره تلاش کنید.")

    @sync_to_async
    def _save_ticket_message(self, ticket: SupportTicket, user: User, content: str) -> SupportMessage:
        with db_transaction.atomic():
            return SupportMessage.objects.create(
                ticket=ticket,
                message_type=SupportMessage.MessageType.CUSTOMER,
                sender=user,
                content=content,
                is_public=True,
                is_read_by_customer=True
            )

    async def close_ticket(self, callback: types.CallbackQuery, ticket_id: int):
        """Allow user to manually close their ticket"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            ticket = await SupportTicket.objects.aget(id=ticket_id, customer=user, brand=self.brand)
            if ticket.status in ["closed", "cancelled"]:
                await callback.answer("این تیکت از قبل بسته شده است.", show_alert=True)
                return

            ticket.status = SupportTicket.TicketStatus.CLOSED
            ticket.closed_at = timezone.now()
            await ticket.asave()

            # Add system message
            await self._save_system_message(ticket, "تیکت توسط کاربر بسته شد.")

            text = """
✅ <b>تیکت با موفقیت بسته شد.</b>

از بازخورد شما متشکریم!
            """
            keyboard = self.create_keyboard([
                [{"text": "⭐ امتیازدهی به پشتیبانی", "callback_data": f"ticket_rate_{ticket.id}"}],
                [{"text": "🔙 بازگشت", "callback_data": "my_tickets"}]
            ])
            await self._safe_edit_or_send(callback, text, keyboard)
            await callback.answer()

        except Exception as e:
            logger.error(f"Error closing ticket: {e}")
            await callback.answer("❌ خطا در بستن تیکت.", show_alert=True)

    @sync_to_async
    def _save_system_message(self, ticket: SupportTicket, content: str):
        with db_transaction.atomic():
            return SupportMessage.objects.create(
                ticket=ticket,
                message_type=SupportMessage.MessageType.SYSTEM,
                sender=ticket.customer,  # System acts on behalf of customer
                content=content,
                is_public=True
            )

    # ──────────────────────────────────────────────────────────────
    # 5. Ticket Rating System
    # ──────────────────────────────────────────────────────────────

    async def show_rating_options(self, callback: types.CallbackQuery, ticket_id: int):
        """Show 1-5 star rating buttons"""
        try:
            ticket = await SupportTicket.objects.aget(id=ticket_id, brand=self.brand)
            if ticket.customer_rating:
                await callback.answer("شما قبلاً به این تیکت امتیاز داده‌اید.", show_alert=True)
                return
        except SupportTicket.DoesNotExist:
            await callback.answer("❌ تیکت یافت نشد.", show_alert=True)
            return

        text = f"""
⭐ <b>امتیازدهی به پشتیبانی</b>

تیکت: <code>{ticket.ticket_number}</code>

لطفاً کیفیت پاسخگویی پشتیبانی را امتیاز دهید:
(۱ = ضعیف، ۵ = عالی)
        """
        buttons = [
            [
                {"text": "1️⃣", "callback_data": f"ticket_rate_submit_{ticket_id}_1"},
                {"text": "2️⃣", "callback_data": f"ticket_rate_submit_{ticket_id}_2"},
                {"text": "3️⃣", "callback_data": f"ticket_rate_submit_{ticket_id}_3"},
                {"text": "4️⃣", "callback_data": f"ticket_rate_submit_{ticket_id}_4"},
                {"text": "5️⃣", "callback_data": f"ticket_rate_submit_{ticket_id}_5"},
            ],
            [{"text": "🔙 بازگشت", "callback_data": f"ticket_details_{ticket_id}"}]
        ]
        await self._safe_edit_or_send(callback, text, self.create_keyboard(buttons))
        await callback.answer()

    async def submit_rating(self, callback: types.CallbackQuery, ticket_id: int, rating: int):
        """Save the rating to the ticket"""
        try:
            ticket = await SupportTicket.objects.aget(id=ticket_id, brand=self.brand)
            if ticket.customer_rating:
                await callback.answer("شما قبلاً امتیاز داده‌اید.", show_alert=True)
                return

            ticket.customer_rating = rating
            ticket.feedback_submitted_at = timezone.now()
            await ticket.asave()

            text = f"""
✅ <b>از امتیاز شما متشکریم!</b>

امتیاز ثبت شده: {'⭐' * rating}
            """
            keyboard = self.create_keyboard([
                [{"text": "🔙 بازگشت", "callback_data": f"ticket_details_{ticket_id}"}]
            ])
            await self._safe_edit_or_send(callback, text, keyboard)
            await callback.answer()

        except Exception as e:
            logger.error(f"Error submitting rating: {e}")
            await callback.answer("❌ خطا در ثبت امتیاز.", show_alert=True)

    # ──────────────────────────────────────────────────────────────
    # 6. Knowledge Base & FAQ
    # ──────────────────────────────────────────────────────────────

    async def show_faq(self, callback: types.CallbackQuery, page: int = 1):
        """List published KB articles with pagination"""
        total_count = await SupportKnowledgeBase.objects.filter(
            brand=self.brand, status="published"
        ).acount()
        total_pages = max(1, math.ceil(total_count / self.FAQ_PER_PAGE))
        page = max(1, min(page, total_pages))

        offset = (page - 1) * self.FAQ_PER_PAGE
        articles = []
        async for a in SupportKnowledgeBase.objects.filter(
            brand=self.brand, status="published"
        ).order_by("-is_featured", "-view_count")[offset:offset + self.FAQ_PER_PAGE]:
            articles.append(a)

        text = f"""
❓ <b>سوالات متداول (FAQ)</b>

📚 مقالات آموزشی و پاسخ سوالات رایج.

<i>صفحه {page} از {total_pages}</i>
        """

        buttons = []
        for a in articles:
            icon = "⭐" if a.is_featured else "📖"
            buttons.append([{"text": f"{icon} {a.title}", "callback_data": f"faq_article_{a.id}"}])

        nav_row = []
        if page > 1:
            nav_row.append({"text": "⬅️ قبلی", "callback_data": f"faq_page_{page-1}"})
        if page < total_pages:
            nav_row.append({"text": "بعدی ➡️", "callback_data": f"faq_page_{page+1}"})
        if len(nav_row) > 0:
            buttons.append(nav_row)

        buttons.append([{"text": "🔍 جستجوی مقاله", "callback_data": "faq_search"}])
        buttons.append([{"text": "🔙 بازگشت", "callback_data": "support"}])

        await self._safe_edit_or_send(callback, text, self.create_keyboard(buttons))
        await callback.answer()

    async def show_faq_article(self, callback: types.CallbackQuery, article_id: int):
        """Show a specific KB article"""
        try:
            article = await SupportKnowledgeBase.objects.aget(
                id=article_id, brand=self.brand, status="published"
            )
        except SupportKnowledgeBase.DoesNotExist:
            await callback.answer("❌ مقاله یافت نشد.", show_alert=True)
            return

        # Increment view count safely
        await self._increment_article_views(article)

        text = f"""
📖 <b>{article.title}</b>

━━━━━━━━━━━━━━━━━━━━

{article.content}

━━━━━━━━━━━━━━━━━━━━
👁 بازدید: {article.view_count + 1}
👍 مفید: {article.helpful_votes} | 👎 نامفید: {article.not_helpful_votes}
        """

        buttons = [
            [
                {"text": "👍 مفید بود", "callback_data": f"faq_helpful_{article.id}"},
                {"text": "👎 مفید نبود", "callback_data": f"faq_not_helpful_{article.id}"}
            ],
            [{"text": "🔙 بازگشت به لیست", "callback_data": "faq"}]
        ]

        await self._safe_edit_or_send(callback, text, self.create_keyboard(buttons))
        await callback.answer()

    @sync_to_async
    def _increment_article_views(self, article: SupportKnowledgeBase):
        SupportKnowledgeBase.objects.filter(pk=article.pk).update(view_count=F("view_count") + 1)

    async def vote_faq(self, callback: types.CallbackQuery, article_id: int, is_helpful: bool):
        """Record user vote on FAQ article helpfulness"""
        try:
            article = await SupportKnowledgeBase.objects.aget(id=article_id, brand=self.brand)
            if is_helpful:
                await self._increment_article_votes(article, 'helpful')
                await callback.answer("✅ از بازخورد شما متشکریم!", show_alert=False)
            else:
                await self._increment_article_votes(article, 'not_helpful')
                await callback.answer("✅ بازخورد شما ثبت شد. برای بهبود تلاش می‌کنیم.", show_alert=False)
        except Exception as e:
            logger.error(f"Error voting FAQ: {e}")
            await callback.answer("❌ خطا در ثبت نظر.", show_alert=True)

    @sync_to_async
    def _increment_article_votes(self, article: SupportKnowledgeBase, vote_type: str):
        if vote_type == 'helpful':
            SupportKnowledgeBase.objects.filter(pk=article.pk).update(helpful_votes=F("helpful_votes") + 1)
        else:
            SupportKnowledgeBase.objects.filter(pk=article.pk).update(not_helpful_votes=F("not_helpful_votes") + 1)

    async def start_faq_search(self, callback: types.CallbackQuery):
        """Prompt user to enter search query"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(user, BotState.StateType.SUPPORT_TICKET, {"step": "search_faq"})

        text = """
🔍 <b>جستجو در مقالات</b>

لطفاً کلمه یا عبارت مورد نظر خود را برای جستجو وارد کنید:
        """
        keyboard = self.get_back_keyboard("faq")
        await self._safe_edit_or_send(callback, text, keyboard)
        await callback.answer()

    async def handle_faq_search(self, message: types.Message, user: User, state: BotState):
        """Process FAQ search query"""
        query = message.text.strip()
        if len(query) < 3:
            await message.reply("❌ عبارت جستجو باید حداقل ۳ کاراکتر باشد.")
            return

        await self.update_user_state(user, BotState.StateType.MAIN_MENU, {})

        articles = []
        async for a in SupportKnowledgeBase.objects.filter(
            brand=self.brand,
            status="published",
            title__icontains=query
        ).order_by("-view_count")[:10]:
            articles.append(a)

        if not articles:
            text = f"""
🔍 <b>نتیجه جستجو برای: {query}</b>

❌ هیچ مقاله‌ای یافت نشد.
            """
            keyboard = self.create_keyboard([
                [{"text": "🔍 جستجوی مجدد", "callback_data": "faq_search"}],
                [{"text": "🔙 بازگشت", "callback_data": "faq"}]
            ])
        else:
            text = f"""
🔍 <b>نتیجه جستجو برای: {query}</b>

{len(articles)} مقاله یافت شد:
            """
            buttons = []
            for a in articles:
                buttons.append([{"text": f"📖 {a.title}", "callback_data": f"faq_article_{a.id}"}])
            buttons.append([{"text": "🔍 جستجوی مجدد", "callback_data": "faq_search"}])
            buttons.append([{"text": "🔙 بازگشت", "callback_data": "faq"}])
            keyboard = self.create_keyboard(buttons)

        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    # ──────────────────────────────────────────────────────────────
    # 7. Contact Info & Unified Handlers
    # ──────────────────────────────────────────────────────────────

    async def show_contact_info(self, callback: types.CallbackQuery):
        """Show contact information"""
        support_email = self.brand.support_email or "support@example.com"
        bot_username = self.brand.bot_username or self.brand.slug

        text = f"""
📞 <b>اطلاعات تماس</b>

📧 <b>ایمیل پشتیبانی:</b>
<code>{support_email}</code>

💬 <b>شناسه تلگرام:</b>
@{bot_username}

ℹ️ <i>برای پیگیری درخواست، لطفاً از طریق سیستم تیکت‌سازی اقدام کنید.</i>
        """
        keyboard = self.create_keyboard([
            [{"text": "🎫 ایجاد تیکت", "callback_data": "create_ticket"}],
            [{"text": "🔙 بازگشت", "callback_data": "support"}]
        ])
        await self._safe_edit_or_send(callback, text, keyboard)
        await callback.answer()

    async def handle_text_message(self, message: types.Message, user: User, state: BotState):
        """Unified text router for support states"""
        step = state.state_data.get("step") if state.state_data else None

        if step == "subject":
            await self.handle_ticket_subject(message, user, state)
        elif step == "description":
            await self.handle_ticket_description(message, user, state)
        elif step == "reply":
            await self.handle_ticket_reply(message, user, state)
        elif step == "search_faq":
            await self.handle_faq_search(message, user, state)
        else:
            await message.reply(
                "لطفاً از منوی زیر استفاده کنید:",
                reply_markup=await self.get_main_menu_keyboard(user)
            )

    # ──────────────────────────────────────────────────────────────
    # 8. Helper & Notification Methods
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _preview(value, limit=1200):
        parts, count = [], 0
        for char in str(value or ""):
            fragment = escape(char)
            if count + len(fragment) > limit:
                return "".join(parts) + "…"
            parts.append(fragment)
            count += len(fragment)
        return "".join(parts)

    def _get_status_emoji(self, status: str) -> str:
        return {
            "open": "🔵",
            "in_progress": "🟡",
            "pending_customer": "⏳",
            "pending_internal": "⏳",
            "resolved": "✅",
            "closed": "⚫",
            "cancelled": "❌"
        }.get(status, "❓")

    def _get_priority_emoji(self, priority: str) -> str:
        return {
            "low": "🟢",
            "normal": "⚪",
            "high": "🟠",
            "urgent": "🔴"
        }.get(priority, "⚪")

    async def _safe_edit_or_send(self, source, text: str, keyboard: InlineKeyboardMarkup):
        """Try to edit the message, fall back to sending a new one."""
        try:
            if hasattr(source, "message"):
                await self.edit_message_with_keyboard(
                    source.message.chat.id,
                    source.message.message_id,
                    text,
                    keyboard
                )
            else:
                await self.send_message_with_keyboard(source, text, keyboard)
        except Exception as e:
            logger.warning(f"Safe edit/send fallback: {e}")
            chat_id = source.message.chat.id if hasattr(source, "message") else source
            await self.send_message_with_keyboard(chat_id, text, keyboard)

    async def _notify_admins_new_ticket(self, user: User, ticket: SupportTicket):
        """Notify brand admins about a new ticket"""
        from apps.accounts.models import User as UserModel

        admin_text = f"""
🔔 <b>تیکت پشتیبانی جدید</b>

🎫 شماره: <code>{ticket.ticket_number}</code>
👤 کاربر: {user.full_name or user.username} (<code>{user.telegram_id}</code>)
📌 موضوع: {escape(ticket.subject)}
🏷 دسته: {escape(ticket.category.name)}
{self._get_priority_emoji(ticket.priority)} اولویت: {ticket.get_priority_display()}

📝 توضیحات:
{self._preview(ticket.description)}
        """

        async for admin in UserModel.objects.filter(is_staff=True, brand=self.brand):
            if admin.telegram_id:
                try:
                    await self.bot.send_message(
                        chat_id=admin.telegram_id,
                        text=admin_text,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Could not notify admin {admin.id} about new ticket: {e}")

    async def _notify_admins_new_reply(self, user: User, ticket: SupportTicket, content: str):
        """Notify assigned agent or all staff about a new reply"""
        from apps.accounts.models import User as UserModel

        admin_text = f"""
💬 <b>پاسخ جدید به تیکت</b>

🎫 شماره: <code>{ticket.ticket_number}</code>
👤 کاربر: {user.full_name or user.username}

📝 پیام:
{content}
        """

        admins_qs = UserModel.objects.filter(is_staff=True, brand=self.brand)
        if ticket.assigned_to:
            admins_qs = admins_qs.filter(id=ticket.assigned_to.id)

        async for admin in admins_qs:
            if admin.telegram_id:
                try:
                    await self.bot.send_message(
                        chat_id=admin.telegram_id,
                        text=admin_text,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Could not notify admin {admin.id} about reply: {e}")
