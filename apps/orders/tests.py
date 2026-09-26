from datetime import timedelta
from decimal import Decimal
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import (
    Coupon,
    CouponUsage,
    Order,
    Payment,
    Wallet,
    WalletTransaction,
)
from apps.orders.services import (
    WalletCheckoutError,
    WalletCouponError,
    WalletOperationError,
    confirm_stars_payment,
    credit_wallet,
    debit_wallet,
    pay_order_with_wallet,
    redeem_wallet_coupon,
    validate_stars_pre_checkout,
)
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

    def test_wallet_ledger_credit_is_positive_and_idempotent(self):
        first = credit_wallet(
            wallet_id=self.wallet.pk,
            amount=Decimal("4.00"),
            transaction_type=WalletTransaction.TransactionType.BONUS,
            description="test credit",
            idempotency_key="test-wallet-credit-once",
        )
        replay = credit_wallet(
            wallet_id=self.wallet.pk,
            amount=Decimal("4.00"),
            transaction_type=WalletTransaction.TransactionType.BONUS,
            description="test credit replay",
            idempotency_key="test-wallet-credit-once",
        )
        self.wallet.refresh_from_db()
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(self.wallet.balance, Decimal("19.00"))
        with self.assertRaises(WalletOperationError):
            credit_wallet(
                wallet_id=self.wallet.pk,
                amount=Decimal("-1"),
                transaction_type=WalletTransaction.TransactionType.BONUS,
                description="invalid credit",
            )

    def test_wallet_debit_enforces_limits_and_cannot_go_negative(self):
        self.wallet.daily_spending_limit = Decimal("4.00")
        self.wallet.save(update_fields=["daily_spending_limit"])
        debit_wallet(
            wallet_id=self.wallet.pk,
            amount=Decimal("3.00"),
            transaction_type=WalletTransaction.TransactionType.PAYMENT,
            description="first spend",
        )
        with self.assertRaisesRegex(WalletOperationError, "Daily"):
            debit_wallet(
                wallet_id=self.wallet.pk,
                amount=Decimal("2.00"),
                transaction_type=WalletTransaction.TransactionType.PAYMENT,
                description="over limit",
            )
        with self.assertRaisesRegex(WalletOperationError, "Insufficient"):
            debit_wallet(
                wallet_id=self.wallet.pk,
                amount=Decimal("100.00"),
                transaction_type=WalletTransaction.TransactionType.PAYMENT,
                description="too much",
            )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("12.00"))

    def test_fixed_wallet_coupon_is_atomic_and_only_used_once_per_user(self):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        coupon = Coupon.objects.create(
            brand=self.brand,
            code=f"WALLET-{uuid.uuid4().hex[:8]}",
            name="Wallet credit",
            coupon_type=Coupon.CouponType.FIXED_AMOUNT,
            discount_value=Decimal("3.00"),
            is_active=True,
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1),
            max_uses=1,
            max_uses_per_user=1,
        )
        _coupon, usage, transaction_row = redeem_wallet_coupon(
            user_id=self.user.pk, brand_id=self.brand.pk, code=coupon.code.lower()
        )
        self.wallet.refresh_from_db()
        coupon.refresh_from_db()
        self.assertIsNone(usage.order_id)
        self.assertEqual(transaction_row.amount, Decimal("3.00"))
        self.assertEqual(self.wallet.balance, Decimal("18.00"))
        self.assertEqual(coupon.current_uses, 1)
        with self.assertRaises(WalletCouponError):
            redeem_wallet_coupon(
                user_id=self.user.pk, brand_id=self.brand.pk, code=coupon.code
            )
        self.assertEqual(WalletTransaction.objects.filter(wallet=self.wallet).count(), 1)

    def test_percentage_coupon_cannot_be_turned_into_fixed_wallet_cash(self):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        coupon = Coupon.objects.create(
            brand=self.brand,
            code=f"PERCENT-{uuid.uuid4().hex[:8]}",
            name="Subscription discount",
            coupon_type=Coupon.CouponType.PERCENTAGE,
            discount_value=Decimal("50.00"),
            is_active=True,
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1),
        )
        with self.assertRaisesRegex(WalletCouponError, "subscription order"):
            redeem_wallet_coupon(
                user_id=self.user.pk, brand_id=self.brand.pk, code=coupon.code
            )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("15.00"))
        self.assertFalse(CouponUsage.objects.filter(coupon=coupon).exists())

    def test_stars_payment_requires_matching_quote_and_credits_exactly_once(self):
        self.user.telegram_id = 987654321
        self.user.save(update_fields=["telegram_id"])
        payment = Payment.objects.create(
            brand=self.brand,
            user=self.user,
            wallet=self.wallet,
            payment_method=Payment.PaymentMethod.TELEGRAM_STARS,
            status=Payment.PaymentStatus.PENDING,
            amount=Decimal("5.00"),
            currency="USD",
            stars_amount=500,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        self.assertFalse(
            validate_stars_pre_checkout(
                payment_id=payment.payment_id.hex,
                telegram_user_id=987654321,
                paid_stars=499,
                currency="XTR",
            )
        )
        self.assertTrue(
            validate_stars_pre_checkout(
                payment_id=payment.payment_id.hex,
                telegram_user_id=987654321,
                paid_stars=500,
                currency="XTR",
            )
        )
        confirmed = confirm_stars_payment(
            payment_id=payment.payment_id.hex,
            user_id=self.user.pk,
            charge_id="stars-charge-test-1",
            paid_stars=500,
        )
        replay = confirm_stars_payment(
            payment_id=payment.payment_id.hex,
            user_id=self.user.pk,
            charge_id="stars-charge-test-1",
            paid_stars=500,
        )
        self.wallet.refresh_from_db()
        self.assertEqual(confirmed.pk, replay.pk)
        self.assertEqual(self.wallet.balance, Decimal("20.00"))
        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                reference_id=str(payment.payment_id),
            ).count(),
            1,
        )
