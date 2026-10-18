"""Regression coverage for Telegram's renewal inline-keyboard size limit."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.handlers.subscriptions import SubscriptionHandler
from apps.brands.models import Brand
from apps.orders.models import Order
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.vpn_providers.models import VPNProvider


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RenewalPaginationTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Renewal Test", slug="renewal-test", currency="T")
        self.user = User.objects.create_user(username="renew-user", telegram_id=4567, brand=self.brand)
        self.provider = VPNProvider.objects.create(
            brand=self.brand, name="Provider", provider_type="connectix",
            base_url="https://example.invalid", is_default=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, vpn_provider=self.provider, name="Current Plan",
            plan_type="hybrid", service_category="normal", price=Decimal("120000"),
            currency="T", duration_value=1, duration_unit="months",
            traffic_limit_gb=Decimal("20"), max_users=1,
        )
        self.order = Order.objects.create(
            brand=self.brand, user=self.user, plan=self.plan,
            original_price=self.plan.price, final_price=self.plan.price,
            currency="T",
        )
        self.subscription = Subscription.objects.create(
            brand=self.brand, user=self.user, owner=self.user,
            order=self.order, plan=self.plan, vpn_provider=self.provider,
            status="active", starts_at=timezone.now(),
            traffic_limit_gb=Decimal("20"), connectix_username="renewal-test",
        )
        self.bot = MagicMock()
        self.bot.edit_message_text = AsyncMock()
        self.handler = SubscriptionHandler(self.bot, self.brand)
        self.handler.get_or_create_user = AsyncMock(return_value=(self.user, False))
        self.handler.purchase_credit = AsyncMock(return_value=Decimal("0"))
        self.callback = MagicMock()
        self.callback.message.chat.id = self.user.telegram_id
        self.callback.message.message_id = 200
        self.callback.answer = AsyncMock()
        # Detached catalogue records are sufficient: checkout validates every
        # chosen plan against the database before creating an order.
        self.catalog = [self.plan] + [
            SubscriptionPlan(
                pk=1000 + i, brand=self.brand, name=f"Plan {i}",
                plan_type="hybrid", service_category="normal",
                price=Decimal("100000") + i, currency="T",
                duration_value=1, duration_unit="months",
                traffic_limit_gb=Decimal("30"), max_users=1,
            )
            for i in range(142)
        ]

    def render(self, page=1):
        with patch("apps.bot.handlers.subscriptions.get_cached_plans", new_callable=AsyncMock, return_value=list(self.catalog)):
            async_to_sync(self.handler.show_renewal_details)(self.callback, self.subscription.pk, page=page)
        kwargs = self.bot.edit_message_text.call_args.kwargs
        rows = kwargs["reply_markup"].inline_keyboard
        return kwargs["text"], [button for row in rows for button in row]

    def test_large_catalog_is_paginated_and_current_plan_first(self):
        text, buttons = self.render()
        self.assertIn("صفحه 1 از 18", text)
        self.assertLessEqual(len(buttons), 11)  # 8 plans, next, details, back
        plan_buttons = [b for b in buttons if b.callback_data.startswith(("repurchase_subscription_", "renew_with_plan_"))]
        self.assertEqual(len(plan_buttons), 8)
        self.assertEqual(plan_buttons[0].callback_data, f"repurchase_subscription_{self.subscription.pk}")
        self.assertTrue(any(b.callback_data == f"renewal_plans_page_{self.subscription.pk}_2" for b in buttons))
        self.assertTrue(all(len(b.callback_data.encode("utf-8")) <= 64 for b in buttons))

    def test_navigation_reaches_every_plan_and_clamps_out_of_range_page(self):
        seen = set()
        for page in range(1, 19):
            text, buttons = self.render(page)
            self.assertIn(f"صفحه {page} از 18", text)
            self.assertLessEqual(len(buttons), 12)
            for button in buttons:
                data = button.callback_data
                if data.startswith("renew_with_plan_"):
                    seen.add(int(data.rsplit("_", 1)[1]))
        self.assertEqual(seen, {1000 + i for i in range(142)})
        text, _ = self.render(100_000)
        self.assertIn("صفحه 18 از 18", text)
        text, _ = self.render(-1)
        self.assertIn("صفحه 1 از 18", text)

    def test_cannot_show_another_users_subscription(self):
        another = User.objects.create_user(username="another", telegram_id=9999, brand=self.brand)
        self.handler.get_or_create_user = AsyncMock(return_value=(another, False))
        async_to_sync(self.handler.show_renewal_details)(self.callback, self.subscription.pk)
        self.callback.answer.assert_awaited_once_with("❌ اشتراک یافت نشد.", show_alert=True)
        self.bot.edit_message_text.assert_not_awaited()
