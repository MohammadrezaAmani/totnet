"""
Wallet Handler for Multi-Tenant VPN Bot
Handles wallet operations, balance, transactions, and charging
Supports multiple payment methods: Card Transfer, Online Gateway, Crypto, Telegram Stars
"""

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from aiogram import types
from aiogram.types import LabeledPrice, PreCheckoutQuery
from asgiref.sync import sync_to_async
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from apps.bot.models import BotState
from apps.orders.models import (
    Coupon,
    CouponUsage,
    CryptoCurrency,
    Order,
    Payment,
    PaymentCard,
    PaymentGateway,
    Wallet,
    WalletTransaction,
)

from .base import BaseHandler

logger = logging.getLogger(__name__)


class WalletHandler(BaseHandler):
    """Handle wallet and financial operations"""

    # Preset charge amounts per currency
    PRESET_AMOUNTS = {
        "USD": [5, 10, 20, 50, 100, 500],
        "EUR": [5, 10, 20, 50, 100, 500],
        "GBP": [5, 10, 20, 50, 100, 500],
        "IRR": [50_000, 100_000, 200_000, 500_000, 1_000_000, 5_000_000],
        "IRT": [50_000, 100_000, 200_000, 500_000, 1_000_000, 5_000_000],
    }

    # Minimum and maximum charge amounts
    LIMITS = {
        "USD": {"min": 1, "max": 10_000},
        "EUR": {"min": 1, "max": 10_000},
        "GBP": {"min": 1, "max": 10_000},
        "IRR": {"min": 10_000, "max": 100_000_000},
        "IRT": {"min": 10_000, "max": 100_000_000},
    }

    # ==================== Main Methods ====================

    async def get_or_create_wallet(self, user) -> Wallet:
        """Get or create user wallet"""
        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
        except Wallet.DoesNotExist:
            wallet = await Wallet.objects.acreate(
                user=user,
                brand=self.brand,
                balance=Decimal("0.00"),
                currency=self.brand.currency,
                is_active=True,
                is_frozen=False,
            )
        return wallet

    async def show_wallet(self, callback: types.CallbackQuery):
        """Show wallet overview with beautiful UI"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)

        # Get recent transactions
        recent_transactions = []
        async for trans in WalletTransaction.objects.filter(wallet=wallet).order_by(
            "-created_at"
        )[:5]:
            recent_transactions.append(trans)

        # Build status display
        if wallet.is_frozen:
            status_icon = "🟡"
            status_text = "مسدود شده"
        elif wallet.is_active:
            status_icon = "🟢"
            status_text = "فعال"
        else:
            status_icon = "🔴"
            status_text = "غیرفعال"

        # Calculate statistics
        total_deposits = await self._calculate_total_by_type(
            wallet, WalletTransaction.TransactionType.DEPOSIT
        )
        total_spent = await self._calculate_total_by_type(
            wallet, WalletTransaction.TransactionType.PAYMENT
        )
        total_bonuses = await self._calculate_total_by_type(
            wallet, WalletTransaction.TransactionType.BONUS
        )

        symbol = self._get_currency_symbol(wallet.currency)

        text = f"""
<b>💰 کیف پول من</b>
━━━━━━━━━━━━━━━━━━━━━━

💵 <b>موجودی فعلی:</b>
<code>{wallet.balance:,.2f}</code> {symbol}

📊 <b>آمار حساب:</b>
├ 📥 مجموع واریزها: <code>{total_deposits:,.2f}</code> {symbol}
├ 📤 مجموع خریدها: <code>{total_spent:,.2f}</code> {symbol}
├ 🎁 مجموع جوایز: <code>{total_bonuses:,.2f}</code> {symbol}
└ {status_icon} وضعیت: {status_text}

