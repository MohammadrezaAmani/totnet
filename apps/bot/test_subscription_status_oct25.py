"""Expired and historic subscriptions remain browseable without data destruction."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from django.utils import timezone

from apps.bot.handlers.subscriptions import SubscriptionHandler
from apps.subscriptions.usage import is_subscription_expired


class SubscriptionStatusTests(SimpleTestCase):
    def make_sub(self, *, status="active", expires_at=None, used=Decimal("0"), limit=Decimal("30")):
        return SimpleNamespace(
            status=status, expires_at=expires_at, traffic_used_gb=used,
            traffic_limit_gb=limit, vpn_provider=SimpleNamespace(provider_type="hiddify"),
            remote_account=None,
        )

    def test_expired_by_time_or_known_exhausted_volume(self):
        now = timezone.now()
        self.assertTrue(is_subscription_expired(self.make_sub(
            expires_at=now-timedelta(seconds=1))))
        self.assertTrue(is_subscription_expired(self.make_sub(used=Decimal("30"))))
        self.assertFalse(is_subscription_expired(self.make_sub(
            expires_at=now+timedelta(days=2), used=Decimal("10"))))
        self.assertTrue(is_subscription_expired(self.make_sub(status="expired")))

    def test_legacy_usage_callback_returns_subscription_picker(self):
        handler = SubscriptionHandler(MagicMock(), SimpleNamespace(pk=1))
        handler.show_my_subscriptions = AsyncMock()
        callback = MagicMock()
        async_to_sync(handler.show_usage_statistics)(callback, 42)
        handler.show_my_subscriptions.assert_awaited_once_with(callback)
