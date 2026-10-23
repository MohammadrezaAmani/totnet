"""Direct-referrer purchase alerts must not depend on the pill reward system."""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import GiftVoucher, Order, Payment
from apps.referrals.models import GamificationNotification, Referral, ReferralLink, ReferralReward
from apps.referrals.services import attribute_referral
from apps.referrals.tasks import (
    _queue_referral_purchase_for_payment,
    process_referral_reward,
    deliver_gamification_notification,
    recover_missing_referrer_purchase_notifications,
)
from apps.subscriptions.models import SubscriptionPlan


class ReferrerPurchaseNotificationTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Purchase Alert Test", slug=f"purchase-alert-{uuid.uuid4().hex[:8]}",
            bot_token="fake-bot-token", currency="T",
            contact_email="alerts@example.invalid",
        )
        self.inviter = User.objects.create_user(
            username="inviter", brand=self.brand, telegram_id=90101
        )
        self.buyer = User.objects.create_user(
            username="buyer", brand=self.brand, telegram_id=90202
        )
        link = ReferralLink.objects.create(
            user=self.inviter, brand=self.brand, code=uuid.uuid4().hex[:12]
        )
        self.assertTrue(attribute_referral(
            user_id=self.buyer.pk, brand_id=self.brand.pk, code=link.code
        ))
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="30g", price=Decimal("100000"),
            currency="T", duration_value=1,
            duration_unit=SubscriptionPlan.DurationUnit.MONTHS,
        )

    def purchase(self, *, amount=Decimal("100000"), order_type=Order.OrderType.NEW_SUBSCRIPTION):
        order = Order.objects.create(
            brand=self.brand, user=self.buyer, plan=self.plan,
            original_price=amount, final_price=amount, currency="T",
            order_type=order_type, status=Order.OrderStatus.PENDING,
        )
        payment = Payment.objects.create(
            brand=self.brand, user=self.buyer, order=order,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=amount, currency="T",
        )
        order.refresh_from_db()
        return order, payment

    def queue(self, payment):
        with patch("apps.referrals.tasks._publish_notification") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                result = _queue_referral_purchase_for_payment(str(payment.payment_id))
        return result, publish

    def test_notifies_inviter_even_when_no_reward_program_configured(self):
        order, payment = self.purchase()
        result, publish = self.queue(payment)
        self.assertTrue(result)
        notification = GamificationNotification.objects.get(
            dedupe_key=f"referral-purchase:{order.pk}"
        )
        self.assertEqual(notification.user_id, self.inviter.pk)
        self.assertEqual(notification.notification_type, "referral_purchase")
        self.assertIn("خرید", notification.metadata["delivery_payload"]["text"])
        self.assertNotIn("یک قرص", notification.metadata["delivery_payload"]["text"])
        publish.assert_called_once_with(notification.pk)
        self.assertFalse(self.queue(payment)[0])

    def test_telegram_delivery_marks_sent_only_after_success(self):
        order, payment = self.purchase()
        self.assertTrue(self.queue(payment)[0])
        notice = GamificationNotification.objects.get(dedupe_key=f"referral-purchase:{order.pk}")
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=718))
        bot.session.close = AsyncMock()
        with patch("apps.referrals.tasks.Bot", return_value=bot), patch("apps.referrals.tasks.AiohttpSession"):
            self.assertTrue(deliver_gamification_notification.run(notice.pk))
            self.assertFalse(deliver_gamification_notification.run(notice.pk))
        notice.refresh_from_db()
        self.assertIsNotNone(notice.sent_at)
        self.assertEqual(notice.metadata["telegram_message_id"], 718)
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args.kwargs["chat_id"], self.inviter.telegram_id)

    def test_processing_reward_zero_still_notifies(self):
        order, payment = self.purchase()
        with patch("apps.referrals.tasks._publish_notification") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(process_referral_reward.run(str(payment.payment_id)), "0")
        self.assertFalse(ReferralReward.objects.filter(order=order).exists())
        self.assertEqual(GamificationNotification.objects.filter(
            dedupe_key=f"referral-purchase:{order.pk}"
        ).count(), 1)
        publish.assert_called_once()

    def test_partial_payment_is_not_a_confirmed_purchase(self):
        order = Order.objects.create(
            brand=self.brand, user=self.buyer, plan=self.plan,
            original_price=Decimal("100000"), final_price=Decimal("100000"),
            currency="T", status=Order.OrderStatus.AWAITING_PAYMENT,
        )
        first = Payment.objects.create(
            brand=self.brand, user=self.buyer, order=order,
            payment_method=Payment.PaymentMethod.WALLET,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal("40000"), currency="T",
        )
        self.assertFalse(self.queue(first)[0])
        second = Payment.objects.create(
            brand=self.brand, user=self.buyer, order=order,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal("60000"), currency="T",
        )
        self.assertTrue(self.queue(second)[0])
        self.assertEqual(GamificationNotification.objects.filter(
            dedupe_key=f"referral-purchase:{order.pk}"
        ).count(), 1)

    def test_notifies_only_actual_referrer(self):
        stranger = User.objects.create_user(username="stranger", brand=self.brand, telegram_id=90404)
        order = Order.objects.create(
            brand=self.brand, user=stranger, plan=self.plan,
            original_price=Decimal("100000"), final_price=Decimal("100000"),
            currency="T", status=Order.OrderStatus.PENDING,
        )
        payment = Payment.objects.create(
            brand=self.brand, user=stranger, order=order,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal("100000"), currency="T",
        )
        self.assertFalse(self.queue(payment)[0])
        self.assertFalse(GamificationNotification.objects.filter(
            dedupe_key=f"referral-purchase:{order.pk}"
        ).exists())

    def test_legacy_user_referred_by_without_referral_ledger_gets_alert(self):
        Referral.objects.filter(brand=self.brand, referee=self.buyer).delete()
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.referred_by_id, self.inviter.pk)
        order, payment = self.purchase()
        self.assertTrue(self.queue(payment)[0])
        self.assertEqual(GamificationNotification.objects.get(
            dedupe_key=f"referral-purchase:{order.pk}"
        ).user_id, self.inviter.pk)

    def test_rejected_referral_cannot_fall_back_to_legacy_attribution(self):
        Referral.objects.filter(brand=self.brand, referee=self.buyer).update(status=Referral.ReferralStatus.REJECTED)
        order, payment = self.purchase()
        self.assertFalse(self.queue(payment)[0])

    def test_gift_purchase_notifies_buyer_inviter_but_not_unclaimed_recipient(self):
        order, payment = self.purchase(order_type=Order.OrderType.GIFT)
        self.assertTrue(self.queue(payment)[0])
        self.assertEqual(GamificationNotification.objects.get(
            dedupe_key=f"referral-purchase:{order.pk}"
        ).user_id, self.inviter.pk)
        self.assertFalse(GamificationNotification.objects.filter(
            dedupe_key=f"gift-claim-referral:{order.pk}"
        ).exists())

    def test_claimed_gift_notifies_gift_purchaser_not_other_referrer(self):
        order, payment = self.purchase(order_type=Order.OrderType.GIFT)
        recipient = User.objects.create_user(
            username="new-gift-recipient", brand=self.brand, telegram_id=90505,
        )
        link = ReferralLink.objects.create(
            user=self.buyer, brand=self.brand, code=uuid.uuid4().hex[:12],
        )
        self.assertTrue(attribute_referral(
            user_id=recipient.pk, brand_id=self.brand.pk, code=link.code
        ))
        now = timezone.now()
        order.recipient = recipient
        order.recipient_claimed_at = now
        order.save(update_fields=["recipient", "recipient_claimed_at", "updated_at"])
        GiftVoucher.objects.create(
            order=order, token=uuid.uuid4().hex,
            expires_at=now + timedelta(days=3),
            claimed_by=recipient, claimed_at=now, new_member_at_claim=True,
        )
        with patch("apps.referrals.tasks._publish_notification"):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(_queue_referral_purchase_for_payment(str(payment.payment_id)))
        alert = GamificationNotification.objects.get(
            dedupe_key=f"gift-claim-referral:{order.pk}"
        )
        self.assertEqual(alert.user_id, self.buyer.pk)
        self.assertIn("هدیه", alert.metadata["delivery_payload"]["text"])

    def test_claiming_gift_as_existing_member_does_not_invent_referral(self):
        order, payment = self.purchase(order_type=Order.OrderType.GIFT)
        now = timezone.now()
        order.recipient = self.inviter
        order.recipient_claimed_at = now
        order.save(update_fields=["recipient", "recipient_claimed_at", "updated_at"])
        GiftVoucher.objects.create(
            order=order, token=uuid.uuid4().hex,
            expires_at=now + timedelta(days=3),
            claimed_by=self.inviter, claimed_at=now, new_member_at_claim=False,
        )
        self.assertTrue(self.queue(payment)[0])  # Buyer did make a purchase.
        self.assertFalse(GamificationNotification.objects.filter(
            dedupe_key=f"gift-claim-referral:{order.pk}"
        ).exists())

    def test_recovery_handles_paid_orders_even_if_reward_ledger_exists(self):
        order, payment = self.purchase()
        ReferralReward.objects.create(
            order=order, user=self.inviter, brand=self.brand,
            reward_type="normal_point", status=ReferralReward.RewardStatus.PROCESSED,
            amount=Decimal(1), cash_value=Decimal("8000"), currency="T",
        )
        with patch("apps.referrals.tasks._publish_notification"):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(recover_missing_referrer_purchase_notifications.run(), 1)
        self.assertTrue(GamificationNotification.objects.filter(
            dedupe_key=f"referral-purchase:{order.pk}"
        ).exists())
        with patch("apps.referrals.tasks._publish_notification"):
            self.assertEqual(recover_missing_referrer_purchase_notifications.run(), 0)
