"""Regressions for invitation handoff, welcome discount, lifecycle messages."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from asgiref.sync import async_to_sync
from aiogram import types
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.handlers.start import StartHandler
from apps.brands.models import Brand
from apps.orders.coupons import (
    PurchaseCouponError, apply_first_purchase_referral_coupon, apply_purchase_coupon,
)
from apps.orders.models import Coupon, CouponUsage, Order
from apps.referrals.models import (
    GamificationNotification, NotificationRule, NotificationTrigger,
    ReferralClick, ReferralLink,
)
from apps.referrals.scheduling import lifecycle_candidates, run_notification_rules
from apps.referrals.services import attribute_referral
from apps.subscriptions.models import SubscriptionPlan


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class CampaignRolloutTests(TestCase):
    def setUp(self):
        cache.clear()
        suffix = uuid.uuid4().hex[:8]
        self.brand = Brand.objects.create(
            name="Referral campaign", slug=f"campaign-{suffix}", bot_username="campaign_test_bot",
            bot_token="test-token", currency="T",
        )
        self.inviter = User.objects.create_user(
            username=f"inviter-{suffix}", brand=self.brand, telegram_id=80901,
        )
        self.recipient = User.objects.create_user(
            username=f"newuser-{suffix}", brand=self.brand, telegram_id=80902,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Campaign plan", currency="T", price=Decimal("400000"),
            duration_value=1, duration_unit=SubscriptionPlan.DurationUnit.MONTHS,
        )
        now = timezone.now()
        self.coupon = Coupon.objects.create(
            brand=self.brand, code=f"WELCOME{suffix}", name="First purchase",
            coupon_type=Coupon.CouponType.FIXED_AMOUNT,
            discount_value=Decimal("47000"),
            valid_from=now - timedelta(days=1), valid_until=now + timedelta(days=7),
            new_users_only=True, max_uses_per_user=1,
        )
        self.link = ReferralLink.objects.create(
            brand=self.brand, user=self.inviter, code=f"LINK{suffix}",
            welcome_coupon=self.coupon, welcome_coupon_valid_hours=48,
        )

    def _signup_visit(self):
        return ReferralClick.objects.create(
            brand=self.brand, visitor=self.recipient, link=self.link,
            is_registration=True,
        )

    def _order(self, user=None):
        return Order.objects.create(
            brand=self.brand, user=user or self.recipient, plan=self.plan,
            original_price=Decimal("400000"), final_price=Decimal("400000"),
            currency="T", status=Order.OrderStatus.PENDING,
        )

    def test_inviter_gets_join_notice_only_once_and_not_referee(self):
        # The idempotency entry is created after transaction commit, and never
        # rewrites the inviter when attribution is retried.
        with patch("apps.referrals.tasks._publish_notification") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(attribute_referral(
                    user_id=self.recipient.pk, brand_id=self.brand.pk, code=self.link.code,
                ))
            notification = GamificationNotification.objects.get(
                dedupe_key=f"referral-joined:{self.brand.pk}:{self.recipient.pk}"
            )
            self.assertEqual(notification.user_id, self.inviter.pk)
            self.assertIn("وارد ربات شد", notification.metadata["delivery_payload"]["text"])
            self.assertFalse(attribute_referral(
                user_id=self.recipient.pk, brand_id=self.brand.pk, code=self.link.code,
            ))
            self.assertEqual(GamificationNotification.objects.filter(
                dedupe_key=notification.dedupe_key
            ).count(), 1)
            publish.assert_called_once()

    def test_start_silently_records_signup_for_referee(self):
        bot = MagicMock()
        handler = StartHandler(bot, self.brand)
        sender = types.User(id=80903, is_bot=False, first_name="Tester")
        message = MagicMock()
        message.from_user = sender
        message.chat.id = 80903
        message.answer = AsyncMock()
        with patch.object(handler, "show_profile_setup", new_callable=AsyncMock) as setup:
            with self.captureOnCommitCallbacks(execute=True):
                async_to_sync(handler.handle_start_command)(
                    message, SimpleNamespace(args=self.link.code)
                )
            setup.assert_awaited_once()
        message.answer.assert_not_awaited()

    def test_welcome_coupon_auto_applies_once_for_valid_first_registration(self):
        self._signup_visit()
        order = self._order()
        result = apply_first_purchase_referral_coupon(
            brand_id=self.brand.pk, user_id=self.recipient.pk,
            order_id=str(order.order_id),
        )
        self.assertIsNotNone(result)
        order.refresh_from_db()
        self.assertEqual(order.final_price, Decimal("353000"))
        self.assertEqual(CouponUsage.objects.filter(order=order).count(), 1)
        self.assertIsNone(apply_first_purchase_referral_coupon(
            brand_id=self.brand.pk, user_id=self.recipient.pk,
            order_id=str(order.order_id),
        ))

    def test_referred_coupon_rejected_for_unrelated_user(self):
        stranger = User.objects.create_user(
            username=f"stranger-{uuid.uuid4().hex[:6]}", brand=self.brand, telegram_id=80904,
        )
        order = self._order(stranger)
        with self.assertRaises(PurchaseCouponError):
            apply_purchase_coupon(brand_id=self.brand.pk, user_id=stranger.pk,
                                  order_id=str(order.order_id), code=self.coupon.code)
        self.assertIsNone(apply_first_purchase_referral_coupon(
            brand_id=self.brand.pk, user_id=stranger.pk, order_id=str(order.order_id)
        ))
        self.assertFalse(CouponUsage.objects.filter(order=order).exists())

    def test_promo_expires_48_hours_after_join_regardless_of_coupon_expiry(self):
        visit = self._signup_visit()
        ReferralClick.objects.filter(pk=visit.pk).update(
            created_at=timezone.now() - timedelta(hours=49)
        )
        order = self._order()
        with self.assertRaises(PurchaseCouponError):
            apply_purchase_coupon(brand_id=self.brand.pk, user_id=self.recipient.pk,
                                  order_id=str(order.order_id), code=self.coupon.code)
        self.assertIsNone(apply_first_purchase_referral_coupon(
            brand_id=self.brand.pk, user_id=self.recipient.pk,
            order_id=str(order.order_id),
        ))

    def test_scheduled_join_notification_independent_of_purchase(self):
        visit = self._signup_visit()
        ReferralClick.objects.filter(pk=visit.pk).update(
            created_at=timezone.now() - timedelta(minutes=6)
        )
        notification_rule = NotificationRule.objects.create(
            brand=self.brand, name="Welcome gift", is_active=True,
            trigger=NotificationTrigger.REFERRAL_LINK_JOIN, delay_minutes=5,
            referral_link=self.link, audience="all", kind="custom",
            message="لینک شما شامل کد {coupon_code} تا {coupon_expiry} است.",
        )
        with patch("apps.referrals.tasks._queue_once", return_value=True) as queue:
            counters = run_notification_rules()
        self.assertEqual(counters[notification_rule.pk], 1)
        args = queue.call_args.kwargs
        self.assertEqual(args["user"].pk, self.recipient.pk)
        self.assertIn(self.coupon.code, args["text"])
        self.assertIn(f"rule:{notification_rule.pk}:{self.recipient.pk}", args["dedupe_key"])

    def test_first_active_subscription_trigger_requires_active_status(self):
        from apps.subscriptions.models import Subscription
        from apps.vpn_providers.models import VPNProvider
        provider = VPNProvider.objects.create(
            brand=self.brand, name="provider", provider_type=VPNProvider.ProviderType.CONNECTIX,
            base_url="https://api.example.invalid", api_key="demo", status=VPNProvider.ProviderStatus.ACTIVE,
        )
        sub = Subscription.objects.create(
            brand=self.brand, user=self.recipient, owner=self.recipient,
            plan=self.plan, order=self._order(), vpn_provider=provider,
            starts_at=timezone.now() - timedelta(days=12),
            status=Subscription.SubscriptionStatus.PENDING,
        )
        kwargs = dict(brand_id=self.brand.pk,
                      trigger=NotificationTrigger.FIRST_ACTIVE_SUBSCRIPTION,
                      delay_minutes=11 * 24 * 60)
        self.assertEqual(list(lifecycle_candidates(**kwargs)), [])
        Subscription.objects.filter(pk=sub.pk).update(status=Subscription.SubscriptionStatus.ACTIVE)
        candidates = list(lifecycle_candidates(**kwargs))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0].pk, self.recipient.pk)

    def test_gift_handover_queues_one_gift_and_one_followup_to_buyer(self):
        from apps.orders.models import Payment
        order = self._order()
        order.order_type = Order.OrderType.GIFT
        order.save(update_fields=["order_type"])
        with patch("apps.referrals.tasks._queue_once", return_value=True) as notices:
            with patch("apps.orders.signals._enqueue_referral_reward"):
                with self.captureOnCommitCallbacks(execute=True):
                    Payment.objects.create(
                        brand=self.brand, user=self.recipient, order=order,
                        payment_method=Payment.PaymentMethod.CARD_TRANSFER,
                        status=Payment.PaymentStatus.CONFIRMED,
                        amount=order.final_price, currency="T",
                    )
        messages = [call.kwargs for call in notices.call_args_list]
        self.assertEqual(len(messages), 2)
        self.assertEqual({m["user"].pk for m in messages}, {self.recipient.pk})
        self.assertTrue(all("?start=gift_" in m["text"] for m in messages))
        self.assertTrue(any("آیفون خودم" in m["text"] for m in messages))
        self.assertTrue(any("۷۲" in m["text"] for m in messages))

    def test_required_channel_gate_only_after_phone_name_device(self):
        from apps.accounts.models import UserProfile
        handler = StartHandler(MagicMock(), self.brand)
        profile, _ = UserProfile.objects.get_or_create(user=self.recipient)
        profile.channel_join_required = True
        profile.channel_join_verified = False
        profile.device_type = ""
        profile.save(update_fields=["channel_join_required", "channel_join_verified", "device_type"])
        # A channel requirement must not block device/profile onboarding.
        self.assertFalse(async_to_sync(handler.show_channel_gate_if_required)(
            self.recipient.telegram_id, self.recipient
        ))

    def test_paid_gift_notice_is_transactional_even_when_marketing_opted_out(self):
        from apps.accounts.models import UserProfile
        from apps.referrals.tasks import deliver_gamification_notification
        profile, _ = UserProfile.objects.get_or_create(user=self.recipient)
        profile.notification_enabled = False
        profile.save(update_fields=["notification_enabled"])
        notification = GamificationNotification.objects.create(
            user=self.recipient, brand=self.brand, notification_type="custom",
            dedupe_key=f"gift-handover-test:{uuid.uuid4().hex}",
            metadata={
                "event": "gift_handover",
                "delivery_payload": {"text": "🎁 لینک هدیه آماده است", "buttons_data": None},
            },
        )
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=909))
        bot.session.close = AsyncMock()
        with patch("apps.referrals.tasks.Bot", return_value=bot), \
             patch("apps.referrals.tasks.AiohttpSession"):
            self.assertTrue(deliver_gamification_notification.run(notification.pk))
        bot.send_message.assert_awaited_once()
        notification.refresh_from_db()
        self.assertIsNotNone(notification.sent_at)
