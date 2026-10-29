"""Regression coverage for hard-delete controls in Django admin."""

from datetime import timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import (
    CardTransferQuote, GiftVoucher, Order, PaymentCard, Wallet, WalletTransaction,
)
from apps.subscriptions.models import SubscriptionPlan


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class AdminHardDeleteTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Admin delete test", slug="admin-delete-test",
            contact_email="admin@example.invalid", bot_token="delete-test-token",
        )
        self.admin_user = User.objects.create_superuser(
            username="admin-delete-superuser", password="testing-only",
            email="admin@example.invalid",
        )
        self.buyer = User.objects.create_user(
            username="admin-delete-buyer", brand=self.brand,
        )
        self.receiver = User.objects.create_user(
            username="admin-delete-receiver", brand=self.brand,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Test plan", plan_type="traffic_based", price=100,
        )
        self.client.force_login(self.admin_user)

    def create_gift(self):
        order = Order.objects.create(
            brand=self.brand, user=self.buyer, plan=self.plan,
            original_price=100, final_price=100, order_type=Order.OrderType.GIFT,
        )
        gift = GiftVoucher.objects.create(
            order=order, token="test-gift-token", claimed_by=self.receiver,
            expires_at=timezone.now() + timedelta(hours=48),
        )
        return order, gift

    def test_single_user_admin_delete_removes_protected_gift_and_order(self):
        order, gift = self.create_gift()
        wallet = Wallet.objects.create(user=self.buyer, brand=self.brand)
        WalletTransaction.objects.create(
            wallet=wallet, transaction_type=WalletTransaction.TransactionType.DEPOSIT,
            amount=Decimal("5"), balance_before=0, balance_after=5,
            description="test wallet credit",
        )
        response = self.client.get(reverse("admin:accounts_user_delete", args=[self.buyer.pk]))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("admin:accounts_user_delete", args=[self.buyer.pk]),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.buyer.pk).exists())
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
        self.assertFalse(GiftVoucher.objects.filter(pk=gift.pk).exists())
        self.assertFalse(Wallet.objects.filter(pk=wallet.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.receiver.pk).exists())

    def test_bulk_user_admin_delete_removes_protected_claimed_gift(self):
        order, gift = self.create_gift()
        response = self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "delete_selected", "_selected_action": [str(self.receiver.pk)],
             "post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.receiver.pk).exists())
        self.assertFalse(GiftVoucher.objects.filter(pk=gift.pk).exists())
        # Claiming another person's gift must not delete that person's order.
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.buyer.pk).exists())

    def test_card_admin_delete_removes_protected_quotes(self):
        order, _gift = self.create_gift()
        card = PaymentCard.objects.create(
            brand=self.brand, bank_name="Test bank", cardholder_name="Test owner",
            card_number="1234000012340000",
        )
        CardTransferQuote.objects.create(
            order=order, card=card, base_amount=100, expected_amount=101,
            release_after=timezone.now() + timedelta(hours=1),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        response = self.client.post(
            reverse("admin:orders_paymentcard_delete", args=[card.pk]),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PaymentCard.objects.filter(pk=card.pk).exists())
        self.assertFalse(CardTransferQuote.objects.filter(card_id=card.pk).exists())

    def test_brand_manager_cannot_delete_customer_from_other_brand(self):
        other_brand = Brand.objects.create(
            name="Another brand", slug="another-admin-delete-brand",
            contact_email="other@example.invalid", bot_token="another-delete-token",
        )
        outside = User.objects.create_user(username="outside-admin-delete", brand=other_brand)
        manager = User.objects.create_user(
            username="admin-delete-manager", brand=self.brand, is_staff=True,
        )
        manager.admin_brands.add(self.brand)
        manager.user_permissions.add(
            Permission.objects.get(codename="delete_user", content_type__app_label="accounts")
        )
        self.client.force_login(manager)
        response = self.client.post(
            reverse("admin:accounts_user_delete", args=[outside.pk]),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(User.objects.filter(pk=outside.pk).exists())

    def test_explicitly_locked_admin_pages_have_delete_permission(self):
        from apps.orders.models import GiftVoucher
        from apps.subscriptions.models import ProviderRemoteSubscription
        from apps.referrals.models import RewardPointLedger
        for model in (GiftVoucher, ProviderRemoteSubscription, RewardPointLedger):
            model_admin = admin.site._registry[model]
            from django.test import RequestFactory
            request = RequestFactory().get("/admin/")
            request.user = self.admin_user
            self.assertTrue(model_admin.has_delete_permission(request))
