"""
Wallet Handler for Multi-Tenant VPN Bot
Handles wallet operations, balance, and transactions
"""

import logging

from aiogram import types

from apps.bot.models import BotState
from apps.orders.models import Wallet, WalletTransaction

from .base import BaseHandler

logger = logging.getLogger(__name__)


class WalletHandler(BaseHandler):
    """Handle wallet and financial operations"""

    async def show_wallet(self, callback: types.CallbackQuery):
        """Show wallet overview"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
        except Wallet.DoesNotExist:
            wallet = await Wallet.objects.acreate(
                user=user,
                brand=self.brand,
                balance=0,
                currency=self.brand.currency,
                is_active=True,
            )

        transactions = []
        async for trans in WalletTransaction.objects.filter(wallet=wallet).order_by(
            "-created_at"
        ):
            transactions.append(trans)
            if len(transactions) >= 5:
                break

        text = f"""
💰 کیف پول من

💵 <b>موجودی فعلی:</b>
{self.format_price(wallet.balance, wallet.currency)}

وضعیت: {"🟢 فعال" if wallet.is_active else "🔴 غیرفعال"}

📊 اطلاعات:
• ارز: {wallet.currency}
• تاریخ ایجاد: {wallet.created_at.strftime("%Y/%m/%d") if wallet.created_at else "نامشخص"}
        """

        if transactions:
            text += "\n\n📝 <b>آخرین تراکنش‌ها:</b>\n"
            for trans in transactions:
                icon = "➕" if trans.transaction_type == "credit" else "➖"
                amount_str = self.format_price(trans.amount, wallet.currency)
                text += f"\n{icon} {amount_str} - {trans.created_at.strftime('%Y/%m/%d %H:%M')}"

        keyboard = self.create_keyboard(
            [
                [{"text": "🔄 شارژ کیف پول", "callback_data": "charge_wallet"}],
                [{"text": "📋 تاریخچه تراکنش‌ها", "callback_data": "wallet_history"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit wallet message: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_wallet_history(self, callback: types.CallbackQuery):
        """Show complete wallet transaction history"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
        except Wallet.DoesNotExist:
            await callback.answer("❌ کیف پول یافت نشد")
            return

        transactions = []
        async for trans in WalletTransaction.objects.filter(wallet=wallet).order_by(
            "-created_at"
        ):
            transactions.append(trans)
            if len(transactions) >= 20:
                break

        if not transactions:
            text = """
📋 تاریخچه تراکنش‌ها

هیچ تراکنشی وجود ندارد.
            """
            keyboard = self.get_back_keyboard("wallet")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )
            await callback.answer()
            return

        text = """
📋 تاریخچه تراکنش‌های کیف پول

"""

        total_credit = sum(
            t.amount
            for t in transactions
            if t.transaction_type == WalletTransaction.TransactionType.CREDIT
        )
        total_debit = sum(
            t.amount
            for t in transactions
            if t.transaction_type == WalletTransaction.TransactionType.DEBIT
        )

        text += f"""
📊 <b>خلاصه:</b>
• درآمد کل: {self.format_price(total_credit, wallet.currency)}
• خرج کل: {self.format_price(total_debit, wallet.currency)}
• موجودی فعلی: {self.format_price(wallet.balance, wallet.currency)}

<b>تراکنش‌های اخیر:</b>
"""

        for trans in transactions:
            icon = "✅ " if trans.transaction_type == "credit" else "💸 "
            amount_str = self.format_price(trans.amount, wallet.currency)
            date_str = trans.created_at.strftime("%Y/%m/%d %H:%M")

            description = trans.description or "تراکنش"

            text += f"\n{icon}<code>{amount_str:>12}</code> - {description}\n   {date_str}\n"

        keyboard = self.create_keyboard(
            [
                [{"text": "🔄 شارژ کیف پول", "callback_data": "charge_wallet"}],
                [{"text": "🔙 بازگشت", "callback_data": "wallet"}],
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

    async def show_charge_options(self, callback: types.CallbackQuery):
        """Show wallet charge options"""
        text = """
🔄 شارژ کیف پول

میزان شارژ مورد نظر را انتخاب کنید:
        """

        amounts = [10, 20, 50, 100, 500, 1000]
        currency_symbol = (
            "$"
            if self.brand.currency == "USD"
            else "تومان"
            if self.brand.currency == "IRR"
            else self.brand.currency
        )

        button_rows = []
        for amount in amounts:
            button_rows.append(
                [
                    {
                        "text": f"{amount} {currency_symbol}",
                        "callback_data": f"charge_amount_{amount}",
                    }
                ]
            )

        button_rows.append([{"text": "🔙 بازگشت", "callback_data": "wallet"}])
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

    async def initiate_charge(self, callback: types.CallbackQuery, amount: int):
        """Initiate wallet charge process"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
        except Wallet.DoesNotExist:
            await callback.answer("❌ کیف پول یافت نشد")
            return

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"action": "wallet_charge", "amount": amount},
        )

        text = f"""
🔄 شارژ کیف پول

مبلغ: {self.format_price(amount, wallet.currency)}
ارز: {wallet.currency}

لطفاً روش پرداخت را انتخاب کنید:
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "💳 پرداخت با کارت",
                        "callback_data": f"wallet_pay_card_{amount}",
                    }
                ],
                [
                    {
                        "text": "₿ پرداخت با رمزارز",
                        "callback_data": f"wallet_pay_crypto_{amount}",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "wallet"}],
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

    async def show_wallet_settings(self, callback: types.CallbackQuery):
        """Show wallet settings"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
        except Wallet.DoesNotExist:
            await callback.answer("❌ کیف پول یافت نشد")
            return

        text = f"""
⚙️ تنظیمات کیف پول

موجودی: {self.format_price(wallet.balance, wallet.currency)}
وضعیت: {"🟢 فعال" if wallet.is_active else "🔴 غیرفعال"}

تنظیمات:
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "🔐 تاریخچه تراکنش‌ها", "callback_data": "wallet_history"}],
                [
                    {
                        "text": "🚫 مسدود کردن کیف پول",
                        "callback_data": "wallet_block",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "wallet"}],
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
