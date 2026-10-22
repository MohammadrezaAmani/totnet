"""Critical staged-rollout regressions: credit, coupon, catalog policy, dates."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User, UserProfile
from apps.bot.handlers.subscriptions import SubscriptionHandler
from apps.brands.models import Brand
from apps.orders.coupons import PurchaseCouponError, apply_purchase_coupon
from apps.orders.models import Coupon, CouponUsage, Order, Wallet, WalletTransaction
from apps.referrals.models import ServiceRewardRate
from apps.referrals.services import grant_profile_completion_reward
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.subscriptions.policy import effective_reward_percent
from apps.vpn_providers.models import VPNProvider
from utils.jalali import jalali_date


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class CheckoutAndProfileRegressionTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Rollout", slug="rollout-regression-20261022", currency="T", contact_email="regression@example.invalid"
        )
        self.user = User.objects.create_user(username="rollout-user", telegram_id=23411, brand=self.brand)
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Royal unlimited 1 month", price=Decimal("400000"), currency="T",
            plan_type="unlimited", service_category="royal", duration_value=1, duration_unit="months"
        )

    def _order(self):
        return Order.objects.create(
            brand=self.brand, user=self.user, plan=self.plan, currency="T",
            original_price=self.plan.price, final_price=self.plan.price
        )

    def test_royal_unlimited_reward_is_always_four_percent(self):
        ServiceRewardRate.objects.create(
            brand=self.brand, service_category="royal", plan_type="unlimited", percent=Decimal("9")
        )
        order = self._order()
        self.assertEqual(effective_reward_percent(order, Decimal("13")), Decimal("4"))
        self.plan.service_category = "normal"
        self.plan.save(update_fields=["service_category"])
        order.plan = self.plan
        self.assertEqual(effective_reward_percent(order, Decimal("13")), Decimal("13"))
        ServiceRewardRate.objects.create(
            brand=self.brand, service_category="normal", plan_type="unlimited", percent=Decimal("6")
        )
        self.assertEqual(effective_reward_percent(order, Decimal("13")), Decimal("6"))

    def test_profile_cash_is_exactly_ten_thousand_once(self):
        self.user.full_name = "Tester"
        self.user.phone_number = "09120000000"
        self.user.birth_date = date(1990, 2, 10)
        self.user.save()
        profile = UserProfile.objects.create(user=self.user, living_location="تهران", gender="undisclosed")
        self.assertTrue(grant_profile_completion_reward(user_id=self.user.pk, brand_id=self.brand.pk))
        self.assertFalse(grant_profile_completion_reward(user_id=self.user.pk, brand_id=self.brand.pk))
        profile.refresh_from_db()
        self.assertIsNotNone(profile.profile_reward_granted_at)
        wallet = Wallet.objects.get(user=self.user, brand=self.brand)
        self.assertEqual(wallet.balance, Decimal("10000"))
        self.assertEqual(WalletTransaction.objects.filter(wallet=wallet, idempotency_key__startswith="profile-completion-cash:").count(), 1)

    def test_percent_and_fixed_checkout_coupons_are_integer_toman_and_once_only(self):
        now = timezone.now()
        coupon = Coupon.objects.create(
            brand=self.brand, code="SAVE12", name="Discount", coupon_type=Coupon.CouponType.PERCENTAGE,
            discount_value=Decimal("12"), valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1)
        )
        order = self._order()
        updated, amount = apply_purchase_coupon(
            brand_id=self.brand.pk, user_id=self.user.pk, order_id=str(order.order_id), code=coupon.code
        )
        self.assertEqual(amount, Decimal("48000"))
        self.assertEqual(updated.final_price, Decimal("352000"))
        with self.assertRaises(PurchaseCouponError):
            apply_purchase_coupon(
                brand_id=self.brand.pk, user_id=self.user.pk, order_id=str(order.order_id), code=coupon.code
            )
        self.assertEqual(CouponUsage.objects.filter(order=order).count(), 1)
        fixed = Coupon.objects.create(
            brand=self.brand, code="FIXED10", name="Fixed", coupon_type=Coupon.CouponType.FIXED_AMOUNT,
            discount_value=Decimal("10001"), valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1)
        )
        another = self._order()
        updated, amount = apply_purchase_coupon(
            brand_id=self.brand.pk, user_id=self.user.pk, order_id=str(another.order_id), code=fixed.code
        )
        self.assertEqual(amount, Decimal("10001"))
        self.assertEqual(updated.final_price, Decimal("389999"))

    def test_cannot_redeem_another_users_order(self):
        now = timezone.now()
        Coupon.objects.create(
            brand=self.brand, code="FOREIGN", name="Foreign", coupon_type=Coupon.CouponType.FIXED_AMOUNT,
            discount_value=Decimal("5000"), valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1)
        )
        order = self._order()
        stranger = User.objects.create_user(username="other-rollout-user", brand=self.brand, telegram_id=8819)
        with self.assertRaises(PurchaseCouponError):
            apply_purchase_coupon(
                brand_id=self.brand.pk, user_id=stranger.pk, order_id=str(order.order_id), code="FOREIGN"
            )

    def test_jalali_date_format_for_customer_views(self):
        self.assertEqual(jalali_date(date(2026, 3, 21)), "1405/01/01")


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RenewalKeyboardRegressionTests(TestCase):
    def test_renewal_large_catalog_is_paginated(self):
        brand = Brand.objects.create(name="Renewal", slug="renewal-rollout-20261022", currency="T", bot_token="renewal-test-token", contact_email="renewal@example.invalid")
        user = User.objects.create_user(username="renew-rollout", telegram_id=99887, brand=brand)
        provider = VPNProvider.objects.create(
            brand=brand, name="Provider", provider_type="connectix", base_url="https://example.invalid", is_default=True,
        )
        plan = SubscriptionPlan.objects.create(
            brand=brand, vpn_provider=provider, name="Current", plan_type="hybrid",
            currency="T", price=Decimal("100000"), duration_value=1, duration_unit="months",
            traffic_limit_gb=Decimal("40")
        )
        order = Order.objects.create(brand=brand, user=user, plan=plan, original_price=plan.price, final_price=plan.price, currency="T")
        sub = Subscription.objects.create(
            brand=brand, user=user, owner=user, order=order, vpn_provider=provider, plan=plan,
            status="active", starts_at=timezone.now(), traffic_limit_gb=Decimal("40"), connectix_username="customer"
        )
        catalog = [plan] + [SubscriptionPlan(
            pk=20000+i, brand=brand, name=f"Plan {i}", plan_type="hybrid", price=Decimal("101000")+i,
            currency="T", duration_value=1, duration_unit="months", traffic_limit_gb=Decimal("60"), max_users=1
        ) for i in range(142)]
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        handler = SubscriptionHandler(bot, brand)
        handler.get_or_create_user = AsyncMock(return_value=(user, False))
        handler.purchase_credit = AsyncMock(return_value=Decimal("0"))
        callback = MagicMock()
        callback.message.chat.id = user.telegram_id
        callback.message.message_id = 31
        callback.answer = AsyncMock()
        with patch("apps.bot.handlers.subscriptions.get_cached_plans", new_callable=AsyncMock, return_value=catalog):
            async_to_sync(handler.show_renewal_details)(callback, sub.pk, page=1)
            kwargs = bot.edit_message_text.call_args.kwargs
            buttons = [button for row in kwargs["reply_markup"].inline_keyboard for button in row]
            self.assertLessEqual(len(buttons), 13)
            self.assertIn("صفحه 1 از 18", kwargs["text"])
            self.assertEqual(len([b for b in buttons if b.callback_data.startswith("renew_with_plan_") or b.callback_data.startswith("repurchase_subscription_")]), 8)
            async_to_sync(handler.show_renewal_details)(callback, sub.pk, page=18)
            kwargs = bot.edit_message_text.call_args.kwargs
            self.assertIn("صفحه 18 از 18", kwargs["text"])
