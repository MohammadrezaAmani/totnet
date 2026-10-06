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
from django.db.models import Sum
from django.utils import timezone

from apps.bot.models import BotState
from apps.orders.models import (
    CryptoCurrency,
    Payment,
    PaymentCard,
    Wallet,
    WalletTransaction,
)
from apps.orders.services import (
    WalletCouponError,
    WalletOperationError,
    credit_wallet as apply_wallet_credit,
    debit_wallet as apply_wallet_debit,
    redeem_wallet_coupon,
    validate_stars_pre_checkout,
    confirm_stars_payment,
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
        "T": [50_000, 100_000, 200_000, 500_000, 1_000_000, 5_000_000],
        "IRR": [50_000, 100_000, 200_000, 500_000, 1_000_000, 5_000_000],
        "IRT": [50_000, 100_000, 200_000, 500_000, 1_000_000, 5_000_000],
    }

    # Minimum and maximum charge amounts
    LIMITS = {
        "USD": {"min": 1, "max": 10_000},
        "EUR": {"min": 1, "max": 10_000},
        "GBP": {"min": 1, "max": 10_000},
        "T": {"min": 10_000, "max": 100_000_000},
        "IRR": {"min": 10_000, "max": 100_000_000},
        "IRT": {"min": 10_000, "max": 100_000_000},
    }

    def _validated_charge_amount(self, amount, currency: str) -> Decimal:
        """Validate callback/user amounts on the server, independent of the UI."""
        if currency not in self.LIMITS:
            raise WalletOperationError("Wallet currency is not supported for top-ups")
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise WalletOperationError("Invalid charge amount") from exc
        limits = self.LIMITS[currency]
        precision = Decimal("1") if currency in {"T", "IRR", "IRT"} else Decimal("0.01")
        if (
            not value.is_finite()
            or value != value.quantize(precision)
            or value < limits["min"]
            or value > limits["max"]
        ):
            raise WalletOperationError("Charge amount is outside the allowed range")
        return value

    # ==================== Main Methods ====================

    async def get_or_create_wallet(self, user) -> Wallet:
        """Get or create user wallet"""
        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
        except Wallet.DoesNotExist:
            wallet, _ = await Wallet.objects.aget_or_create(
                user=user,
                brand=self.brand,
                defaults={
                    "balance": Decimal("0.00"),
                    "currency": self.brand.currency,
                    "is_active": True,
                    "is_frozen": False,
                },
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
        if not wallet.is_active or wallet.is_frozen:
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
                delta = trans.balance_after - trans.balance_before
                sign = "+" if delta >= 0 else "-"
                amount_str = f"{sign}{abs(trans.amount):,.2f}"
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
                delta = trans.balance_after - trans.balance_before
                sign = "+" if delta >= 0 else "-"
                amount_str = f"{sign}{abs(trans.amount):,.2f}"
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

        if not wallet.is_active or wallet.is_frozen:
            await callback.answer(
                "❌ کیف پول شما مسدود شده است.\nلطفاً با پشتیبانی تماس بگیرید.",
                show_alert=True,
            )
            return

        currency = wallet.currency
        amounts = self.PRESET_AMOUNTS.get(currency)
        if not amounts:
            await callback.answer(
                "شارژ کیف پول برای این ارز هنوز پیکربندی نشده است.", show_alert=True
            )
            return
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
        wallet = await self.get_or_create_wallet(user)

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"action": "wallet_charge", "step": "waiting_custom_amount"},
        )

        limits = self.LIMITS.get(wallet.currency)
        if not limits:
            await callback.answer(
                "شارژ کیف پول برای این ارز هنوز پیکربندی نشده است.", show_alert=True
            )
            return
        symbol = self._get_currency_symbol(wallet.currency)

        text = f"""
✏️ <b>مبلغ دلخواه</b>
━━━━━━━━━━━━━━━━━━━━━━

لطفاً مبلغ مورد نظر برای شارژ را وارد کنید:

📊 <b>محدودیت‌ها:</b>
├ حداقل: <code>{self._format_amount(limits["min"], wallet.currency)}</code> {symbol}
└ حداکثر: <code>{self._format_amount(limits["max"], wallet.currency)}</code> {symbol}

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
            wallet = await self.get_or_create_wallet(user)
            amount = self._validated_charge_amount(amount_text, wallet.currency)

            limits = self.LIMITS.get(wallet.currency)
            if not limits:
                raise WalletOperationError("Wallet currency is not supported for top-ups")
            symbol = self._get_currency_symbol(wallet.currency)

            if amount < limits["min"]:
                await message.reply(
                    f"❌ حداقل مبلغ شارژ <code>{self._format_amount(limits['min'], wallet.currency)}</code> {symbol} می‌باشد."
                )
                return

            if amount > limits["max"]:
                await message.reply(
                    f"❌ حداکثر مبلغ شارژ <code>{self._format_amount(limits['max'], wallet.currency)}</code> {symbol} می‌باشد."
                )
                return

            # Proceed to payment method selection
            await self.show_payment_methods(message.chat.id, user, amount)

        except (ValueError, InvalidOperation, WalletOperationError):
            await message.reply(
                "❌ لطفاً یک عدد معتبر وارد کنید.\n\n💡 مثال: 50000 یا 100000"
            )

    async def initiate_charge(self, callback: types.CallbackQuery, amount: float):
        """Initiate wallet charge process - show payment methods"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)

        try:
            amount = self._validated_charge_amount(amount, wallet.currency)
        except WalletOperationError:
            await callback.answer("❌ مبلغ شارژ معتبر نیست.", show_alert=True)
            return

        if not wallet.is_active or wallet.is_frozen:
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
        try:
            amount = self._validated_charge_amount(amount, wallet.currency)
        except WalletOperationError:
            if callback:
                await callback.answer("❌ مبلغ شارژ معتبر نیست.", show_alert=True)
            else:
                await self.send_message_with_keyboard(
                    chat_id,
                    "❌ مبلغ شارژ معتبر نیست.",
                    self.get_back_keyboard("charge_wallet"),
                )
            return
        if not wallet.is_active or wallet.is_frozen:
            if callback:
                await callback.answer("❌ کیف پول شما در دسترس نیست.", show_alert=True)
            else:
                await self.send_message_with_keyboard(
                    chat_id,
                    "❌ کیف پول شما در دسترس نیست.",
                    self.get_back_keyboard("wallet"),
                )
            return
        symbol = self._get_currency_symbol(wallet.currency)

        # Check available payment methods
        has_cards = await PaymentCard.objects.filter(
            brand=self.brand, is_active=True
        ).aexists()

        has_crypto = await CryptoCurrency.objects.filter(
            brand=self.brand, is_active=True
        ).aexists()

        # Store amount in state
        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"action": "wallet_charge", "amount": str(amount)},
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

        # The current Stars conversion is denominated in USD.
        if wallet.currency == "USD":
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
        try:
            amount = self._validated_charge_amount(amount, wallet.currency)
        except WalletOperationError:
            await callback.answer("❌ مبلغ شارژ معتبر نیست.", show_alert=True)
            return
        if not wallet.is_active or wallet.is_frozen:
            await callback.answer("❌ کیف پول شما در دسترس نیست.", show_alert=True)
            return
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
            {
                "action": "wallet_charge",
                "amount": str(amount),
                "step": "waiting_receipt",
            },
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

        text += f"""
━━━━━━━━━━━━━━━━━━━━━━

⚠️ <b>راهنمای پرداخت:</b>
1️⃣ مبلغ دقیق <b>{amount:,.2f}</b> را به یکی از کارت‌های بالا واریز کنید
2️⃣ از رسید پرداخت عکس بگیرید
3️⃣ روی دکمه "📸 ارسال رسید" کلیک کنید
4️⃣ عکس رسید را ارسال کنید

⏰ لطفاً ظرف <b>۲۴ ساعت</b> رسید را ارسال کنید.
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
        wallet = await self.get_or_create_wallet(user)

        if amount is None:
            await message.reply("❌ خطا در پردازش. لطفاً دوباره تلاش کنید.")
            await self._send_charge_menu(message.chat.id, user)
            return

        try:
            amount = self._validated_charge_amount(amount, wallet.currency)
        except WalletOperationError:
            await message.reply("❌ مبلغ پرداخت منقضی یا نامعتبر است. دوباره شارژ را آغاز کنید.")
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
            return

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
        wallet = await Wallet.objects.aget(user=user, brand=self.brand)
        payment = await Payment.objects.acreate(
            order=None,
            wallet=wallet,
            brand=self.brand,
            user=user,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.PENDING,
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

        notified = await self._notify_admin_receipt(wallet, payment, file_id)
        if not notified:
            await self.send_message_with_keyboard(
                message.chat.id,
                "⚠️ رسید ذخیره شد، اما اعلان برای ادمین ارسال نشد. لطفاً با پشتیبانی تماس بگیرید.",
                keyboard,
            )

    async def _notify_admin_receipt(
        self, wallet: Wallet, payment: Payment, file_id: str
    ) -> bool:
        """ارسال رسید به ادمین برای تأیید/رد"""
        admin_text = (
            f"🧾شارژ کیف پول\n"
            f"سفارش: {payment.amount}\n"
            f"مبلغ: {self.format_price(payment.amount, payment.currency)}\n"
            f"payment_id: {payment.id}"
        )
        admin_kb = self.create_keyboard(
            [
                [
                    {
                        "text": "✅ تأیید",
                        "callback_data": f"admin_confirm_wallet_{payment.id}",
                    },
                    {
                        "text": "❌ رد",
                        "callback_data": f"admin_reject_wallet_{payment.id}",
                    },
                ]
            ]
        )

        notified = 0
        async for user in self.get_brand_admin_recipients():
            try:
                await self.bot.send_photo(
                    chat_id=user.telegram_id,
                    photo=file_id,
                    caption=admin_text,
                    reply_markup=admin_kb,
                )
                notified += 1
            except Exception:
                logger.exception(
                    "Failed to send wallet receipt notification to admin %s", user.pk
                )
        if notified:
            logger.info(
                "Sent wallet receipt notification to %s admin(s) for brand %s, payment %s",
                notified,
                self.brand.pk,
                payment.pk,
            )
        else:
            logger.error(
                "No Telegram admin received wallet receipt notification for brand %s, payment %s",
                self.brand.pk,
                payment.pk,
            )
        return notified > 0

    async def admin_confirm_payment(
        self,
        callback: types.CallbackQuery,
        payment_id: int,
    ):
        """Confirm payment."""

        user, _ = await self.get_or_create_user(callback.from_user, use_cache=False)
        if not await self.has_admin_access(user):
            await callback.answer("⛔ دسترسی ادمین ندارید.", show_alert=True)
            return

        try:
            payment = await Payment.objects.aget(id=int(payment_id), brand=self.brand)
        except Payment.DoesNotExist:
            await callback.answer("❌ پرداخت یافت نشد.", show_alert=True)
            return
        if payment.status not in (
            Payment.PaymentStatus.PENDING,
            Payment.PaymentStatus.AWAITING_CONFIRMATION,
        ) or not payment.wallet_id:
            await callback.answer("❌ این پرداخت قابل تأیید نیست.", show_alert=True)
            return
        payment.status = Payment.PaymentStatus.CONFIRMED
        payment.verified_by = user
        payment.verified_at = timezone.now()
        await payment.asave(
            update_fields=["status", "verified_by", "verified_at", "updated_at"]
        )

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("✅ پرداخت تأیید شد.")

    async def admin_reject_payment(
        self,
        callback: types.CallbackQuery,
        payment_id: int,
    ):
        """Reject payment."""

        user, _ = await self.get_or_create_user(callback.from_user, use_cache=False)
        if not await self.has_admin_access(user):
            await callback.answer("⛔ دسترسی ادمین ندارید.", show_alert=True)
            return

        try:
            payment = await Payment.objects.aget(id=int(payment_id), brand=self.brand)
        except Payment.DoesNotExist:
            await callback.answer("❌ پرداخت یافت نشد.", show_alert=True)
            return

        if payment.status not in (
            Payment.PaymentStatus.PENDING,
            Payment.PaymentStatus.AWAITING_CONFIRMATION,
        ) or not payment.wallet_id:
            await callback.answer("❌ این پرداخت قابل رد نیست.", show_alert=True)
            return

        payment.status = Payment.PaymentStatus.FAILED
        payment.verified_by = user
        payment.verified_at = timezone.now()
        await payment.asave(
            update_fields=["status", "verified_by", "verified_at", "updated_at"]
        )

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("❌ پرداخت رد شد.")
    # ==================== Gateway Payment Methods ====================

    async def show_gateway_payment(self, callback: types.CallbackQuery, amount: float):
        """Report unavailable until a real gateway adapter is installed."""
        await callback.answer(
            "درگاه آنلاین هنوز به سرویس پرداخت متصل نیست؛ هیچ پرداختی ثبت نشد.",
            show_alert=True,
        )

    async def process_gateway_payment(
        self, callback: types.CallbackQuery, gateway_id: int, amount_cents: int
    ):
        """Never create a pending payment without a configured provider."""
        await callback.answer(
            "درگاه آنلاین هنوز به سرویس پرداخت متصل نیست؛ هیچ پرداختی ثبت نشد.",
            show_alert=True,
        )

    # ==================== Crypto Payment Methods ====================

    async def show_crypto_payment(self, callback: types.CallbackQuery, amount: float):
        """Show cryptocurrency payment options"""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        try:
            amount = self._validated_charge_amount(amount, wallet.currency)
        except WalletOperationError:
            await callback.answer("❌ مبلغ شارژ معتبر نیست.", show_alert=True)
            return
        if not wallet.is_active or wallet.is_frozen:
            await callback.answer("❌ کیف پول شما در دسترس نیست.", show_alert=True)
            return
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
            if crypto.conversion_rate <= 0:
                continue
            crypto_amount = amount / crypto.conversion_rate

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
        if len(button_rows) == 1:
            await callback.answer("برای رمزارزها نرخ تبدیل معتبر ثبت نشده است.", show_alert=True)
            return
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
        try:
            amount = self._validated_charge_amount(amount, wallet.currency)
        except WalletOperationError:
            await callback.answer("❌ مبلغ شارژ معتبر نیست.", show_alert=True)
            return
        if not wallet.is_active or wallet.is_frozen:
            await callback.answer("❌ کیف پول شما در دسترس نیست.", show_alert=True)
            return
        symbol = self._get_currency_symbol(wallet.currency)

        try:
            crypto = await CryptoCurrency.objects.aget(
                id=crypto_id, brand=self.brand, is_active=True
            )
        except CryptoCurrency.DoesNotExist:
            await callback.answer("❌ رمزارز یافت نشد.", show_alert=True)
            return

        if crypto.conversion_rate <= 0:
            await callback.answer("❌ نرخ تبدیل رمزارز تنظیم نشده است.", show_alert=True)
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
            wallet=wallet,
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
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            payment = await Payment.objects.aget(
                payment_id=payment_id,
                user=user,
                brand=self.brand,
                payment_method=Payment.PaymentMethod.CRYPTOCURRENCY,
                status__in=(
                    Payment.PaymentStatus.PENDING,
                    Payment.PaymentStatus.AWAITING_CONFIRMATION,
                ),
            )
            if payment.crypto_address:
                await callback.answer(payment.crypto_address, show_alert=False)
            else:
                await callback.answer("❌ آدرس یافت نشد", show_alert=True)
        except Payment.DoesNotExist:
            await callback.answer("❌ پرداخت یافت نشد", show_alert=True)

    async def request_txid(self, callback: types.CallbackQuery, payment_id: str):
        """Request TXID from user"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            await Payment.objects.aget(
                payment_id=payment_id,
                user=user,
                brand=self.brand,
                payment_method=Payment.PaymentMethod.CRYPTOCURRENCY,
                status=Payment.PaymentStatus.PENDING,
                expires_at__gt=timezone.now(),
            )
        except Payment.DoesNotExist:
            await callback.answer("❌ پرداخت منقضی یا نامعتبر است.", show_alert=True)
            return

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
        """Validate a crypto reference and route it to an admin for review."""
        txid = (message.text or "").strip()
        payment_id = state.state_data.get("payment_id") if state.state_data else None
        if not payment_id or not txid or len(txid) > 255 or any(ch.isspace() for ch in txid):
            await message.reply("❌ شناسهٔ تراکنش نامعتبر است. فقط TXID معتبر را ارسال کنید.")
            return

        try:
            payment = await Payment.objects.aget(
                payment_id=payment_id,
                user=user,
                brand=self.brand,
                payment_method=Payment.PaymentMethod.CRYPTOCURRENCY,
                status=Payment.PaymentStatus.PENDING,
                expires_at__gt=timezone.now(),
            )
        except Payment.DoesNotExist:
            await message.reply("❌ پرداخت پیدا نشد یا منقضی شده است.")
            return

        payment.crypto_txid = txid
        payment.status = Payment.PaymentStatus.AWAITING_CONFIRMATION
        await payment.asave(update_fields=["crypto_txid", "status", "updated_at"])
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        notified = await self._notify_admin_crypto_payment(payment)
        symbol = self._get_currency_symbol(payment.currency)
        status_text = (
            "در انتظار بررسی ادمین"
            if notified
            else "اعلان به ادمین نرسید؛ لطفاً با پشتیبانی تماس بگیرید"
        )
        text = f"""
✅ <b>TXID دریافت شد</b>
━━━━━━━━━━━━━━━━━━━━━━

🆔 TXID: <code>{txid[:30]}...</code>
💰 مبلغ: <code>{payment.amount:,.2f}</code> {symbol}
₿ رمزارز: {payment.crypto_currency}

⏳ <b>وضعیت:</b> {status_text}
        """
        keyboard = self.create_keyboard(
            [
                [{"text": "💰 مشاهده کیف پول", "callback_data": "wallet"}],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )
        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    async def _notify_admin_crypto_payment(self, payment: Payment) -> bool:
        keyboard = self.create_keyboard(
            [[
                {"text": "✅ تأیید", "callback_data": f"admin_confirm_wallet_{payment.pk}"},
                {"text": "❌ رد", "callback_data": f"admin_reject_wallet_{payment.pk}"},
            ]]
        )
        text = (
            "₿ رسید رمزارز برای بررسی\n"
            f"مبلغ: {self.format_price(payment.amount, payment.currency)}\n"
            f"ارز: {payment.crypto_currency}\n"
            f"TXID: <code>{payment.crypto_txid}</code>\n"
            f"شناسه پرداخت: {payment.pk}"
        )
        notified = 0
        async for admin in self.get_brand_admin_recipients():
            try:
                await self.bot.send_message(
                    chat_id=admin.telegram_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                notified += 1
            except Exception:
                logger.exception(
                    "Failed to notify admin %s about crypto payment %s",
                    admin.pk,
                    payment.pk,
                )
        return notified > 0

    # ==================== Telegram Stars Payment Methods ====================

    async def show_stars_payment(self, callback: types.CallbackQuery, amount: float):
        """Show a quoted Stars invoice for USD wallets only."""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        if wallet.currency != "USD":
            await callback.answer("پرداخت با ستاره فقط برای کیف پول دلاری فعال است.", show_alert=True)
            return
        if not wallet.is_active or wallet.is_frozen:
            await callback.answer("❌ کیف پول شما در دسترس نیست.", show_alert=True)
            return
        try:
            amount = self._validated_charge_amount(amount, wallet.currency)
        except WalletOperationError:
            await callback.answer("❌ مبلغ شارژ معتبر نیست.", show_alert=True)
            return
        stars_amount = int(amount * 100)
        symbol = self._get_currency_symbol(wallet.currency)
        text = f"""
⭐ <b>پرداخت با ستاره‌های تلگرام</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 مبلغ شارژ: <code>{amount:,.2f}</code> {symbol}
⭐ معادل: <code>{stars_amount:,}</code> ستاره

نرخ شارژ این برند: ۱ ستاره = ۰٫۰۱ دلار.
بعد از پرداخت، اعتبار دقیق همین فاکتور به کیف پول واریز می‌شود.
        """
        keyboard = self.create_keyboard(
            [
                [{"text": f"⭐ پرداخت {stars_amount:,} ستاره", "callback_data": f"wallet_stars_pay_{stars_amount}_{int(amount * 100)}"}],
                [{"text": "🔙 بازگشت", "callback_data": f"charge_amount_{int(amount)}"}],
            ]
        )
        await self._safe_edit_message(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def process_stars_payment(
        self, callback: types.CallbackQuery, stars_amount: int, amount_cents: int
    ):
        """Create a stored, expiring Stars quote before sending its invoice."""
        user, _ = await self.get_or_create_user(callback.from_user)
        wallet = await self.get_or_create_wallet(user)
        amount = Decimal(str(amount_cents)) / Decimal("100")
        if wallet.currency != "USD" or not wallet.is_active or wallet.is_frozen:
            await callback.answer("❌ پرداخت با ستاره برای این کیف پول در دسترس نیست.", show_alert=True)
            return
        try:
            amount = self._validated_charge_amount(amount, wallet.currency)
        except WalletOperationError:
            await callback.answer("❌ مبلغ شارژ معتبر نیست.", show_alert=True)
            return
        expected_stars = int(amount * 100)
        if stars_amount != expected_stars:
            await callback.answer("❌ مبلغ فاکتور تغییر کرده؛ دوباره تلاش کنید.", show_alert=True)
            return

        payment = await Payment.objects.acreate(
            brand=self.brand,
            user=user,
            wallet=wallet,
            payment_method=Payment.PaymentMethod.TELEGRAM_STARS,
            status=Payment.PaymentStatus.PENDING,
            amount=amount,
            currency=wallet.currency,
            stars_amount=stars_amount,
            notes="Wallet top-up via Telegram Stars",
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        symbol = self._get_currency_symbol(wallet.currency)
        await self.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"شارژ کیف پول - {self.brand.name}",
            description=f"شارژ {amount:,.2f} {symbol} به کیف پول شما",
            payload=f"wallet_charge_{payment.payment_id.hex}",
            currency="XTR",
            prices=[LabeledPrice(label=f"شارژ {amount:,.2f} {symbol}", amount=stars_amount)],
            provider_token="",
        )
        await callback.answer()

    async def handle_pre_checkout_query(self, pre_checkout_query: PreCheckoutQuery):
        """Reject invoices unless the database has the exact live quote."""
        try:
            prefix = "wallet_charge_"
            payment_id = pre_checkout_query.invoice_payload.removeprefix(prefix)
            if not pre_checkout_query.invoice_payload.startswith(prefix):
                raise WalletOperationError("Invalid invoice payload")
            valid = await sync_to_async(
                validate_stars_pre_checkout, thread_sensitive=True
            )(
                payment_id=payment_id,
                telegram_user_id=pre_checkout_query.from_user.id,
                paid_stars=pre_checkout_query.total_amount,
                currency=pre_checkout_query.currency,
            )
            if not valid:
                raise WalletOperationError("Stars invoice did not match the payment")
            await pre_checkout_query.answer(ok=True)
        except Exception as exc:
            logger.warning("Rejected Telegram Stars pre-checkout (%s)", type(exc).__name__)
            await pre_checkout_query.answer(
                ok=False,
                error_message="فاکتور نامعتبر یا منقضی است. دوباره از کیف پول پرداخت را آغاز کنید.",
            )

    async def handle_successful_payment(self, message: types.Message, user):
        """Confirm the matching invoice; the payment signal credits the wallet once."""
        successful_payment = message.successful_payment
        if not successful_payment:
            return
        prefix = "wallet_charge_"
        payload = successful_payment.invoice_payload or ""
        if not payload.startswith(prefix):
            await message.reply("❌ فاکتور پرداخت شناسایی نشد؛ با پشتیبانی تماس بگیرید.")
            return
        payment_id = payload.removeprefix(prefix)
        try:
            payment = await sync_to_async(
                confirm_stars_payment, thread_sensitive=True
            )(
                payment_id=payment_id,
                user_id=user.pk,
                charge_id=successful_payment.telegram_payment_charge_id,
                paid_stars=successful_payment.total_amount,
            )
        except WalletOperationError as exc:
            logger.error("Stars payment confirmation failed (%s)", type(exc).__name__)
            await message.reply("❌ پرداخت با فاکتور شما تطبیق نداشت؛ با پشتیبانی تماس بگیرید.")
            return

        wallet = await self.get_or_create_wallet(user)
        await wallet.arefresh_from_db()
        symbol = self._get_currency_symbol(wallet.currency)
        text = f"""
✅ <b>پرداخت موفق!</b>

⭐ ستاره‌های پرداخت شده: <code>{successful_payment.total_amount:,}</code>
💰 مبلغ شارژ: <code>{payment.amount:,.2f}</code> {symbol}
💵 موجودی جدید کیف پول: <code>{wallet.balance:,.2f}</code> {symbol}
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
🎁 <b>اعتبار کد هدیه</b>
━━━━━━━━━━━━━━━━━━━━━━

کد هدیهٔ دارای مبلغ ثابت را وارد کنید تا اعتبار آن به کیف پول افزوده شود.

💡 <b>نکات:</b>
• کدهای درصدی و کدهای مخصوص پلن باید هنگام خرید اشتراک استفاده شوند
• تعداد استفاده و سقف هر کد در زمان ثبت کنترل می‌شود

📌 <b>مثال:</b> <code>WALLET10</code>
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
        """Apply a fixed-value wallet coupon atomically."""
        code = message.text.strip().upper()
        try:
            coupon, _usage, _transaction = await sync_to_async(
                redeem_wallet_coupon, thread_sensitive=True
            )(
                user_id=user.pk,
                brand_id=self.brand.pk,
                code=code,
            )
        except WalletCouponError as exc:
            messages = {
                "Coupon was not found or is inactive": "❌ کد تخفیف پیدا نشد یا غیرفعال است.",
                "Coupon is outside its valid period": "❌ این کد در بازهٔ اعتبارش نیست.",
                "Coupon usage limit has been reached": "❌ ظرفیت استفاده از این کد تکمیل شده است.",
                "Coupon has already reached this user's limit": "❌ سقف استفادهٔ شما از این کد تکمیل شده است.",
                "Coupon is only available to new users": "❌ این کد فقط برای کاربران جدید است.",
                "This coupon must be applied to a subscription order": "❌ این کد باید هنگام خرید اشتراک استفاده شود.",
                "Coupon has no wallet value": "❌ این کد اعتبار کیف پول ندارد.",
                "Wallet was not found": "❌ کیف پول پیدا نشد.",
                "Wallet currency does not match the brand": "❌ ارز کیف پول با برند هماهنگ نیست؛ با پشتیبانی تماس بگیرید.",
                "Wallet is unavailable": "❌ کیف پول شما غیرفعال یا مسدود است.",
            }
            await message.reply(messages.get(str(exc), "❌ کد تخفیف قابل استفاده نیست."))
            return

        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        wallet = await self.get_or_create_wallet(user)
        await wallet.arefresh_from_db()
        symbol = self._get_currency_symbol(wallet.currency)

        text = f"""
🎉 <b>کد تخفیف با موفقیت فعال شد!</b>
━━━━━━━━━━━━━━━━━━━━━━

🎁 کد تخفیف: <code>{code}</code>
📝 نام: {coupon.name}
💰 مبلغ جایزه: <code>{_transaction.amount:,.2f}</code> {symbol}

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
        idempotency_key: str = None,
    ) -> Optional[WalletTransaction]:
        """Public method to credit wallet - used by other handlers"""
        wallet = await self.get_or_create_wallet(user)

        if wallet.is_frozen:
            logger.warning(f"Cannot credit frozen wallet for user {user.id}")
            return None

        return await self._credit_wallet(
            wallet,
            amount,
            transaction_type,
            description,
            reference_id,
            metadata,
            idempotency_key,
        )

    async def debit_wallet(
        self,
        user,
        amount: Decimal,
        transaction_type: str,
        description: str,
        reference_id: str = None,
        metadata: dict = None,
        idempotency_key: str = None,
    ) -> Optional[WalletTransaction]:
        """Public method to debit wallet - used by purchase handler"""
        wallet = await self.get_or_create_wallet(user)

        if wallet.is_frozen:
            logger.warning(f"Cannot debit frozen wallet for user {user.id}")
            return None

        return await self._debit_wallet(
            wallet,
            amount,
            transaction_type,
            description,
            reference_id,
            metadata,
            idempotency_key,
        )

    async def check_wallet_balance(self, user) -> Decimal:
        """Check user wallet balance"""
        wallet = await self.get_or_create_wallet(user)
        return wallet.balance

    async def can_afford(self, user, amount: Decimal) -> bool:
        """Check if user can afford the amount"""
        wallet = await self.get_or_create_wallet(user)
        try:
            amount = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return (
            amount.is_finite()
            and amount > 0
            and wallet.balance >= amount
            and wallet.is_active
            and not wallet.is_frozen
        )

    # ==================== Internal Helper Methods ====================

    async def _credit_wallet(
        self,
        wallet: Wallet,
        amount: Decimal,
        transaction_type: str,
        description: str,
        reference_id: str = None,
        metadata: dict = None,
        idempotency_key: str = None,
    ) -> WalletTransaction:
        """Credit amount to wallet and create transaction record"""
        try:
            return await sync_to_async(apply_wallet_credit, thread_sensitive=True)(
                wallet_id=wallet.pk,
                amount=amount,
                transaction_type=transaction_type,
                description=description,
                reference_id=reference_id,
                metadata=metadata,
                idempotency_key=idempotency_key,
            )
        except WalletOperationError as exc:
            logger.warning("Wallet credit rejected for wallet %s: %s", wallet.pk, exc)
            raise

    async def _debit_wallet(
        self,
        wallet: Wallet,
        amount: Decimal,
        transaction_type: str,
        description: str,
        reference_id: str = None,
        metadata: dict = None,
        idempotency_key: str = None,
    ) -> Optional[WalletTransaction]:
        """Debit amount from wallet and create transaction record"""
        try:
            return await sync_to_async(apply_wallet_debit, thread_sensitive=True)(
                wallet_id=wallet.pk,
                amount=amount,
                transaction_type=transaction_type,
                description=description,
                reference_id=reference_id,
                metadata=metadata,
                idempotency_key=idempotency_key,
            )
        except WalletOperationError as exc:
            logger.warning("Wallet debit rejected for wallet %s: %s", wallet.pk, exc)
            return None

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
            "T": "تومان",
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
        if currency in ["T", "IRR", "IRT"]:
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
        amounts = self.PRESET_AMOUNTS.get(currency)
        if not amounts:
            await self.send_message_with_keyboard(
                chat_id,
                "شارژ کیف پول برای این ارز هنوز پیکربندی نشده است.",
                self.get_back_keyboard("wallet"),
            )
            return
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