📅 تاریخ عضویت: <code>{self._format_persian_date(wallet.created_at)}</code>
"""

        # Add recent transactions
        if recent_transactions:
            text += "\n📝 <b>آخرین تراکنش‌ها:</b>\n"
            text += "┌──────────────────────────────\n"
            for i, trans in enumerate(recent_transactions, 1):
                icon = self._get_transaction_icon(trans.transaction_type)
                is_credit = self._is_credit_type(trans.transaction_type)
                sign = "+" if is_credit else "-"
                amount_str = f"{sign}{trans.amount:,.2f}"
                date_str = trans.created_at.strftime("%m/%d %H:%M")
                description = self._truncate_text(trans.description or "تراکنش", 18)

                text += f"│ {icon} <code>{amount_str:>11}</code> {symbol}\n"
                text += f"│    📝 {description}\n"
                text += f"│    📅 {date_str}\n"
                if i < len(recent_transactions):
                    text += "├──────────────────────────────\n"
            text += "└──────────────────────────────\n"

        keyboard = self.create_keyboard(
            [
                [
                    {"text": "🔄 شارژ کیف پول", "callback_data": "charge_wallet"},
                    {"text": "📋 تاریخچه", "callback_data": "wallet_history_1"},
                ],
                [{"text": "🎁 استفاده از کد تخفیف", "callback_data": "wallet_coupon"}],
                [{"text": "🔙 بازگشت به منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def show_wallet_history(self, callback: types.CallbackQuery, page: int = 1):
        """Show complete wallet transaction history with pagination"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)

        per_page = 8
        offset = (page - 1) * per_page

        # Get transactions with pagination
        transactions = []
        async for trans in WalletTransaction.objects.filter(wallet=wallet).order_by(
            "-created_at"
        )[offset : offset + per_page]:
            transactions.append(trans)

        total_count = await WalletTransaction.objects.filter(wallet=wallet).acount()
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        symbol = self._get_currency_symbol(wallet.currency)

        if not transactions:
            text = """
📋 <b>تاریخچه تراکنش‌ها</b>
━━━━━━━━━━━━━━━━━━━━━━

📭 هیچ تراکنشی یافت نشد.

💡 اولین تراکنش خود را با شارژ کیف پول شروع کنید!
            """
            keyboard = self.create_keyboard(
                [
                    [{"text": "🔄 شارژ کیف پول", "callback_data": "charge_wallet"}],
                    [{"text": "🔙 بازگشت به کیف پول", "callback_data": "wallet"}],
                ]
            )
        else:
            # Calculate totals
            total_deposits = await self._calculate_total_by_type(
                wallet, WalletTransaction.TransactionType.DEPOSIT
            )
            total_spent = await self._calculate_total_by_type(
                wallet, WalletTransaction.TransactionType.PAYMENT
            )
            total_bonuses = await self._calculate_total_by_type(
                wallet, WalletTransaction.TransactionType.BONUS
            )

            text = f"""
📋 <b>تاریخچه تراکنش‌ها</b>
━━━━━━━━━━━━━━━━━━━━━━

📊 <b>خلاصه حساب:</b>
├ 💰 موجودی: <code>{wallet.balance:,.2f}</code> {symbol}
├ 📥 واریزها: <code>{total_deposits:,.2f}</code>
├ 📤 خریدها: <code>{total_spent:,.2f}</code>
└ 🎁 جوایز: <code>{total_bonuses:,.2f}</code>

━━━━━━━━━━━━━━━━━━━━━━
📄 صفحه <code>{page}</code> از <code>{total_pages}</code> | مجموع: <code>{total_count}</code> تراکنش
━━━━━━━━━━━━━━━━━━━━━━

"""

            for trans in transactions:
                icon = self._get_transaction_icon(trans.transaction_type)
                is_credit = self._is_credit_type(trans.transaction_type)
                sign = "+" if is_credit else "-"
                amount_str = f"{sign}{trans.amount:,.2f}"
                date_str = trans.created_at.strftime("%Y/%m/%d - %H:%M")
                description = trans.description or "تراکنش"
                type_display = trans.get_transaction_type_display()

                text += f"{icon} <b>{type_display}</b>\n"
                text += f"   💵 <code>{amount_str:>12}</code> {symbol}\n"
                text += f"   📝 {description}\n"
                text += f"   📅 {date_str}\n"
                text += "   ─────────────────────────\n"

            # Build pagination keyboard
            button_rows = []
            nav_row = []

            if page > 1:
                nav_row.append(
                    {"text": "⬅️ قبلی", "callback_data": f"wallet_history_{page - 1}"}
                )

            nav_row.append(
                {"text": f"📄 {page}/{total_pages}", "callback_data": "wallet_noop"}
            )

            if page < total_pages:
                nav_row.append(
                    {"text": "بعدی ➡️", "callback_data": f"wallet_history_{page + 1}"}
                )

            button_rows.append(nav_row)
            button_rows.append(
                [
                    {"text": "🔄 شارژ کیف پول", "callback_data": "charge_wallet"},
                    {"text": "🔙 بازگشت", "callback_data": "wallet"},
                ]
            )

            keyboard = self.create_keyboard(button_rows)

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    # ==================== Charge Methods ====================

    async def show_charge_options(self, callback: types.CallbackQuery):
        """Show wallet charge options with beautiful UI"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)

        if wallet.is_frozen:
            await callback.answer(
                "❌ کیف پول شما مسدود شده است.\nلطفاً با پشتیبانی تماس بگیرید.",
                show_alert=True,
            )
            return

        currency = wallet.currency
        amounts = self.PRESET_AMOUNTS.get(currency, self.PRESET_AMOUNTS["USD"])
        symbol = self._get_currency_symbol(currency)

        text = f"""
🔄 <b>شارژ کیف پول</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 موجودی فعلی: <code>{wallet.balance:,.2f}</code> {symbol}

💡 مبلغ شارژ مورد نظر را انتخاب کنید:
        """

        # Create beautiful amount buttons in 2 columns
        button_rows = []
        for i in range(0, len(amounts), 2):
            row = []
            for j in range(2):
                if i + j < len(amounts):
                    amount = amounts[i + j]
                    formatted = self._format_amount(amount, currency)
                    row.append(
                        {
                            "text": f"💰 {formatted} {symbol}",
                            "callback_data": f"charge_amount_{amount}",
                        }
                    )
            button_rows.append(row)

        # Add custom amount option
        button_rows.append(
            [{"text": "✏️ وارد کردن مبلغ دلخواه", "callback_data": "charge_custom"}]
        )

        # Add coupon option
        button_rows.append(
            [{"text": "🎁 استفاده از کد تخفیف", "callback_data": "wallet_coupon"}]
        )

        button_rows.append(
            [{"text": "🔙 بازگشت به کیف پول", "callback_data": "wallet"}]
        )

        keyboard = self.create_keyboard(button_rows)

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def request_custom_amount(self, callback: types.CallbackQuery):
        """Request custom charge amount from user"""
        user, _ = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"action": "wallet_charge", "step": "waiting_custom_amount"},
        )

        limits = self.LIMITS.get(self.brand.currency, self.LIMITS["USD"])
        symbol = self._get_currency_symbol(self.brand.currency)

        text = f"""
✏️ <b>مبلغ دلخواه</b>
━━━━━━━━━━━━━━━━━━━━━━

لطفاً مبلغ مورد نظر برای شارژ را وارد کنید:

📊 <b>محدودیت‌ها:</b>
├ حداقل: <code>{self._format_amount(limits["min"], self.brand.currency)}</code> {symbol}
└ حداکثر: <code>{self._format_amount(limits["max"], self.brand.currency)}</code> {symbol}

