"""Regression tests for renewal checkout and purchased-gift handover."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.handlers.subscriptions import SubscriptionHandler
from apps.brands.models import Brand
from apps.orders.gift_delivery import queue_ready_gift_details
from apps.orders.models import GiftVoucher, Order
from apps.referrals.models import NotificationRule, NotificationTrigger, ReferralReward
from apps.referrals.tasks import _queue_gift_transfer_notice
from apps.referrals.scheduling import run_notification_rules
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.vpn_providers.models import VPNProvider


class RenewalGiftFlowTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Gift renewal tests", slug="gift-renewal-tests",
            bot_token="bot-gift-renewal-tests", bot_username="gift_test_bot",
            contact_email="gift-tests@example.invalid", status=Brand.BrandStatus.ACTIVE,
        )
        self.buyer = User.objects.create_user(
            username="gift_buyer", brand=self.brand, telegram_id=80101001,
        )
        self.recipient = User.objects.create_user(
            username="gift_recipient", brand=self.brand, telegram_id=80101002,
        )
        self.provider = VPNProvider.objects.create(
            brand=self.brand, name="Connectix", provider_type=VPNProvider.ProviderType.CONNECTIX,
            status=VPNProvider.ProviderStatus.ACTIVE, is_default=True,
            base_url="https://vpn.example.invalid", api_key="masked-example",
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, vpn_provider=self.provider,
            name="20G royal", service_category="royal",
            plan_type=SubscriptionPlan.PlanType.TRAFFIC_BASED,
            max_users=2, traffic_limit_gb=20, duration_value=1,
            duration_unit=SubscriptionPlan.DurationUnit.MONTHS,
            price=Decimal("100"), currency="USD",
        )
        self.order = Order.objects.create(
            brand=self.brand, user=self.buyer, plan=self.plan,
            order_type=Order.OrderType.GIFT, status=Order.OrderStatus.PAID,
            original_price=Decimal("100"), final_price=Decimal("100"),
            currency="USD",
        )
        self.subscription = Subscription.objects.create(
            brand=self.brand, user=self.buyer, owner=self.buyer,
            plan=self.plan, order=self.order, vpn_provider=self.provider,
            is_gift=True, display_name="gift_buyer_others",
            status=Subscription.SubscriptionStatus.ACTIVE,
            starts_at=timezone.now(),
            subscription_url="https://sub.example.invalid/gift",
            connectix_username="test-login", connectix_password="test-pass",
        )
        self.voucher = GiftVoucher.objects.create(
            order=self.order, token="testing-gift-token",
            expires_at=timezone.now() + timedelta(hours=48),
        )

    def visible_to(self, user):
        return list(Subscription.objects.filter(
            SubscriptionHandler.visible_to_user(user), brand=self.brand,
        ).values_list("pk", flat=True))

    def transfer(self):
        self.order.recipient = self.recipient
        self.order.recipient_claimed_at = timezone.now()
        self.order.save(update_fields=("recipient", "recipient_claimed_at", "updated_at"))
        self.voucher.claimed_by = self.recipient
        self.voucher.claimed_at = timezone.now()
        self.voucher.save(update_fields=("claimed_by", "claimed_at"))
        self.subscription.owner = self.recipient
        self.subscription.display_name = "gift_recipient"
        self.subscription.save(update_fields=("owner", "display_name", "updated_at"))

    def test_unclaimed_gift_shows_only_to_buyer_and_claimed_gift_only_to_recipient(self):
        self.assertEqual(self.visible_to(self.buyer), [self.subscription.pk])
        self.assertEqual(self.visible_to(self.recipient), [])
        self.transfer()
        self.assertEqual(self.visible_to(self.buyer), [])
        self.assertEqual(self.visible_to(self.recipient), [self.subscription.pk])

    def test_renewal_first_opens_choice_page_without_creating_order(self):
        handler = SubscriptionHandler(MagicMock(), self.brand)
        handler.get_or_create_user = AsyncMock(return_value=(self.buyer, False))
        handler.purchase_credit = AsyncMock(return_value=Decimal("13"))
        handler.edit_message_with_keyboard = AsyncMock()
        callback = MagicMock()
        callback.message.chat.id = self.buyer.telegram_id
        callback.message.message_id = 17
        callback.answer = AsyncMock()
        before = Order.objects.count()
        async_to_sync(handler.show_renewal_details)(callback, self.subscription.pk)
        self.assertEqual(Order.objects.count(), before)
        args = handler.edit_message_with_keyboard.await_args.args
        self.assertIn("همیشه قبل از اتمام تایم", args[2])
        self.assertIn("۸۷", args[2].translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")))
        buttons = [btn for row in args[3].inline_keyboard for btn in row]
        self.assertEqual(buttons[0].callback_data, "purchase_subscription")
        self.assertEqual(buttons[0].style, "success")
        self.assertEqual(buttons[1].callback_data, f"repurchase_subscription_{self.subscription.pk}")

    def test_gift_buyer_receives_account_credentials_and_recipient_link(self):
        with patch("apps.referrals.tasks._queue_once", return_value=True) as send:
            self.assertTrue(queue_ready_gift_details(order_id=self.order.pk))
        text = send.call_args.kwargs["text"]
        self.assertIn("test-login", text)
        self.assertIn("test-pass", text)
        self.assertIn("https://t.me/gift_test_bot?start=gift_testing-gift-token", text)
        self.assertEqual(send.call_args.kwargs["user"].pk, self.buyer.pk)

    def test_gift_credentials_are_sent_even_without_subscription_url(self):
        self.subscription.subscription_url = ""
        self.subscription.save(update_fields=("subscription_url", "updated_at"))
        with patch("apps.referrals.tasks._queue_once", return_value=True) as send:
            self.assertTrue(queue_ready_gift_details(order_id=self.order.pk))
        text = send.call_args.kwargs["text"]
        self.assertIn("test-login", text)
        self.assertIn("test-pass", text)

    def test_transfer_notification_only_mentions_pill_when_awarded(self):
        self.transfer()
        with patch("apps.referrals.tasks._queue_once", return_value=True) as send:
            self.assertTrue(_queue_gift_transfer_notice(self.order))
        self.assertIn("از فهرست اشتراک‌های شما حذف شده", send.call_args.kwargs["text"])
        self.assertNotIn("یک 💊", send.call_args.kwargs["text"])
        self.assertEqual(send.call_args.kwargs["dedupe_key"], f"gift-claim-referral:{self.order.pk}")

        ReferralReward.objects.create(
            order=self.order, user=self.buyer, brand=self.brand,
            reward_type="normal_point", amount=1, cash_value=1,
            status=ReferralReward.RewardStatus.PROCESSED,
        )
        with patch("apps.referrals.tasks._queue_once", return_value=True) as send:
            self.assertTrue(_queue_gift_transfer_notice(self.order))
        self.assertIn("یک 💊", send.call_args.kwargs["text"])

    def test_two_configurable_profile_reminders_can_be_queued_separately(self):
        """n/m reminder rules share a kind but must not suppress one another."""
        for day, message in ((3, "پروفایل خود را تکمیل کنید."),
                             (7, "پروفایل را کامل کنید تا قرص بگیرید.")):
            NotificationRule.objects.create(
                name=f"profile-day-{day}", brand=self.brand,
                kind=NotificationRule.Kind.PROFILE_COMPLETION,
                trigger=NotificationTrigger.JOINED,
                delay_minutes=day * 1440, is_active=True, message=message,
            )
        with (patch("apps.referrals.scheduling.lifecycle_candidates") as candidates,
              patch("apps.referrals.tasks._queue_once", return_value=True) as send):
            candidates.return_value = [(self.buyer, "joined:1", timezone.now() - timedelta(days=8))]
            counts = run_notification_rules()
        self.assertEqual(sum(counts.values()), 2)
        self.assertEqual(send.call_count, 2)
        self.assertNotEqual(
            send.call_args_list[0].kwargs["dedupe_key"],
            send.call_args_list[1].kwargs["dedupe_key"],
        )
