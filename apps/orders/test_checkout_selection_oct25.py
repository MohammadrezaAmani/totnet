"""Cash-optional purchases and configurable card choice regressions."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.accounts.models import User
from apps.brands.models import Brand, BrandPaymentMethod
from apps.bot.handlers.purchase import PurchaseHandler
from apps.orders.models import Order, Payment, PaymentCard, Wallet, WalletTransaction
from apps.orders.services import (
    WalletCheckoutError, available_purchase_credit, pay_order_with_wallet,
)
from apps.subscriptions.models import SubscriptionPlan


class CheckoutSelectionTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Card choice regression", slug="card-choice-regression", currency="T",
            bot_token="card-choice-test-token", contact_email="choice@example.invalid",
        )
        self.user = User.objects.create_user(
            username="card-choice-customer", telegram_id=998877, brand=self.brand,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Normal 30", price=Decimal("180000"),
            currency="T", service_category="normal", plan_type="hybrid",
            duration_unit="months", duration_value=1,
        )
        self.wallet = Wallet.objects.create(
            brand=self.brand, user=self.user, balance=Decimal("84800"), currency="T",
        )
        self.bot = MagicMock()
        self.handler = PurchaseHandler(self.bot, self.brand)
        self.handler.get_or_create_user = AsyncMock(return_value=(self.user, False))
        self.handler.edit_message_with_keyboard = AsyncMock()
        self.callback = MagicMock()
        self.callback.answer = AsyncMock()
        self.callback.message.chat.id = self.user.telegram_id
        self.callback.message.message_id = 111
        self.handler.create_keyboard = lambda rows: rows

    def make_order(self, *, use_wallet_credit=True):
        return Order.objects.create(
            brand=self.brand, user=self.user, plan=self.plan, currency="T",
            original_price=self.plan.price, final_price=self.plan.price,
            use_wallet_credit=use_wallet_credit,
        )

    def test_disabled_wallet_credit_cannot_be_used_via_old_callback(self):
        order = self.make_order(use_wallet_credit=False)
        with self.assertRaisesMessage(WalletCheckoutError, "Wallet credit is disabled"):
            pay_order_with_wallet(
                order_id=str(order.order_id), user_id=self.user.pk, brand_id=self.brand.pk,
                allow_partial=True,
            )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("84800"))
        self.assertFalse(WalletTransaction.objects.filter(wallet=self.wallet).exists())
        self.assertFalse(Payment.objects.filter(order=order).exists())

    def test_round_down_cash_credit_keeps_subthousand_balance(self):
        self.assertEqual(available_purchase_credit(
            user_id=self.user.pk, brand_id=self.brand.pk, currency="T"
        ), Decimal("84000"))
        order = self.make_order()
        with patch("apps.orders.signals.broadcast_message"):
            result = pay_order_with_wallet(
                order_id=str(order.order_id), user_id=self.user.pk, brand_id=self.brand.pk,
                allow_partial=True,
            )
        self.assertEqual(result.amount_applied, Decimal("84000"))
        self.assertEqual(result.remaining_due, Decimal("96000"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("800"))

    def test_card_selection_respects_order_color_and_quote_is_delayed(self):
        BrandPaymentMethod.objects.create(
            brand=self.brand, name="کارت", payment_type="card_transfer",
            button_style="success", display_order=1,
        )
        card1 = PaymentCard.objects.create(
            brand=self.brand, card_number="1111222233334444", cardholder_name="A",
            bank_name="مسکن", display_order=2, card_color="#b92828",
        )
        card2 = PaymentCard.objects.create(
            brand=self.brand, card_number="5555666677778888", cardholder_name="B",
            bank_name="ملت", display_order=1, card_color="#2ca94b",
        )
        order = self.make_order(use_wallet_credit=False)
        async_to_sync(self.handler.show_card_transfer_payment)(self.callback, str(order.order_id))
        markup = self.handler.edit_message_with_keyboard.await_args.args[3]
        self.assertEqual(markup[0][0]["callback_data"], f"select_card_{card2.pk}_{order.order_id}")
        self.assertEqual(markup[0][0]["style"], "success")
        self.assertEqual(markup[1][0]["callback_data"], f"select_card_{card1.pk}_{order.order_id}")
        self.assertEqual(markup[1][0]["style"], "danger")
        from apps.orders.models import CardTransferQuote
        self.assertFalse(CardTransferQuote.objects.filter(order=order).exists())
        async_to_sync(self.handler.show_selected_card)(self.callback, str(order.order_id), card2.pk)
        self.assertEqual(CardTransferQuote.objects.get(order=order).card_id, card2.pk)
        text = self.handler.edit_message_with_keyboard.await_args.args[2]
        self.assertIn("ملت", text)
        self.assertIn("8888", text)
        self.assertIn("180000", text)

    def test_plan_details_has_both_buy_modes(self):
        self.handler.get_user_state = AsyncMock(return_value=MagicMock(
            state_data={}, current_state="purchase_flow"
        ))
        self.handler.update_user_state = AsyncMock()
        with patch("apps.bot.handlers.purchase.is_plan_visible_in_bot", new_callable=AsyncMock, return_value=True):
            async_to_sync(self.handler.show_plan_details)(self.callback, self.plan.pk)
        buttons = self.handler.edit_message_with_keyboard.await_args.args[3]
        callbacks = [b["callback_data"] for row in buttons for b in row]
        self.assertIn(f"purchase_plan_{self.plan.pk}", callbacks)
        self.assertIn(f"purchase_nocredit_{self.plan.pk}", callbacks)
        self.assertIn(f"buy_for_other_{self.plan.pk}", callbacks)
        self.assertIn(f"buy_for_other_nocredit_{self.plan.pk}", callbacks)

    def test_gift_default_is_48_hours(self):
        self.assertEqual(self.brand.gift_expiry_hours, 48)