💡 مثال: 50000 یا 100000
        """

        keyboard = self.get_back_keyboard("charge_wallet")

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def handle_custom_amount_message(
        self, message: types.Message, user, state: BotState
    ):
        """Handle custom amount input"""
        try:
            amount_text = message.text.strip().replace(",", "").replace(" ", "")
            amount = Decimal(amount_text)

            limits = self.LIMITS.get(self.brand.currency, self.LIMITS["USD"])
            symbol = self._get_currency_symbol(self.brand.currency)

            if amount < limits["min"]:
                await message.reply(
                    f"❌ حداقل مبلغ شارژ <code>{self._format_amount(limits['min'], self.brand.currency)}</code> {symbol} می‌باشد."
                )
                return

            if amount > limits["max"]:
                await message.reply(
                    f"❌ حداکثر مبلغ شارژ <code>{self._format_amount(limits['max'], self.brand.currency)}</code> {symbol} می‌باشد."
                )
                return

            # Proceed to payment method selection
            await self.show_payment_methods(message.chat.id, user, float(amount))

        except ValueError, InvalidOperation:
            await message.reply(
                "❌ لطفاً یک عدد معتبر وارد کنید.\n\n💡 مثال: 50000 یا 100000"
            )

    async def initiate_charge(self, callback: types.CallbackQuery, amount: float):
        """Initiate wallet charge process - show payment methods"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)

        if wallet.is_frozen:
            await callback.answer(
                "❌ کیف پول شما مسدود شده است.\nلطفاً با پشتیبانی تماس بگیرید.",
                show_alert=True,
            )
            return

        await self.show_payment_methods(
            callback.message.chat.id, user, amount, callback
        )

    async def show_payment_methods(
        self,
        chat_id: int,
        user,
        amount: float,
        callback: Optional[types.CallbackQuery] = None,
    ):
        """Show available payment methods"""
        wallet = await self.get_or_create_wallet(user)
        symbol = self._get_currency_symbol(wallet.currency)

        # Check available payment methods
        has_cards = await PaymentCard.objects.filter(
            brand=self.brand, is_active=True
        ).aexists()

        has_gateways = await PaymentGateway.objects.filter(
            brand=self.brand, is_active=True
        ).aexists()

        has_crypto = await CryptoCurrency.objects.filter(
            brand=self.brand, is_active=True
        ).aexists()

        # Store amount in state
        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"action": "wallet_charge", "amount": amount},
        )

        text = f"""
💳 <b>انتخاب روش پرداخت</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ شارژ: <code>{amount:,.2f}</code> {symbol}
💵 ارز: {wallet.currency}

💡 لطفاً روش پرداخت را انتخاب کنید:
        """

        button_rows = []

        # Card transfer option
        if has_cards:
            button_rows.append(
                [
                    {
                        "text": "💳 کارت به کارت",
                        "callback_data": f"wallet_pay_card_{amount}",
                    }
                ]
            )

        # Online gateway option
        if has_gateways:
            button_rows.append(
                [
                    {
                        "text": "🌐 درگاه پرداخت آنلاین",
                        "callback_data": f"wallet_pay_gateway_{amount}",
                    }
                ]
            )

        # Crypto option
        if has_crypto:
            button_rows.append(
                [
                    {
                        "text": "₿ پرداخت با رمزارز",
                        "callback_data": f"wallet_pay_crypto_{amount}",
                    }
                ]
            )

        # Telegram Stars option
        button_rows.append(
            [
                {
                    "text": "⭐ ستاره‌های تلگرام",
                    "callback_data": f"wallet_pay_stars_{amount}",
                }
            ]
        )

        if not button_rows:
            text += "\n\n❌ متأسفانه در حال حاضر هیچ روش پرداختی فعالی وجود ندارد.\nلطفاً بعداً تلاش کنید یا با پشتیبانی تماس بگیرید."

        button_rows.append([{"text": "🔙 بازگشت", "callback_data": "charge_wallet"}])
        keyboard = self.create_keyboard(button_rows)

        if callback:
            await self._safe_edit_message(
                callback.message.chat.id,
                callback.message.message_id,
                text,
                keyboard,
            )
            await callback.answer()
        else:
            await self.send_message_with_keyboard(chat_id, text, keyboard)

    # ==================== Card Payment Methods ====================

    async def show_card_payment(self, callback: types.CallbackQuery, amount: float):
        """Show card transfer payment details"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        symbol = self._get_currency_symbol(wallet.currency)

        # Get active cards
        cards = []
        async for card in PaymentCard.objects.filter(
            brand=self.brand, is_active=True
        ).order_by("display_order"):
            cards.append(card)

        if not cards:
            await callback.answer("❌ کارت بانکی فعالی وجود ندارد.", show_alert=True)
            return

        # Update state
        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"action": "wallet_charge", "amount": amount, "step": "waiting_receipt"},
        )

        text = f"""
💳 <b>پرداخت کارت به کارت</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ شارژ: <code>{amount:,.2f}</code> {symbol}

📋 <b>اطلاعات کارت‌های بانکی:</b>
"""

        for i, card in enumerate(cards, 1):
            text += f"""
<b>🔸 {i}. {card.bank_name}</b>
├ 📱 شماره کارت: <code>{card.card_number}</code>
├ 👤 صاحب حساب: <code>{card.cardholder_name}</code>
"""

        text += """
━━━━━━━━━━━━━━━━━━━━━━

⚠️ <b>راهنمای پرداخت:</b>
1️⃣ مبلغ دقیق <b>{amount:,.2f}</b> را به یکی از کارت‌های بالا واریز کنید
2️⃣ از رسید پرداخت عکس بگیرید
3️⃣ روی دکمه "📸 ارسال رسید" کلیک کنید
4️⃣ عکس رسید را ارسال کنید

⏰ لطفاً ظرف <b>۳۰ دقیقه</b> رسید را ارسال کنید.
❗ بدون ارسال رسید، واریز تأیید نمی‌شود.
        """

        button_rows = [
            [{"text": "📸 ارسال رسید", "callback_data": "wallet_send_receipt"}],
            [{"text": "❌ انصراف", "callback_data": "charge_wallet"}],
        ]
        keyboard = self.create_keyboard(button_rows)

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def prompt_send_receipt(self, callback: types.CallbackQuery):
        """Prompt user to send receipt photo"""
        text = """
📸 <b>ارسال رسید</b>
━━━━━━━━━━━━━━━━━━━━━━

لطفاً عکس رسید پرداخت خود را ارسال کنید:

