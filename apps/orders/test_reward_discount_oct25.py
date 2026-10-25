"""Money actually spent is not the number of pills earned."""
from decimal import Decimal
from django.test import TestCase

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import Wallet, WalletTransaction, Order, Payment
from apps.orders.services import confirmed_reward_discounts
from apps.subscriptions.models import SubscriptionPlan


class RewardDiscountLedgerTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Reward ledger test", slug="reward-ledger-test",
            contact_email="test@example.invalid", bot_token="test-bot", currency="T",
        )
        self.user = User.objects.create_user(
            username="reward-ledger-user", brand=self.brand, telegram_id=100101,
        )
        self.wallet = Wallet.objects.create(
            brand=self.brand, user=self.user, balance=Decimal("100"), currency="T",
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Ledger plan", plan_type="traffic_based",
            price=Decimal("50"), currency="T",
        )

    def test_only_confirmed_reward_sourced_payments_count(self):
        order = Order.objects.create(
            brand=self.brand, user=self.user, plan=self.plan,
            original_price=50, final_price=50, currency="T",
            status=Order.OrderStatus.PAID,
        )
        # A pill or a wallet credit alone is not a redeemed cash discount.
        WalletTransaction.objects.create(
            wallet=self.wallet, transaction_type=WalletTransaction.TransactionType.REFERRAL_REWARD,
            amount=10, balance_before=0, balance_after=10, description="pill credit",
        )
        self.assertEqual(confirmed_reward_discounts(
            user_id=self.user.pk, brand_id=self.brand.pk), Decimal("0"))
        WalletTransaction.objects.create(
            wallet=self.wallet, transaction_type=WalletTransaction.TransactionType.PAYMENT,
            amount=10, balance_before=10, balance_after=0, description="paid with reward",
            metadata={"order_id": str(order.order_id), "reward_discount_amount": "10"},
        )
        Payment.objects.create(
            brand=self.brand, user=self.user, order=order, wallet=self.wallet,
            payment_method=Payment.PaymentMethod.WALLET, amount=50, currency="T",
            status=Payment.PaymentStatus.CONFIRMED,
        )
        self.assertEqual(confirmed_reward_discounts(
            user_id=self.user.pk, brand_id=self.brand.pk), Decimal("10"))
        Order.objects.filter(pk=order.pk).update(status=Order.OrderStatus.CANCELLED)
        self.assertEqual(confirmed_reward_discounts(
            user_id=self.user.pk, brand_id=self.brand.pk), Decimal("0"))
