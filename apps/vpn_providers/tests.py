import inspect
import json

import httpx
from django.test import SimpleTestCase

from apps.vpn_providers.services.base import VPNProviderFactory
from apps.vpn_providers.services.connectix import ConnectixProvider
from apps.vpn_providers.services.connectix_client import (
    ConnectixAuthenticationError,
    ConnectixClient,
    ConnectixMalformedResponse,
    ConnectixUpstreamError,
)
from apps.vpn_providers.services.connectix import ConnectixProvisioningRequest
from apps.vpn_providers.services.capabilities import capabilities_for


class VPNProviderFactoryTests(SimpleTestCase):
    def test_capabilities_fail_closed_and_match_verified_connectix_surface(self):
        connectix = capabilities_for("connectix")
        self.assertTrue(connectix.provision)
        self.assertTrue(connectix.reconcile)
        self.assertTrue(connectix.sync_status)
        self.assertFalse(connectix.sync_usage)
        self.assertFalse(connectix.renew)
        self.assertFalse(connectix.suspend)
        self.assertFalse(capabilities_for("marzban").provision)

    def test_implemented_providers_are_constructible(self):
        for provider_type, kwargs in (
            (
                "hiddify",
                {"base_url": "https://example.invalid", "api_key": "test-key"},
            ),
            (
                "pasarguard",
                {"base_url": "https://example.invalid", "api_key": "test-key"},
            ),
            (
                "connectix",
                {
                    "base_url": "https://api.connectix.vip",
                    "username": "seller@example.invalid",
                    "password": "test-password",
                },
            ),
        ):
            with self.subTest(provider_type=provider_type):
                provider = VPNProviderFactory.create(provider_type, **kwargs)
                if provider_type == "connectix":
                    self.assertIsInstance(provider, ConnectixProvider)
                else:
                    self.assertEqual(provider.base_url, "https://example.invalid")

    def test_unimplemented_enum_provider_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "not implemented or registered"):
            VPNProviderFactory.create(
                "marzban",
                base_url="https://example.invalid",
                api_key="test-key",
            )

    def test_beat_references_importable_synchronous_tasks(self):
        from config.celery import app

        for entry in app.conf.beat_schedule.values():
            task_module, task_name = entry["task"].rsplit(".", 1)
            with self.subTest(task=entry["task"]):
                module = __import__(task_module, fromlist=[task_name])
                task = getattr(module, task_name)
                self.assertFalse(inspect.iscoroutinefunction(task.run))


class ConnectixClientContractTests(SimpleTestCase):
    def make_client(self, handler):
        return ConnectixClient(
            base_url="https://api.connectix.vip",
            username="seller@example.invalid",
            password="test-password",
            transport=httpx.MockTransport(handler),
        )

    async def test_login_and_seller_data_use_bearer_token(self):
        calls = []

        async def handler(request):
            calls.append(request)
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(200, json={"token": "test-token", "seller": {}})
            return httpx.Response(200, json={"seller": {"short_id": "abc"}})

        client = self.make_client(handler)
        try:
            seller = await client.get_seller_data()
        finally:
            await client.close()

        self.assertEqual(seller["seller"]["short_id"], "abc")
        self.assertEqual(calls[0].method, "POST")
        self.assertEqual(calls[0].url.path, "/v1/seller/auth/login")
        self.assertEqual(calls[1].headers["Authorization"], "Bearer test-token")

    async def test_client_list_parses_pagination_and_redacts_secret_repr(self):
        async def handler(request):
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(200, json={"token": "test-token"})
            return httpx.Response(
                200,
                json={
                    "total_clients": 1,
                    "clients": {
                        "current_page": 1,
                        "last_page": 1,
                        "total": 1,
                        "data": [
                            {
                                "id": "remote-id",
                                "username": "local-user",
                                "password": "private-test-password",
                                "subscription_link": "https://sub.example.invalid/secret",
                                "is_active": 1,
                            }
                        ],
                    },
                },
            )

        client = self.make_client(handler)
        try:
            page = await client.get_clients()
        finally:
            await client.close()

        self.assertEqual(page.total, 1)
        self.assertEqual(page.clients[0].remote_id, "remote-id")
        self.assertNotIn("private-test-password", repr(page.clients[0]))
        self.assertNotIn("sub.example.invalid", repr(page.clients[0]))

    async def test_expired_session_reauthenticates_safe_get(self):
        login_count = 0

        async def handler(request):
            nonlocal login_count
            if request.url.path.endswith("/auth/login"):
                login_count += 1
                return httpx.Response(200, json={"token": f"token-{login_count}"})
            if request.headers.get("Authorization") == "Bearer token-1":
                return httpx.Response(401, json={"error": "expired"})
            return httpx.Response(200, json={"seller": {"short_id": "abc"}})

        client = self.make_client(handler)
        try:
            result = await client.get_seller_data()
        finally:
            await client.close()
        self.assertEqual(login_count, 2)
        self.assertIn("seller", result)

    async def test_create_is_not_retried_after_upstream_failure(self):
        create_calls = 0

        async def handler(request):
            nonlocal create_calls
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(200, json={"token": "test-token"})
            if request.url.path.endswith("/clients/store"):
                create_calls += 1
                return httpx.Response(503, json={"error": "unavailable"})
            return httpx.Response(404, json={})

        client = self.make_client(handler)
        try:
            with self.assertRaises(ConnectixUpstreamError):
                await client.create_client({"id": None})
        finally:
            await client.close()
        self.assertEqual(create_calls, 1)

    async def test_provider_creates_from_mapped_plan_without_retaining_password(self):
        submitted_payloads = []

        async def handler(request):
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(200, json={"token": "test-token"})
            if request.url.path.endswith("/seller-data"):
                return httpx.Response(200, json={"seller": {"short_id": "abc"}})
            if request.url.path.endswith("/clients/store"):
                submitted_payloads.append(request.read().decode())
                return httpx.Response(200, json={"client_id": 123})
            return httpx.Response(404, json={})

        provider = ConnectixProvider(
            base_url="https://api.connectix.vip",
            username="seller@example.invalid",
            password="test-password",
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await provider.create_account(
                ConnectixProvisioningRequest(
                    subscription_id="local-subscription",
                    display_name="Test User",
                    email="user@example.invalid",
                    telegram_id=123,
                    plan_id="plan-1",
                    plan_name="Monthly Test",
                    group_id="group-1",
                    group_name="Test Group",
                    count_of_devices=3,
                )
            )
        finally:
            await provider.close()

        self.assertEqual(result.remote_id, "123")
        self.assertEqual(result.username[:3], "abc")
        self.assertNotIn("password=", repr(result))
        self.assertEqual(len(submitted_payloads), 1)
        self.assertEqual(json.loads(submitted_payloads[0])["count_of_devices"], 3)

    async def test_bad_login_and_malformed_response_are_explicit(self):
        login_client = self.make_client(
            lambda request: httpx.Response(401, json={"error": "bad credentials"})
        )
        try:
            with self.assertRaises(ConnectixAuthenticationError):
                await login_client.get_seller_data()
        finally:
            await login_client.close()

        malformed_client = self.make_client(
            lambda request: httpx.Response(200, content=b"not-json")
        )
        try:
            with self.assertRaises(ConnectixMalformedResponse):
                await malformed_client.get_seller_data()
        finally:
            await malformed_client.close()