💡 نکات مهم:
• عکس باید واضح و خوانا باشد
• مبلغ و شماره کارت مقصد در رسید مشخص باشد
• از اسکرین‌شات رسید موبایل بانک استفاده کنید
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "❌ انصراف", "callback_data": "charge_wallet"}],
            ]
        )

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def handle_receipt_photo(self, message: types.Message, user, state: BotState):
        """Handle payment receipt photo"""
        amount = state.state_data.get("amount") if state.state_data else None

        if not amount:
            await message.reply("❌ خطا در پردازش. لطفاً دوباره تلاش کنید.")
            await self._send_charge_menu(message.chat.id, user)
            return

        wallet = await self.get_or_create_wallet(user)
        symbol = self._get_currency_symbol(wallet.currency)

        # Get the photo
        if not message.photo:
            await message.reply(
                "❌ لطفاً عکس رسید را ارسال کنید.\nفایل متنی یا ویدیو قبول نیست."
            )
            return

        photo = message.photo[-1]  # Get highest quality
        file_id = photo.file_id

        # Create pending payment record
        payment = await Payment.objects.acreate(
            order=None,
            brand=self.brand,
            user=user,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.AWAITING_CONFIRMATION,
            amount=Decimal(str(amount)),
            currency=wallet.currency,
            receipt_file=file_id,
            notes="شارژ کیف پول - کارت به کارت",
            expires_at=timezone.now() + timedelta(hours=24),
        )

        # Reset state
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        text = f"""
✅ <b>رسید دریافت شد</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ: <code>{amount:,.2f}</code> {symbol}
📋 شناسه پرداخت: <code>{str(payment.payment_id)[:8]}...</code>
📅 زمان ثبت: <code>{timezone.now().strftime("%Y/%m/%d - %H:%M")}</code>

⏳ <b>وضعیت:</b> در انتظار تأیید ادمین

💡 <b>توضیحات:</b>
• پیام شما ثبت شد و در صف بررسی قرار گرفت
• پس از تأیید توسط ادمین، موجودی کیف پول شما شارژ خواهد شد
• معمولاً بررسی ظرف <b>۱ تا ۲ ساعت</b> انجام می‌شود
• در صورت تأخیر، از منوی پشتیبانی تیکت ثبت کنید
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "💰 مشاهده کیف پول", "callback_data": "wallet"}],
                [
                    {
                        "text": "📋 تاریخچه تراکنش‌ها",
                        "callback_data": "wallet_history_1",
                    },
                ],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    # ==================== Gateway Payment Methods ====================

    async def show_gateway_payment(self, callback: types.CallbackQuery, amount: float):
        """Show online payment gateway options"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        symbol = self._get_currency_symbol(wallet.currency)

        # Get active gateways
        gateways = []
        async for gw in PaymentGateway.objects.filter(
            brand=self.brand, is_active=True
        ).order_by("name"):
            gateways.append(gw)

        if not gateways:
            await callback.answer("❌ درگاه پرداخت فعالی وجود ندارد.", show_alert=True)
            return

        text = f"""
🌐 <b>پرداخت آنلاین</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ: <code>{amount:,.2f}</code> {symbol}

💡 لطفاً درگاه پرداخت را انتخاب کنید:
        """

        button_rows = []
        for gw in gateways:
            # Calculate total with fees
            total = amount
            if gw.transaction_fee_percentage > 0:
                fee = amount * (gw.transaction_fee_percentage / 100)
                total = amount + fee
            if gw.fixed_transaction_fee > 0:
                total += gw.fixed_transaction_fee

            fee_text = ""
            if total != amount:
                fee_text = f" (+{total - amount:,.2f} کارمزد)"

            button_rows.append(
                [
                    {
                        "text": f"🔗 {gw.name}{fee_text}",
                        "callback_data": f"wallet_gw_{gw.id}_{int(amount * 100)}",
                    }
                ]
            )

        button_rows.append(
            [{"text": "🔙 بازگشت", "callback_data": f"charge_amount_{int(amount)}"}]
        )
        keyboard = self.create_keyboard(button_rows)

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def process_gateway_payment(
        self, callback: types.CallbackQuery, gateway_id: int, amount_cents: int
    ):
        """Process online gateway payment - creates payment and returns URL"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        amount = Decimal(str(amount_cents)) / Decimal("100")

        try:
            gateway = await PaymentGateway.objects.aget(
                id=gateway_id, brand=self.brand, is_active=True
            )
        except PaymentGateway.DoesNotExist:
            await callback.answer("❌ درگاه پرداخت یافت نشد.", show_alert=True)
            return

        # Calculate total with fees
        total = amount
        if gateway.transaction_fee_percentage > 0:
            total += amount * (gateway.transaction_fee_percentage / 100)
        if gateway.fixed_transaction_fee > 0:
            total += gateway.fixed_transaction_fee

        # Create payment record
        payment = await Payment.objects.acreate(
            brand=self.brand,
            user=user,
            payment_method=Payment.PaymentMethod.ONLINE_GATEWAY,
            status=Payment.PaymentStatus.PENDING,
            amount=total,
            currency=wallet.currency,
            gateway_name=gateway.name,
            gateway_response={"gateway_type": gateway.gateway_type},
            notes=f"شارژ کیف پول - {gateway.name}",
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        # Here you would integrate with the actual payment gateway
        # For ZarinPal, IDPay, etc., you would call their API to get payment URL
        # This is a placeholder that shows the concept

        symbol = self._get_currency_symbol(wallet.currency)
        text = f"""
🌐 <b>درگاه پرداخت {gateway.name}</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ: <code>{amount:,.2f}</code> {symbol}
💳 کارمزد: <code>{total - amount:,.2f}</code> {symbol}
💵 مبلغ نهایی: <code>{total:,.2f}</code> {symbol}

📋 شناسه پرداخت: <code>{str(payment.payment_id)[:8]}...</code>

⚠️ <b>توجه:</b>
درگاه پرداخت در حال تنظیم می‌باشد.
لطفاً از روش‌های پرداخت دیگر استفاده کنید یا با پشتیبانی تماس بگیرید.
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "💳 کارت به کارت",
                        "callback_data": f"wallet_pay_card_{amount}",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "charge_wallet"}],
            ]
        )

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer("⚠️ درگاه در حال تنظیم است", show_alert=True)

    # ==================== Crypto Payment Methods ====================

    async def show_crypto_payment(self, callback: types.CallbackQuery, amount: float):
        """Show cryptocurrency payment options"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        symbol = self._get_currency_symbol(wallet.currency)

        # Get active cryptocurrencies
        cryptos = []
        async for crypto in CryptoCurrency.objects.filter(
            brand=self.brand, is_active=True
        ).order_by("display_order"):
            cryptos.append(crypto)

        if not cryptos:
            await callback.answer("❌ رمزارز فعالی وجود ندارد.", show_alert=True)
            return

        text = f"""
₿ <b>پرداخت با رمزارز</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ: <code>{amount:,.2f}</code> {symbol}

