"""Regression coverage for async checkout after coupon/order refresh."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.bot.handlers.purchase import PurchaseHandler
from apps.bot.models import BotState
from apps.brands.models import Brand
from apps.orders.models import Order
from apps.subscriptions.models import SubscriptionPlan


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class AsyncPurchaseCheckoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(
            name="Checkout Test", slug="async-purchase-checkout",
            bot_token="test-token", currency="T", contact_email="test@example.invalid",
        )
        self.user = User.objects.create_user(
            username="async-buyer", telegram_id=785410, brand=self.brand,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Normal 30GB", plan_type="hybrid",
            service_category="normal", price=Decimal("200000"),
            currency="T", duration_value=1, duration_unit="months",
            traffic_limit_gb=Decimal("30"), is_active=True, is_visible=True,
        )
        self.bot = MagicMock()
        self.handler = PurchaseHandler(self.bot, self.brand)
        self.handler.get_or_create_user = AsyncMock(return_value=(self.user, False))
        self.handler.edit_message_with_keyboard = AsyncMock()
        self.callback = MagicMock()
        self.callback.message.chat.id = 785410
        self.callback.message.message_id = 10
        self.callback.answer = AsyncMock()

    def test_regular_purchase_opens_payment_methods_in_async_callback(self):
        async_to_sync(self.handler.initiate_purchase)(self.callback, self.plan.pk)
        order = Order.objects.get(user=self.user)
        self.assertEqual(order.final_price, Decimal("200000"))
        self.assertEqual(
            BotState.objects.get(user=self.user).state_data["step"],
            "payment_method",
        )
        self.handler.edit_message_with_keyboard.assert_awaited_once()
        self.assertIn(
            "انتخاب روش پرداخت",
            self.handler.edit_message_with_keyboard.await_args.args[2],
        )

    def test_checkout_uses_updated_coupon_amount_after_async_reload(self):
        def apply_discount(*, brand_id, user_id, order_id):
            Order.objects.filter(
                order_id=order_id, brand_id=brand_id, user_id=user_id,
            ).update(final_price=Decimal("157000"), discount_amount=Decimal("43000"))

        with patch(
            "apps.orders.coupons.apply_first_purchase_referral_coupon",
            side_effect=apply_discount,
        ):
            async_to_sync(self.handler.initiate_purchase)(self.callback, self.plan.pk)
        order = Order.objects.get(user=self.user)
        self.assertEqual(order.final_price, Decimal("157000"))
        self.handler.edit_message_with_keyboard.assert_awaited_once()
        self.assertIn(
            "157000",
            self.handler.edit_message_with_keyboard.await_args.args[2],
        )
