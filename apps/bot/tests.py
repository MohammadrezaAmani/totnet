from decimal import Decimal
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.core.cache import cache
from django.test import TestCase, SimpleTestCase, override_settings

from apps.accounts.models import User
from apps.bot.handlers.purchase import PurchaseHandler
from apps.bot.models import BotState
from apps.brands.models import Brand, BrandPaymentMethod
from apps.orders.models import Order, Wallet, Payment, PaymentCard
from apps.orders.services import pay_order_with_wallet
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.subscriptions.catalog import get_cached_plans, refresh_plan_catalog
from apps.subscriptions.tasks import provision_paid_order
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.services.connectix import ConnectixProvider
from apps.vpn_providers.services.connectix_client import ConnectixUpstreamError
from apps.vpn_providers.tasks import sync_connectix_plans
from utils.proxy import normalize_proxy


class ProxyTests(SimpleTestCase):
    def test_socks_alias_bare_address_and_blank(self):
        for value in [
            "socks://127.0.0.1:2080",
            "127.0.0.1:2080",
            " socks5://127.0.0.1:2080 ",
        ]:
            self.assertEqual(normalize_proxy(value), "socks5://127.0.0.1:2080")
        self.assertIsNone(normalize_proxy(""))
        self.assertEqual(
            normalize_proxy("socks://localhost:2080", running_in_docker=True),
            "socks5://host.docker.internal:2080",
        )


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class BotConnectixFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(
            name="A & B",
            slug="bot-test",
            bot_token="test-token",
            contact_email="test@example.invalid",
            currency="T",
        )
        self.provider = VPNProvider.objects.create(
            brand=self.brand,
            name="Connectix",
            provider_type="connectix",
            base_url="https://api.example.invalid",
            is_default=True,
        )
        self.user = User.objects.create_user(
            username="test-user", telegram_id=123, brand=self.brand
        )
        self.bot = MagicMock()
        self.bot.edit_message_text = AsyncMock()
        self.bot.send_message = AsyncMock()
        self.handler = PurchaseHandler(self.bot, self.brand)
        self.handler.get_or_create_user = AsyncMock(return_value=(self.user, False))
        self.callback = MagicMock()
        self.callback.message.chat.id = 123
        self.callback.message.message_id = 42
        self.callback.answer = AsyncMock()
        self.creates = 0

    def provider_client(self, *args, **kwargs):
        def transport(request):
            path = request.url.path
            if path.endswith("auth/login"):
                return httpx.Response(200, json={"token": "synthetic-token"})
            if path.endswith("seller-plans"):
                self.assertEqual(request.url.params["forClientPage"], "false")
                return httpx.Response(
                    200,
                    json={
                        "groups": [{"id": "group-1", "name": "Sublink"}],
                        "seller_plan_group": [
                            {
                                "seller_plans": [
                                    {
                                        "id": "plan-1",
                                        "title": "Monthly <VPN>",
                                        "price": "98,000",
                                        "sell_price": "0",
                                        "period": 1,
                                        "period_unit": "Months",
                                        "traffic_amount": 15,
                                        "count_of_devices": 2,
                                        "group_name_translations": {"en": "Sublink"},
                                        "is_displayed_in_robot": True,
                                    }
                                ]
                            }
                        ],
                    },
                )
            if path.endswith("seller-data"):
                return httpx.Response(200, json={"seller": {"short_id": "abc"}})
            if path.endswith("meta-data"):
                return httpx.Response(
                    200,
                    json={
                        "seller_plans": [{"id": "plan-1", "title": "Monthly"}],
                        "groups": [],
                    },
                )
            if path.endswith("clients/store"):
                self.creates += 1
                return httpx.Response(200, json={"client_id": "remote-1"})
            if path.endswith("clients"):
                return httpx.Response(
                    200,
                    json={
                        "clients": {
                            "current_page": 1,
                            "last_page": 1,
                            "total": 1,
                            "data": [
                                {
                                    "id": "remote-1",
                                    "username": "abc12345",
                                    "is_active": True,
                                    "is_expired": False,
                                    "subscription_link": "https://sub.example.invalid/test",
                                }
                            ],
                        }
                    },
                )
            return httpx.Response(404, json={})

        return ConnectixProvider(
            base_url="https://api.example.invalid",
            username="test",
            password="test",
            transport=httpx.MockTransport(transport),
        )

    def import_plan(self):
        with (
            patch("apps.vpn_providers.tasks._client", side_effect=self.provider_client),
            patch("apps.vpn_providers.tasks.call_command"),
        ):
            self.assertEqual(sync_connectix_plans.run(self.provider.pk), 1)
        return SubscriptionPlan.objects.get(vpn_provider=self.provider)

    def test_import_selection_purchase_payment_activation_and_delivery(self):
        plan = self.import_plan()
        self.assertEqual(plan.price, Decimal("98000"))
        self.assertEqual(plan.upstream_group_id, "group-1")
        self.assertEqual(plan.duration_unit, "months")
        # Reimport updates the same product, without duplicating it.
        self.assertEqual(self.import_plan().pk, plan.pk)
        BrandPaymentMethod.objects.create(
            brand=self.brand, payment_type="card_transfer", name="Card"
        )
        PaymentCard.objects.create(
            brand=self.brand,
            bank_name="Test Bank",
            cardholder_name="Test User",
            card_number="0000000000000000",
        )

        async def choose():
            await self.handler.show_subscription_plans(self.callback)
            await self.handler.show_plans_by_category(self.callback, "normal")
            keyboard = self.bot.edit_message_text.call_args.kwargs["reply_markup"]
            self.assertEqual(
                keyboard.inline_keyboard[0][0].callback_data, f"select_plan_{plan.pk}"
            )
            await self.handler.show_plan_details(self.callback, plan.pk)
            self.assertIn(
                "Monthly &lt;VPN&gt;",
                self.bot.edit_message_text.call_args.kwargs["text"],
            )
            await self.handler.initiate_purchase(self.callback, plan.pk)
            self.assertIn(
                "payment_card_transfer_",
                self.bot.edit_message_text.call_args.kwargs["reply_markup"]
                .inline_keyboard[0][0]
                .callback_data,
            )

        async_to_sync(choose)()
        order = Order.objects.get(user=self.user)
        self.assertEqual(
            BotState.objects.get(user=self.user).state_data["step"], "payment_method"
        )
        Wallet.objects.create(
            user=self.user, brand=self.brand, currency="T", balance=Decimal("100000")
        )
        with (
            patch("apps.orders.signals.broadcast_message"),
            patch("apps.subscriptions.tasks.provision_paid_order.delay"),
            patch("apps.referrals.tasks.process_referral_reward.delay"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            pay_order_with_wallet(
                order_id=str(order.order_id),
                user_id=self.user.pk,
                brand_id=self.brand.pk,
            )
        with (
            patch(
                "apps.subscriptions.services.ConnectixProvider",
                side_effect=self.provider_client,
            ),
            patch("utils.message.broadcast_message") as notify,
        ):
            self.assertTrue(provision_paid_order.run(order.pk))
            self.assertFalse(provision_paid_order.run(order.pk))
        self.assertEqual(self.creates, 1)
        subscription = Subscription.objects.get(order=order)
        self.assertEqual(subscription.status, "active")
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")
        self.assertIn(subscription.subscription_url, notify.call_args.kwargs["text"])

    def test_plan_pages_are_bounded_and_keep_category(self):
        plan = self.import_plan()
        for number in range(12):
            plan.pk = None
            plan.name = f"Plan {number}"
            plan.upstream_plan_id = f"plan-{number + 2}"
            plan.save()
        refresh_plan_catalog(self.brand.pk)

        async def browse():
            text, keyboard = await self.handler.get_plans(self.user, category="normal")
            self.assertIn("1 از 2", text)
            self.assertEqual(len(keyboard.inline_keyboard), 12)
            self.assertEqual(
                keyboard.inline_keyboard[-2][0].callback_data, "plans_page_normal_0_2"
            )
            await self.handler.show_plans_page(self.callback, "normal", False, 2)
            self.assertIn("2 از 2", self.bot.edit_message_text.call_args.kwargs["text"])

        async_to_sync(browse)()

    def test_cached_catalog_avoids_database_reads_until_refreshed(self):
        plan = self.import_plan()
        refresh_plan_catalog(self.brand.pk)
        with self.assertNumQueries(0):
            cached = async_to_sync(get_cached_plans)(self.brand.pk)
        self.assertEqual(cached[0].price, Decimal("98000"))
        SubscriptionPlan.objects.filter(pk=plan.pk).update(price=Decimal("110000"))
        with self.assertNumQueries(0):
            cached = async_to_sync(get_cached_plans)(self.brand.pk)
        self.assertEqual(cached[0].price, Decimal("98000"))
        refresh_plan_catalog(self.brand.pk)
        self.assertEqual(
            async_to_sync(get_cached_plans)(self.brand.pk)[0].price, Decimal("110000")
        )

    def test_refresh_hides_plans_not_allowed_for_client_creation(self):
        plan = self.import_plan()
        client = self.provider_client()
        client.client.get_client_metadata = AsyncMock(
            return_value={"seller_plans": [], "groups": []}
        )
        with (
            patch("apps.vpn_providers.tasks._client", return_value=client),
            patch("apps.vpn_providers.tasks.call_command"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            sync_connectix_plans.run(self.provider.pk)
        plan.refresh_from_db()
        self.assertFalse(plan.is_active)
        self.assertFalse(plan.is_visible)
        with self.assertNumQueries(0):
            self.assertEqual(async_to_sync(get_cached_plans)(self.brand.pk), [])

    def test_failed_upstream_refresh_preserves_database_and_cache(self):
        plan = self.import_plan()
        refresh_plan_catalog(self.brand.pk)
        client = self.provider_client()
        client.client.get_seller_plans = AsyncMock(
            side_effect=ConnectixUpstreamError("upstream unavailable")
        )
        with (
            patch("apps.vpn_providers.tasks._client", return_value=client),
            self.assertRaises(ConnectixUpstreamError),
        ):
            sync_connectix_plans.run(self.provider.pk)
        plan.refresh_from_db()
        self.assertTrue(plan.is_active)
        with self.assertNumQueries(0):
            self.assertEqual(
                async_to_sync(get_cached_plans)(self.brand.pk)[0].pk, plan.pk
            )

    def test_no_payment_method_gives_support_response(self):
        plan = self.import_plan()
        async_to_sync(self.handler.initiate_purchase)(self.callback, plan.pk)
        response = self.bot.edit_message_text.call_args.kwargs
        self.assertIn("در حال حاضر روش پرداختی", response["text"])
        self.assertEqual(
            response["reply_markup"].inline_keyboard[0][0].callback_data, "support"
        )

    def test_card_receipt_admin_confirmation_and_connectix_activation(self):
        plan = self.import_plan()
        PaymentCard.objects.create(
            brand=self.brand,
            bank_name="Test & Bank",
            cardholder_name="Test User",
            card_number="0000000000000000",
        )
        BrandPaymentMethod.objects.create(
            brand=self.brand, payment_type="card_transfer", name="Card"
        )
        admin = User.objects.create_user(
            username="test-admin", telegram_id=456, brand=self.brand, is_staff=True
        )
        self.bot.send_photo = AsyncMock()
        self.callback.message.edit_reply_markup = AsyncMock()
        photo = MagicMock()
        photo.photo = [MagicMock(file_id="synthetic-receipt")]
        photo.chat.id = 123
        photo.answer = AsyncMock()
        admin_handler = PurchaseHandler(self.bot, self.brand)
        admin_handler.get_or_create_user = AsyncMock(return_value=(admin, False))

        async def pay():
            await self.handler.initiate_purchase(self.callback, plan.pk)
            order = await Order.objects.aget(user=self.user)
            order_id = str(order.order_id)
            await self.handler.show_card_transfer_payment(self.callback, order_id)
            self.assertIn(
                "Test &amp; Bank", self.bot.edit_message_text.call_args.kwargs["text"]
            )
            await self.handler.payment_done(self.callback, order_id)
            state = await self.handler.get_user_state(self.user)
            await self.handler.handle_photo_message(photo, state)
            payment = await Payment.objects.aget(order=order)
            self.assertEqual(payment.receipt_file, "synthetic-receipt")
            await admin_handler.admin_confirm_payment(self.callback, payment.pk)
            await admin_handler.admin_confirm_payment(self.callback, payment.pk)

        with (
            patch("apps.orders.signals.broadcast_message"),
            patch("apps.subscriptions.tasks.provision_paid_order.delay") as enqueue,
            patch("apps.referrals.tasks.process_referral_reward.delay"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            async_to_sync(pay)()
        order = Order.objects.get(user=self.user)
        self.assertEqual(order.status, "paid")
        enqueue.assert_called_once_with(order.pk)
        self.bot.send_photo.assert_awaited_once()
        with (
            patch(
                "apps.subscriptions.services.ConnectixProvider",
                side_effect=self.provider_client,
            ),
            patch("utils.message.broadcast_message"),
        ):
            self.assertTrue(provision_paid_order.run(order.pk))
        self.assertEqual(self.creates, 1)

    @override_settings(
        TELEGRAM_BOT_TOKEN="env-token",
        CONNECTIX_USERNAME="seller",
        CONNECTIX_PASSWORD="secret",
    )
    def test_env_bootstrap_is_idempotent_and_preserves_disabled_brand(self):
        identity = MagicMock(username="example_bot")
        with patch("apps.brands.management.commands.setup_bot.Bot") as bot_class:
            bot_class.return_value.get_me = AsyncMock(return_value=identity)
            bot_class.return_value.session.close = AsyncMock()
            call_command("setup_bot", stdout=StringIO())
            call_command("setup_bot", stdout=StringIO())
        brand = Brand.objects.get(bot_token="env-token")
        self.assertEqual(brand.vpn_providers.count(), 1)
        brand.status = Brand.BrandStatus.INACTIVE
        brand.save()
        call_command("setup_bot", stdout=StringIO())
        brand.refresh_from_db()
        self.assertEqual(brand.status, Brand.BrandStatus.INACTIVE)