💡 لطفاً رمزارز مورد نظر را انتخاب کنید:
        """

        button_rows = []
        for crypto in cryptos:
            # Calculate crypto amount
            crypto_amount = Decimal("0")
            if crypto.conversion_rate and crypto.conversion_rate > 0:
                crypto_amount = Decimal(str(amount)) / crypto.conversion_rate

            button_rows.append(
                [
                    {
                        "text": f"₿ {crypto.name} ({crypto.symbol}) - {crypto_amount:.6f}",
                        "callback_data": f"wallet_crypto_{crypto.id}_{int(amount * 100)}",
                    }
                ]
            )

        button_rows.append(
            [{"text": "🔙 بازگشت", "callback_data": f"charge_amount_{int(amount)}"}]
        )
        keyboard = self.create_keyboard(button_rows)

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def show_crypto_address(
        self, callback: types.CallbackQuery, crypto_id: int, amount_cents: int
    ):
        """Show cryptocurrency payment address and details"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        amount = Decimal(str(amount_cents)) / Decimal("100")
        symbol = self._get_currency_symbol(wallet.currency)

        try:
            crypto = await CryptoCurrency.objects.aget(
                id=crypto_id, brand=self.brand, is_active=True
            )
        except CryptoCurrency.DoesNotExist:
            await callback.answer("❌ رمزارز یافت نشد.", show_alert=True)
            return

        # Calculate crypto amount based on conversion rate
        crypto_amount = Decimal("0")
        if crypto.conversion_rate and crypto.conversion_rate > 0:
            crypto_amount = amount / crypto.conversion_rate

        # Add small buffer for price fluctuation (1%)
        crypto_amount_min = crypto_amount * Decimal("0.99")

        # Create pending payment record
        payment = await Payment.objects.acreate(
            brand=self.brand,
            user=user,
            payment_method=Payment.PaymentMethod.CRYPTOCURRENCY,
            status=Payment.PaymentStatus.PENDING,
            amount=amount,
            currency=wallet.currency,
            crypto_currency=crypto.symbol,
            crypto_amount=crypto_amount,
            crypto_address=crypto.wallet_address,
            notes=f"شارژ کیف پول - {crypto.name}",
            expires_at=timezone.now() + timedelta(hours=2),
        )

        # Calculate confirmation time estimate
        network_times = {
            "bitcoin": "۱۰-۶۰ دقیقه",
            "ethereum": "۲-۵ دقیقه",
            "tron": "۱-۳ دقیقه",
            "bsc": "۱-۳ دقیقه",
            "polygon": "۱-۲ دقیقه",
        }
        confirm_time = network_times.get(crypto.network, "۱۰-۶۰ دقیقه")

        text = f"""
₿ <b>پرداخت با {crypto.name} ({crypto.symbol})</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ: <code>{amount:,.2f}</code> {symbol}
📊 شبکه: <code>{crypto.get_network_display()}</code>

💵 <b>مبلغ واریز:</b>
<code>{crypto_amount:.8f}</code> {crypto.symbol}
⚠️ حداقل قابل قبول: <code>{crypto_amount_min:.8f}</code> {crypto.symbol}

📋 <b>آدرس واریز:</b>
<code>{crypto.wallet_address}</code>

⏰ <b>زمان تأیید تقریبی:</b> {confirm_time}
⏳ <b>زمان انقضا:</b> ۲ ساعت

━━━━━━━━━━━━━━━━━━━━━━

⚠️ <b>توجه‌های مهم:</b>
❗ دقیقاً مبلغ مشخص شده را واریز کنید
❗ فقط از شبکه <b>{crypto.get_network_display()}</b> استفاده کنید
❗ واریز از شبکه‌های دیگر باعث <b>از دست رفتن وجه</b> می‌شود
❗ حداقل تأیید مورد نیاز: <b>{crypto.required_confirmations}</b> بلاک
        """

        button_rows = [
            [
                {
                    "text": "📋 کپی آدرس",
                    "callback_data": f"copy_crypto_addr_{payment.payment_id}",
                }
            ],
            [
                {
                    "text": "📝 ارسال TXID (اختیاری)",
                    "callback_data": f"send_txid_{payment.payment_id}",
                }
            ],
            [{"text": "❌ انصراف", "callback_data": "charge_wallet"}],
        ]
        keyboard = self.create_keyboard(button_rows)

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def copy_crypto_address(self, callback: types.CallbackQuery, payment_id: str):
        """Copy crypto address to clipboard"""
        try:
            payment = await Payment.objects.aget(payment_id=payment_id)
            if payment.crypto_address:
                await callback.answer(payment.crypto_address, show_alert=False)
            else:
                await callback.answer("❌ آدرس یافت نشد", show_alert=True)
        except Payment.DoesNotExist:
            await callback.answer("❌ پرداخت یافت نشد", show_alert=True)

    async def request_txid(self, callback: types.CallbackQuery, payment_id: str):
        """Request TXID from user"""
        user, _ = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {
                "action": "wallet_crypto_txid",
                "payment_id": payment_id,
                "step": "waiting_txid",
            },
        )

        text = """
📝 <b>ارسال TXID</b>
━━━━━━━━━━━━━━━━━━━━━━

لطفاً شناسه تراکنش (TXID) خود را ارسال کنید:

💡 <b>TXID چیست؟</b>
TXID یک رشته طولانی از حروف و اعداد است که پس از انجام تراکنش در کیف پول شما نمایش داده می‌شود.

📌 <b>مثال TXID:</b>
<code>0x1234567890abcdef...</code>

⚠️ ارسال TXID اختیاری است و صرفاً برای تسریع در فرآیند تأیید استفاده می‌شود.
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "⏭️ رد کردن", "callback_data": "wallet"}],
                [{"text": "❌ انصراف از پرداخت", "callback_data": "charge_wallet"}],
            ]
        )

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def handle_txid_message(self, message: types.Message, user, state: BotState):
        """Handle TXID input"""
        txid = message.text.strip()
        payment_id = state.state_data.get("payment_id") if state.state_data else None

        if not payment_id:
            await message.reply("❌ خطا در پردازش. لطفاً دوباره تلاش کنید.")
            return

        try:
            payment = await Payment.objects.aget(payment_id=payment_id, user=user)
        except Payment.DoesNotExist:
            await message.reply("❌ پرداخت یافت نشد.")
            return

        # Update payment with TXID
        payment.crypto_txid = txid
        payment.status = Payment.PaymentStatus.AWAITING_CONFIRMATION
        await payment.asave()

        # Reset state
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        symbol = self._get_currency_symbol(payment.currency)

        text = f"""
