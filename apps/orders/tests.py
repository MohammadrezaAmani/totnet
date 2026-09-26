from decimal import Decimal
import uuid
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import Order, Payment, Wallet, WalletTransaction
from apps.orders.services import WalletCheckoutError, pay_order_with_wallet
from apps.subscriptions.models import SubscriptionPlan
from apps.vpn_providers.models import VPNProvider


class WalletCheckoutTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Wallet Test",
            slug=f"wallet-test-{uuid.uuid4().hex[:8]}",
            contact_email="billing@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
            currency="USD",
        )
        self.user = User.objects.create_user(
            username=f"wallet-user-{uuid.uuid4().hex[:8]}",
            password="test-only-password",
            brand=self.brand,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="Wallet Test Plan",
            plan_type=SubscriptionPlan.PlanType.TIME_BASED,
            price=Decimal("10.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.provider = VPNProvider.objects.create(
            name="Wallet Test Provider",
            provider_type=VPNProvider.ProviderType.CONNECTIX,
            base_url="https://api.example.invalid",
            api_key="masked-test-value",
            brand=self.brand,
            status=VPNProvider.ProviderStatus.ACTIVE,
            is_default=True,
        )
        self.order = Order.objects.create(
            brand=self.brand,
            user=self.user,
            plan=self.plan,
            original_price=Decimal("10.00"),
            final_price=Decimal("10.00"),
            currency="USD",
            status=Order.OrderStatus.PENDING,
        )
        self.wallet = Wallet.objects.create(
            user=self.user,
            brand=self.brand,
            balance=Decimal("15.00"),
            currency="USD",
        )

    def test_wallet_order_payment_debits_once_and_is_idempotent(self):
        with (
            patch("apps.orders.signals.broadcast_message"),
            patch("apps.subscriptions.tasks.provision_paid_order.delay") as provision_delay,
            patch("apps.referrals.tasks.process_referral_reward.delay") as reward_delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            first = pay_order_with_wallet(
                order_id=str(self.order.order_id),
                user_id=self.user.pk,
                brand_id=self.brand.pk,
            )
            second = pay_order_with_wallet(
                order_id=str(self.order.order_id),
                user_id=self.user.pk,
                brand_id=self.brand.pk,
            )

        self.assertFalse(first.already_paid)
        self.assertTrue(second.already_paid)
        self.wallet.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("5.00"))
        self.assertEqual(self.order.status, Order.OrderStatus.PAID)
        self.assertEqual(self.order.subscriptions.count(), 1)
        self.assertEqual(
            Payment.objects.filter(
                order=self.order, status=Payment.PaymentStatus.CONFIRMED
            ).count(),
            1,
        )
        self.assertEqual(
            WalletTransaction.objects.filter(wallet=self.wallet).count(), 1
        )
        provision_delay.assert_called_once_with(self.order.pk)
        reward_delay.assert_called_once()

    def test_insufficient_balance_changes_nothing(self):
        self.wallet.balance = Decimal("9.99")
        self.wallet.save(update_fields=["balance"])

        with self.assertRaisesRegex(WalletCheckoutError, "Insufficient"):
            pay_order_with_wallet(
                order_id=str(self.order.order_id),
                user_id=self.user.pk,
                brand_id=self.brand.pk,
            )

        self.wallet.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("9.99"))
        self.assertEqual(self.order.status, Order.OrderStatus.PENDING)
        self.assertFalse(Payment.objects.filter(order=self.order).exists())
        self.assertFalse(WalletTransaction.objects.filter(wallet=self.wallet).exists())

    def test_order_lookup_is_scoped_to_user_and_brand(self):
        with self.assertRaisesRegex(WalletCheckoutError, "Order was not found"):
            pay_order_with_wallet(
                order_id=str(self.order.order_id),
                user_id=self.user.pk + 1000,
                brand_id=self.brand.pk,
            )
