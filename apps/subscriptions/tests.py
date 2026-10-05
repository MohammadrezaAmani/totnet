import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import Order
from apps.subscriptions.models import (
    ProviderRemoteSubscription,
    Subscription,
    SubscriptionPlan,
)
from apps.subscriptions.services import provision_order_subscription
from apps.subscriptions.tasks import provision_paid_order
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.services.connectix import ConnectixProvider
from apps.vpn_providers.services.connectix_client import (
    ConnectixClientPage,
    ConnectixClientRecord,
)
from apps.vpn_providers.tasks import sync_connectix_status


class ConnectixOrderProvisioningTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Provisioning Test",
            slug=f"provisioning-test-{uuid.uuid4().hex[:8]}",
            contact_email="provisioning@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
            currency="USD",
        )
        self.user = User.objects.create_user(
            username=f"provisioning-user-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.provider = VPNProvider.objects.create(
            name="Connectix Test Provider",
            provider_type=VPNProvider.ProviderType.CONNECTIX,
            base_url="https://api.example.invalid",
            api_key="masked-test-value",
            brand=self.brand,
            status=VPNProvider.ProviderStatus.ACTIVE,
            is_default=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            vpn_provider=self.provider,
            upstream_plan_id="plan-1",
            upstream_group_id="group-1",
            upstream_count_of_devices=2,
            name="Mapped plan",
            plan_type=SubscriptionPlan.PlanType.TIME_BASED,
            price=Decimal("10.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.order = Order.objects.create(
            brand=self.brand,
            user=self.user,
            plan=self.plan,
            original_price=Decimal("10.00"),
            final_price=Decimal("10.00"),
            currency="USD",
            status=Order.OrderStatus.PAID,
        )

    def test_connectix_provisioning_routes_by_plan_and_is_locally_idempotent(self):
        async def activate(subscription):
            subscription.status = Subscription.SubscriptionStatus.ACTIVE
            subscription.subscription_url = "https://subscription.example.invalid/token"
            await subscription.asave(
                update_fields=("status", "subscription_url", "updated_at")
            )
            return True

        provision_mock = AsyncMock(side_effect=activate)
        with patch(
            "apps.subscriptions.services.provision_connectix_subscription",
            provision_mock,
        ):
            first = async_to_sync(provision_order_subscription)(self.order.pk)
            second = async_to_sync(provision_order_subscription)(self.order.pk)

        self.order.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.vpn_provider_id, self.provider.pk)
        self.assertEqual(first.owner_id, self.user.pk)
        self.assertEqual(first.status, Subscription.SubscriptionStatus.ACTIVE)
        self.assertEqual(self.order.status, Order.OrderStatus.COMPLETED)
        self.assertEqual(Subscription.objects.filter(order=self.order).count(), 1)
        provision_mock.assert_awaited_once_with(first)

    def test_gift_subscription_belongs_to_recipient(self):
        recipient = User.objects.create_user(
            username=f"gift-recipient-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.order.recipient = recipient
        self.order.order_type = Order.OrderType.GIFT
        self.order.save(update_fields=("recipient", "order_type", "updated_at"))

        async def activate(subscription):
            subscription.status = Subscription.SubscriptionStatus.ACTIVE
            subscription.subscription_url = "https://subscription.example.invalid/gift"
            await subscription.asave(
                update_fields=("status", "subscription_url", "updated_at")
            )
            return True

        with patch(
            "apps.subscriptions.services.provision_connectix_subscription",
            new_callable=AsyncMock,
            side_effect=activate,
        ):
            subscription = async_to_sync(provision_order_subscription)(self.order.pk)

        self.assertEqual(subscription.user_id, self.user.pk)
        self.assertEqual(subscription.owner_id, recipient.pk)

    def test_uncertain_first_attempt_is_not_blindly_retried(self):
        subscription = Subscription.objects.create(
            brand=self.brand,
            user=self.user,
            owner=self.user,
            plan=self.plan,
            order=self.order,
            vpn_provider=self.provider,
            starts_at=self.order.created_at,
            status=Subscription.SubscriptionStatus.PENDING,
            provisioning_attempts=1,
        )
        with patch(
            "apps.subscriptions.tasks.provision_order_subscription",
            new_callable=AsyncMock,
        ) as provision_mock:
            result = provision_paid_order.run(self.order.pk)

        self.assertFalse(result)
        subscription.refresh_from_db()
        self.assertEqual(subscription.provisioning_attempts, 1)
        provision_mock.assert_not_awaited()

    def test_connectix_status_sync_updates_local_lifecycle_and_keeps_usage_raw(self):
        subscription = Subscription.objects.create(
            brand=self.brand,
            user=self.user,
            owner=self.user,
            plan=self.plan,
            order=self.order,
            vpn_provider=self.provider,
            starts_at=self.order.created_at,
            status=Subscription.SubscriptionStatus.ACTIVE,
        )
        remote = ProviderRemoteSubscription.objects.create(
            subscription=subscription,
            provider=self.provider,
            remote_id="remote-123",
            state=ProviderRemoteSubscription.State.ACTIVE,
        )
        record = ConnectixClientRecord(
            remote_id="remote-123",
            username="client-name",
            password="",
            is_active=False,
            is_expired=True,
            used_traffic="12.4 GB",
        )
        provider_client = ConnectixProvider(
            base_url="https://api.example.invalid",
            username="seller@example.invalid",
            password="test-password",
        )

        async def get_clients(*, page):
            return ConnectixClientPage(
                (record,), current_page=page, last_page=1, total=1
            )

        async def close():
            return None

        with (
            patch("apps.vpn_providers.tasks._client", return_value=provider_client),
            patch.object(
                provider_client.client, "get_clients", side_effect=get_clients
            ),
            patch.object(provider_client, "close", side_effect=close),
        ):
            self.assertTrue(sync_connectix_status(self.provider))

        remote.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(remote.remote_status, "expired")
        self.assertEqual(remote.metadata["used_traffic_raw"], "12.4 GB")
        self.assertEqual(subscription.status, Subscription.SubscriptionStatus.EXPIRED)


class SubscriptionPlanPricingTests(TestCase):
    def test_expired_offer_does_not_apply_discount(self):
        from datetime import timedelta

        from django.utils import timezone

        plan = SubscriptionPlan(
            price=Decimal("100.00"),
            discount_percentage=Decimal("25.00"),
            offer_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(plan.discounted_price, Decimal("100.00"))

    def test_active_offer_applies_discount(self):
        from datetime import timedelta

        from django.utils import timezone

        plan = SubscriptionPlan(
            price=Decimal("100.00"),
            discount_percentage=Decimal("25.00"),
            offer_expires_at=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(plan.discounted_price, Decimal("75.0000"))


class ProvisioningDeliveryTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name=f"Delivery Test {uuid.uuid4().hex[:8]}",
            slug=f"delivery-test-{uuid.uuid4().hex[:8]}",
            contact_email="delivery@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
            currency="USD",
        )
        self.user = User.objects.create_user(
            username=f"delivery-user-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.provider = VPNProvider.objects.create(
            name="Delivery Provider",
            provider_type=VPNProvider.ProviderType.CONNECTIX,
            base_url="https://api.example.invalid",
            api_key="masked-test-value",
            brand=self.brand,
            status=VPNProvider.ProviderStatus.ACTIVE,
            is_default=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            vpn_provider=self.provider,
            upstream_plan_id="plan-delivery",
            upstream_group_id="group-delivery",
            upstream_count_of_devices=1,
            name="Delivery plan",
            plan_type=SubscriptionPlan.PlanType.TIME_BASED,
            price=Decimal("10.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.order = Order.objects.create(
            brand=self.brand,
            user=self.user,
            plan=self.plan,
            original_price=Decimal("10.00"),
            final_price=Decimal("10.00"),
            currency="USD",
            status=Order.OrderStatus.PAID,
        )

    def test_success_notification_exposes_link_and_direct_config_qr_action(self):
        subscription = Subscription.objects.create(
            brand=self.brand,
            user=self.user,
            owner=self.user,
            plan=self.plan,
            order=self.order,
            vpn_provider=self.provider,
            starts_at=self.order.created_at,
            status=Subscription.SubscriptionStatus.PENDING,
        )

        async def activate(_order_pk):
            subscription.status = Subscription.SubscriptionStatus.ACTIVE
            subscription.subscription_url = (
                "https://subscription.example.invalid/direct"
            )
            subscription.connectix_username = "client-123"
            await subscription.asave(
                update_fields=(
                    "status",
                    "subscription_url",
                    "connectix_username",
                    "updated_at",
                )
            )
            return subscription

        with (
            patch(
                "apps.subscriptions.tasks.provision_order_subscription",
                new_callable=AsyncMock,
                side_effect=activate,
            ),
            patch("utils.message.broadcast_message") as broadcast_mock,
        ):
            self.assertTrue(provision_paid_order.run(self.order.pk))

        broadcast_mock.assert_called_once()
        kwargs = broadcast_mock.call_args.kwargs
        self.assertIn("client-123", kwargs["text"])
        self.assertIn("subscription.example.invalid/direct", kwargs["text"])
        self.assertEqual(
            kwargs["buttons_data"][0][0]["callback_data"],
            f"get_config_{subscription.pk}",
        )