✅ <b>TXID دریافت شد</b>
━━━━━━━━━━━━━━━━━━━━━━

🆔 TXID: <code>{txid[:30]}...</code>
💰 مبلغ: <code>{payment.amount:,.2f}</code> {symbol}
₿ رمزارز: {payment.crypto_currency}

⏳ <b>وضعیت:</b> در انتظار تأیید تراکنش

💡 <b>توضیحات:</b>
• تراکنش شما ثبت شد
• پس از تأیید در بلاکچین، موجودی کیف پول شارژ می‌شود
• معمولاً تأیید ظرف <b>۱۰ تا ۶۰ دقیقه</b> انجام می‌شود
• با ارسال TXID، فرآیند بررسی سریع‌تر انجام می‌شود
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "💰 مشاهده کیف پول", "callback_data": "wallet"}],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    # ==================== Telegram Stars Payment Methods ====================

    async def show_stars_payment(self, callback: types.CallbackQuery, amount: float):
        """Show Telegram Stars payment option"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        symbol = self._get_currency_symbol(wallet.currency)

        # Calculate stars amount (1 star ≈ $0.01)
        # This rate should be configurable per brand
        stars_rate = Decimal("0.01")  # 1 star = $0.01
        stars_amount = int(Decimal(str(amount)) / stars_rate)

        if stars_amount < 1:
            await callback.answer("❌ مبلغ خیلی کم است.", show_alert=True)
            return

        text = f"""
⭐ <b>پرداخت با ستاره‌های تلگرام</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ شارژ: <code>{amount:,.2f}</code> {symbol}
⭐ معادل: <code>{stars_amount:,}</code> ستاره

📊 <b>نرخ تبدیل:</b> ۱ ستاره = ۰.۰۱ دلار

━━━━━━━━━━━━━━━━━━━━━━

✅ <b>مزایای پرداخت با ستاره:</b>
• ✨ پرداخت فوری و خودکار
• 🚫 نیاز به تأیید ادمین نیست
• 💰 کیف پول بلافاصله شارژ می‌شود

⚠️ <b>توجه:</b>
• موجودی ستاره باید در حساب تلگرام شما کافی باشد
• ستاره‌ها قابل بازگشت نیستند
        """

        button_rows = [
            [
                {
                    "text": f"⭐ پرداخت {stars_amount:,} ستاره",
                    "callback_data": f"wallet_stars_pay_{stars_amount}_{int(amount * 100)}",
                }
            ],
            [{"text": "🔙 بازگشت", "callback_data": f"charge_amount_{int(amount)}"}],
        ]
        keyboard = self.create_keyboard(button_rows)

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def process_stars_payment(
        self, callback: types.CallbackQuery, stars_amount: int, amount_cents: int
    ):
        """Process Telegram Stars payment - send invoice"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        amount = Decimal(str(amount_cents)) / Decimal("100")
        symbol = self._get_currency_symbol(wallet.currency)

        # Send invoice via Telegram
        await self.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"شارژ کیف پول - {self.brand.name}",
            description=f"شارژ {amount:,.2f} {symbol} به کیف پول شما",
            payload=f"wallet_charge_{user.id}_{stars_amount}",
            currency="XTR",  # Telegram Stars currency code
            prices=[
                LabeledPrice(label=f"شارژ {amount:,.2f} {symbol}", amount=stars_amount)
            ],
            provider_token="",  # Empty for Telegram Stars
        )

        await callback.answer()

    async def handle_pre_checkout_query(self, pre_checkout_query: PreCheckoutQuery):
        """Handle pre-checkout query for Telegram Stars"""
        try:
            payload = pre_checkout_query.invoice_payload
            if payload and payload.startswith("wallet_charge_"):
                # Validate the payload format
                parts = payload.split("_")
                if len(parts) >= 3:
                    await pre_checkout_query.answer(ok=True)
                    return

            await pre_checkout_query.answer(
                ok=False, error_message="خطا در پردازش پرداخت. لطفاً دوباره تلاش کنید."
            )
        except Exception as e:
            logger.error(f"Error in pre-checkout query: {e}")
            await pre_checkout_query.answer(
                ok=False, error_message="خطای سیستمی. لطفاً بعداً تلاش کنید."
            )

    async def handle_successful_payment(self, message: types.Message, user):
        """Handle successful Telegram Stars payment"""
        if not message.successful_payment:
            return

        stars_amount = message.successful_payment.total_amount
        charge_id = message.successful_payment.telegram_payment_charge_id

        # Convert stars to currency (1 star = $0.01)
        stars_rate = Decimal("0.01")
        amount = Decimal(str(stars_amount)) * stars_rate

        wallet = await self.get_or_create_wallet(user)
        symbol = self._get_currency_symbol(wallet.currency)

        # Create payment record
        await Payment.objects.acreate(
            brand=self.brand,
            user=user,
            payment_method=Payment.PaymentMethod.TELEGRAM_STARS,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=amount,
            currency=wallet.currency,
            stars_amount=stars_amount,
            telegram_payment_charge_id=charge_id,
            notes="شارژ کیف پول - ستاره تلگرام",
        )

        # Credit wallet
        await self._credit_wallet(
            wallet,
            amount,
            WalletTransaction.TransactionType.DEPOSIT,
            f"شارژ کیف پول - {stars_amount:,} ستاره تلگرام",
            metadata={"payment_method": "telegram_stars", "stars": stars_amount},
        )

        # Refresh wallet to get updated balance
        await wallet.arefresh_from_db()

        text = f"""
✅ <b>پرداخت موفق!</b>
━━━━━━━━━━━━━━━━━━━━━━

⭐ ستاره‌های پرداخت شده: <code>{stars_amount:,}</code>
💰 مبلغ شارژ: <code>{amount:,.2f}</code> {symbol}

💵 <b>موجودی جدید کیف پول:</b>
<code>{wallet.balance:,.2f}</code> {symbol}

🎉 ممنون از پرداخت شما! کیف پول با موفقیت شارژ شد.
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "💰 مشاهده کیف پول", "callback_data": "wallet"}],
                [{"text": "🛒 خرید اشتراک", "callback_data": "purchase_subscription"}],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    # ==================== Coupon Methods ====================

    async def show_coupon_input(self, callback: types.CallbackQuery):
        """Show coupon code input"""
        user, _ = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"action": "wallet_coupon", "step": "waiting_coupon"},
        )

        text = """
