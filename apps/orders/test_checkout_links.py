"""Safety regressions for direct transfer checkout and claimable gifts."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.checkout_links import (
    GiftClaimError, QuoteUnavailable, claim_gift, issue_paid_gift,
    paid_gift_link, reserve_card_quote,
)
from apps.orders.models import CardTransferQuote, GiftVoucher, Order, Payment, PaymentCard
from apps.referrals.models import Referral
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.vpn_providers.models import VPNProvider


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CheckoutLinksTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name='Gift Tests', slug='gift-tests', contact_email='testing@example.invalid',
            bot_token='fake-test-token', bot_username='gift_tests_bot',
            gift_expiry_hours=72, currency='T',
        )
        self.buyer = User.objects.create_user(
            username='buyer-for-gift-test', password='unused-test-password',
            brand=self.brand, telegram_id=101,
        )
        self.existing = User.objects.create_user(
            username='preexisting-giftee', password='unused-test-password',
            brand=self.brand, telegram_id=202,
        )
        self.provider = VPNProvider.objects.create(
            brand=self.brand, name='Gift provider', provider_type=VPNProvider.ProviderType.CONNECTIX,
            base_url='https://api.example.invalid', api_key='testing-key',
            status=VPNProvider.ProviderStatus.ACTIVE, is_default=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name='Gift plan', plan_type=SubscriptionPlan.PlanType.TRAFFIC_BASED,
            price=Decimal('500000'), currency='T',
            duration_value=1, duration_unit=SubscriptionPlan.DurationUnit.MONTHS,
            traffic_limit_gb=Decimal('25'),
            vpn_provider=self.provider,
        )
        self.card = PaymentCard.objects.create(
            brand=self.brand, bank_name='آزمایشی', card_number='0000000000000000',
            cardholder_name='گیرنده آزمایشی', is_active=True, unique_amount_enabled=True,
        )

    def order(self, kind=Order.OrderType.NEW_SUBSCRIPTION):
        return Order.objects.create(
            brand=self.brand, user=self.buyer, plan=self.plan,
            order_type=kind, status=Order.OrderStatus.PENDING,
            original_price=self.plan.price, final_price=self.plan.price,
            currency='T',
        )

    def paid_gift(self):
        order = self.order(kind=Order.OrderType.GIFT)
        with patch('apps.orders.signals.broadcast_message'):
            Payment.objects.create(
                order=order, brand=self.brand, user=self.buyer,
                payment_method=Payment.PaymentMethod.CARD_TRANSFER,
                status=Payment.PaymentStatus.CONFIRMED,
                amount=order.final_price, currency='T',
            )
        order.refresh_from_db()
        return order, GiftVoucher.objects.get(order=order)

    def test_same_card_receives_unique_amount_per_pending_order(self):
        first, second = self.order(), self.order()
        a = reserve_card_quote(order_id=str(first.order_id), user_id=self.buyer.pk,
                               brand_id=self.brand.pk, card_id=self.card.pk)
        b = reserve_card_quote(order_id=str(second.order_id), user_id=self.buyer.pk,
                               brand_id=self.brand.pk, card_id=self.card.pk)
        self.assertEqual(a.expected_amount, Decimal('500001'))
        self.assertEqual(b.expected_amount, Decimal('500002'))
        self.assertEqual(a.pk, reserve_card_quote(order_id=str(first.order_id),
            user_id=self.buyer.pk, brand_id=self.brand.pk, card_id=self.card.pk).pk)

    def test_unique_amount_off_is_the_exact_base_amount(self):
        self.card.unique_amount_enabled = False
        self.card.save(update_fields=['unique_amount_enabled'])
        first, second = self.order(), self.order()
        for order in (first, second):
            quote = reserve_card_quote(order_id=str(order.order_id), user_id=self.buyer.pk,
                brand_id=self.brand.pk, card_id=self.card.pk)
            self.assertEqual(quote.expected_amount, Decimal('500000'))
            self.assertFalse(quote.reserved)

    def test_paid_gift_not_provisioned_until_claim(self):
        order, gift = self.paid_gift()
        self.assertEqual(order.status, Order.OrderStatus.PAID)
        self.assertFalse(Subscription.objects.filter(order=order).exists())
        self.assertTrue(paid_gift_link(gift).startswith('https://t.me/gift_tests_bot?start=gift_'))
        self.assertEqual(issue_paid_gift(order_id=order.pk).pk, gift.pk)
        self.assertAlmostEqual((gift.expires_at - gift.created_at).total_seconds(), 72*3600, delta=5)

    def test_existing_member_claims_gift_without_becoming_a_new_referral(self):
        order, gift = self.paid_gift()
        with patch('apps.orders.signals._enqueue_order_provisioning'), \
             patch('apps.referrals.services._enqueue_claimed_gift_reward'), \
             self.captureOnCommitCallbacks(execute=True):
            claim_gift(token=gift.token, brand_id=self.brand.pk,
                       user_id=self.existing.pk, newly_registered=False)
        order.refresh_from_db()
        self.existing.refresh_from_db()
        self.assertEqual(order.recipient_id, self.existing.pk)
        self.assertEqual(self.existing.referral_count, 0)
        self.assertFalse(Referral.objects.filter(referee=self.existing).exists())
        self.assertEqual(Subscription.objects.get(order=order).owner_id, self.existing.pk)
        # Telegram can resend the same /start after an interrupted connection.
        self.assertEqual(
            claim_gift(token=gift.token, brand_id=self.brand.pk,
                       user_id=self.existing.pk, newly_registered=False).pk, order.pk
        )
        self.assertEqual(Subscription.objects.filter(order=order).count(), 1)
        with self.assertRaises(GiftClaimError):
            claim_gift(token=gift.token, brand_id=self.brand.pk,
                       user_id=self.buyer.pk, newly_registered=False)

    def test_new_member_claims_and_counts_as_buyer_invite(self):
        order, gift = self.paid_gift()
        newbie = User.objects.create_user(username='brand-new-giftee',
            brand=self.brand, telegram_id=303, password='unused-test-password')
        with patch('apps.orders.signals._enqueue_order_provisioning'), \
             patch('apps.referrals.services._enqueue_claimed_gift_reward'), \
             self.captureOnCommitCallbacks(execute=True):
            claim_gift(token=gift.token, brand_id=self.brand.pk,
                       user_id=newbie.pk, newly_registered=True)
        newbie.refresh_from_db()
        self.buyer.refresh_from_db()
        self.assertEqual(newbie.referred_by_id, self.buyer.pk)
        self.assertEqual(self.buyer.referral_count, 1)
        self.assertEqual(Referral.objects.get(referee=newbie).referrer_id, self.buyer.pk)
        self.assertEqual(Subscription.objects.get(order=order).owner_id, newbie.pk)

    def test_expired_link_cannot_be_used(self):
        order, gift = self.paid_gift()
        GiftVoucher.objects.filter(pk=gift.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(GiftClaimError):
            claim_gift(token=gift.token, brand_id=self.brand.pk,
                       user_id=self.existing.pk, newly_registered=False)
        self.assertFalse(Subscription.objects.filter(order=order).exists())

    def test_unpaid_gift_has_no_link(self):
        order = self.order(kind=Order.OrderType.GIFT)
        self.assertIsNone(issue_paid_gift(order_id=order.pk))
        self.assertFalse(GiftVoucher.objects.filter(order=order).exists())