🎁 <b>استفاده از کد تخفیف</b>
━━━━━━━━━━━━━━━━━━━━━━

لطفاً کد تخفیف خود را وارد کنید:

💡 <b>نکات:</b>
• کدهای تخفیف معمولاً ترکیبی از حروف و اعداد هستند
• کدها به حروف بزرگ و کوچک حساس نیستند
• هر کد معمولاً فقط یک بار قابل استفاده است

📌 <b>مثال:</b> <code>WELCOME50</code> یا <code>VPN2024</code>
        """

        keyboard = self.get_back_keyboard("wallet")

        await self._safe_edit_message(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
        )
        await callback.answer()

    async def handle_coupon_message(
        self, message: types.Message, user, state: BotState
    ):
        """Handle coupon code input and validate"""
        code = message.text.strip().upper()

        try:
            coupon = await Coupon.objects.aget(
                code=code,
                brand=self.brand,
                is_active=True,
            )
        except Coupon.DoesNotExist:
            await message.reply(
                "❌ <b>کد تخفیف نامعتبر است!</b>\n\n"
                "• کد را به درستی وارد کنید\n"
                "• ممکن است کد منقضی شده باشد\n"
                "• برای دریافت کد تخفیف با پشتیبانی تماس بگیرید"
            )
            return

        now = timezone.now()

        # Check validity period
        if now < coupon.valid_from:
            await message.reply(
                f"❌ این کد تخفیف از تاریخ <code>{coupon.valid_from.strftime('%Y/%m/%d')}</code> فعال می‌شود."
            )
            return

        if now > coupon.valid_until:
            await message.reply(
                f"❌ این کد تخفیف در تاریخ <code>{coupon.valid_until.strftime('%Y/%m/%d')}</code> منقضی شده است."
            )
            return

        # Check total usage limit
        if coupon.max_uses and coupon.current_uses >= coupon.max_uses:
            await message.reply("❌ ظرفیت استفاده از این کد تخفیف تکمیل شده است.")
            return

        # Check per-user limit
        user_uses = await CouponUsage.objects.filter(coupon=coupon, user=user).acount()
        if user_uses >= coupon.max_uses_per_user:
            await message.reply("❌ شما قبلاً از این کد تخفیف استفاده کرده‌اید.")
            return

        # Check if new users only
        if coupon.new_users_only:
            has_orders = await Order.objects.filter(
                user=user, brand=self.brand
            ).aexists()
            if has_orders:
                await message.reply(
                    "❌ این کد تخفیف فقط برای کاربران جدید قابل استفاده است."
                )
                return

        # Calculate discount/bonus amount
        if coupon.coupon_type == Coupon.CouponType.PERCENTAGE:
            # For wallet, percentage coupons give a fixed bonus
            bonus = Decimal(str(coupon.discount_value))
        else:
            bonus = Decimal(str(coupon.discount_value))

        # Apply max discount limit
        if coupon.max_discount_amount:
            bonus = min(bonus, coupon.max_discount_amount)

        # Reset state
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        # Apply bonus to wallet
        wallet = await self.get_or_create_wallet(user)
        symbol = self._get_currency_symbol(wallet.currency)

        await self._credit_wallet(
            wallet,
            bonus,
            WalletTransaction.TransactionType.BONUS,
            f"جوایز کد تخفیف {code}",
            metadata={"coupon_code": code, "coupon_id": coupon.id},
        )

        # Update coupon usage
        await CouponUsage.objects.acreate(
            coupon=coupon,
            user=user,
            order=None,
            discount_amount=bonus,
        )
        coupon.current_uses += 1
        await coupon.asave()

        # Refresh wallet balance
        await wallet.arefresh_from_db()

        text = f"""
🎉 <b>کد تخفیف با موفقیت فعال شد!</b>
━━━━━━━━━━━━━━━━━━━━━━

🎁 کد تخفیف: <code>{code}</code>
📝 نام: {coupon.name}
💰 مبلغ جایزه: <code>{bonus:,.2f}</code> {symbol}

💵 <b>موجودی جدید کیف پول:</b>
<code>{wallet.balance:,.2f}</code> {symbol}

🎊 ممنون از استفاده از کد تخفیف!
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "💰 مشاهده کیف پول", "callback_data": "wallet"}],
                [{"text": "🔄 شارژ بیشتر", "callback_data": "charge_wallet"}],
                [{"text": "🛒 خرید اشتراک", "callback_data": "purchase_subscription"}],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    # ==================== Wallet Operations (Public) ====================

    async def credit_wallet(
        self,
        user,
        amount: Decimal,
        transaction_type: str,
        description: str,
        reference_id: str = None,
        metadata: dict = None,
    ) -> Optional[WalletTransaction]:
        """Public method to credit wallet - used by other handlers"""
        wallet = await self.get_or_create_wallet(user)

        if wallet.is_frozen:
            logger.warning(f"Cannot credit frozen wallet for user {user.id}")
            return None

        return await self._credit_wallet(
            wallet, amount, transaction_type, description, reference_id, metadata
        )

    async def debit_wallet(
        self,
        user,
        amount: Decimal,
        transaction_type: str,
        description: str,
        reference_id: str = None,
        metadata: dict = None,
    ) -> Optional[WalletTransaction]:
        """Public method to debit wallet - used by purchase handler"""
        wallet = await self.get_or_create_wallet(user)

        if wallet.is_frozen:
            logger.warning(f"Cannot debit frozen wallet for user {user.id}")
            return None

        return await self._debit_wallet(
            wallet, amount, transaction_type, description, reference_id, metadata
        )

    async def check_wallet_balance(self, user) -> Decimal:
        """Check user wallet balance"""
        wallet = await self.get_or_create_wallet(user)
        return wallet.balance

    async def can_afford(self, user, amount: Decimal) -> bool:
        """Check if user can afford the amount"""
        wallet = await self.get_or_create_wallet(user)
        return wallet.balance >= amount and not wallet.is_frozen

    # ==================== Internal Helper Methods ====================

    async def _credit_wallet(
        self,
        wallet: Wallet,
        amount: Decimal,
        transaction_type: str,
        description: str,
        reference_id: str = None,
        metadata: dict = None,
    ) -> WalletTransaction:
        """Credit amount to wallet and create transaction record"""
        async with db_transaction.atomic():
            # Lock the wallet row
            wallet = await Wallet.objects.select_for_update().aget(pk=wallet.pk)

            balance_before = wallet.balance
            balance_after = balance_before + amount

            # Create transaction record
            transaction = await WalletTransaction.objects.acreate(
                wallet=wallet,
                transaction_type=transaction_type,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_after,
                reference_id=reference_id,
                description=description,
                metadata=metadata or {},
            )

            # Update wallet balance
            wallet.balance = balance_after
            await wallet.asave()

            logger.info(
                f"Wallet credited: user={wallet.user_id}, amount={amount}, "
                f"balance_before={balance_before}, balance_after={balance_after}"
            )

            return transaction

    async def _debit_wallet(
        self,
        wallet: Wallet,
        amount: Decimal,
        transaction_type: str,
        description: str,
        reference_id: str = None,
        metadata: dict = None,
    ) -> Optional[WalletTransaction]:
        """Debit amount from wallet and create transaction record"""
        async with db_transaction.atomic():
            # Lock the wallet row
            wallet = await Wallet.objects.select_for_update().aget(pk=wallet.pk)

            if wallet.balance < amount:
                logger.warning(
                    f"Insufficient balance: user={wallet.user_id}, "
                    f"balance={wallet.balance}, amount={amount}"
                )
                return None

            if wallet.is_frozen:
                logger.warning(f"Wallet is frozen: user={wallet.user_id}")
                return None

            balance_before = wallet.balance
            balance_after = balance_before - amount

            # Create transaction record
            transaction = await WalletTransaction.objects.acreate(
                wallet=wallet,
                transaction_type=transaction_type,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_after,
                reference_id=reference_id,
                description=description,
                metadata=metadata or {},
            )

            # Update wallet balance
            wallet.balance = balance_after
            await wallet.asave()

            logger.info(
                f"Wallet debited: user={wallet.user_id}, amount={amount}, "
                f"balance_before={balance_before}, balance_after={balance_after}"
            )

            return transaction

    @sync_to_async
    def _calculate_total_by_type(
        self, wallet: Wallet, transaction_type: str
    ) -> Decimal:
        """Calculate total amount for a specific transaction type"""
        result = WalletTransaction.objects.filter(
            wallet=wallet, transaction_type=transaction_type
        ).aggregate(total=Sum("amount"))
        return result["total"] or Decimal("0")

    def _get_currency_symbol(self, currency: str) -> str:
        """Get currency symbol"""
        symbols = {
            "USD": "$",
            "EUR": "€",
            "GBP": "£",
            "IRR": "تومان",
            "IRT": "تومان",
            "AED": "درهم",
            "TRY": "لیر",
        }
        return symbols.get(currency, currency)

    def _get_transaction_icon(self, transaction_type: str) -> str:
        """Get icon for transaction type"""
        icons = {
            WalletTransaction.TransactionType.DEPOSIT: "📥",
            WalletTransaction.TransactionType.WITHDRAWAL: "📤",
            WalletTransaction.TransactionType.PAYMENT: "💸",
            WalletTransaction.TransactionType.REFUND: "🔄",
            WalletTransaction.TransactionType.BONUS: "🎁",
            WalletTransaction.TransactionType.REFERRAL_REWARD: "👥",
            WalletTransaction.TransactionType.ADMIN_ADJUSTMENT: "⚙️",
        }
        return icons.get(transaction_type, "📝")

    def _is_credit_type(self, transaction_type: str) -> bool:
        """Check if transaction type is credit (adds to balance)"""
        credit_types = [
            WalletTransaction.TransactionType.DEPOSIT,
            WalletTransaction.TransactionType.REFUND,
            WalletTransaction.TransactionType.BONUS,
            WalletTransaction.TransactionType.REFERRAL_REWARD,
            WalletTransaction.TransactionType.ADMIN_ADJUSTMENT,
        ]
        return transaction_type in credit_types

    def _format_amount(self, amount, currency: str) -> str:
        """Format amount based on currency"""
        if currency in ["IRR", "IRT"]:
            return f"{int(amount):,}"
        return f"{amount:,.2f}"

    def _format_persian_date(self, dt) -> str:
        """Format date in Persian style"""
        if dt:
            return dt.strftime("%Y/%m/%d")
        return "نامشخص"

    def _truncate_text(self, text: str, max_length: int) -> str:
        """Truncate text with ellipsis"""
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    async def _safe_edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard,
    ):
        """Safely edit message with fallback to send new message"""
        try:
            await self.edit_message_with_keyboard(chat_id, message_id, text, keyboard)
        except Exception as e:
            logger.debug(f"Could not edit message, sending new: {e}")
            await self.send_message_with_keyboard(chat_id, text, keyboard)

    async def _send_charge_menu(self, chat_id: int, user):
        """Send charge menu directly (for message handlers)"""
        wallet = await self.get_or_create_wallet(user)
        currency = wallet.currency
        amounts = self.PRESET_AMOUNTS.get(currency, self.PRESET_AMOUNTS["USD"])
        symbol = self._get_currency_symbol(currency)

        text = f"""
🔄 <b>شارژ کیف پول</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 موجودی فعلی: <code>{wallet.balance:,.2f}</code> {symbol}

💡 مبلغ شارژ مورد نظر را انتخاب کنید:
        """

        button_rows = []
        for i in range(0, len(amounts), 2):
            row = []
            for j in range(2):
                if i + j < len(amounts):
                    amount = amounts[i + j]
                    formatted = self._format_amount(amount, currency)
                    row.append(
                        {
                            "text": f"💰 {formatted} {symbol}",
                            "callback_data": f"charge_amount_{amount}",
                        }
                    )
            button_rows.append(row)

        button_rows.append(
            [{"text": "✏️ وارد کردن مبلغ دلخواه", "callback_data": "charge_custom"}]
        )
        button_rows.append([{"text": "🔙 بازگشت", "callback_data": "wallet"}])

        keyboard = self.create_keyboard(button_rows)
        await self.send_message_with_keyboard(chat_id, text, keyboard)

    # ==================== No-op Handler ====================

    async def noop(self, callback: types.CallbackQuery):
        """No operation - used for pagination buttons that shouldn't do anything"""
        await callback.answer()
