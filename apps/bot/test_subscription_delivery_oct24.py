"""Subscription delivery and category-policy checks for the consolidated rollout."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from apps.bot.handlers.subscriptions import SubscriptionHandler
from apps.subscriptions.policy import blocked_duration_key, temporary_duration_blocked


class DurationPolicyTests(SimpleTestCase):
    @override_settings(TEMP_DISABLE_2_3_MONTH_PLANS=True)
    def test_normal_month_two_allowed_three_blocked_royal_two_and_three_enabled(self):
        for category, months, expected in (("normal", 2, False), ("normal", 3, True),
                                            ("royal", 2, False), ("royal", 3, False)):
            plan = SimpleNamespace(service_category=category, duration_unit="months", duration_value=months)
            self.assertEqual(temporary_duration_blocked(plan), expected)
        self.assertFalse(blocked_duration_key("m2", "normal"))
        self.assertTrue(blocked_duration_key("m3", "normal"))
        self.assertFalse(blocked_duration_key("m2", "royal"))
        self.assertFalse(blocked_duration_key("m3", "royal"))


class ConnectixDeliveryTests(SimpleTestCase):
    def test_account_delivery_has_rename_navigation_and_v2ray_link_only_here(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()
        handler = SubscriptionHandler(bot, SimpleNamespace(pk=1))
        handler.generate_qr_code = lambda url: b"test-png"
        handler.create_keyboard = lambda rows: rows
        callback = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=99)), answer=AsyncMock())
        subscription = SimpleNamespace(
            pk=15, display_name="آیفون خودم", connectix_username="test-user",
            connectix_password="pass-123", subscription_url="https://example.test/v2ray",
            plan=SimpleNamespace(name="Plan"),
        )
        async_to_sync(handler.send_connectix_config)(callback, subscription, None)
        text = bot.send_message.await_args.args[1]
        self.assertIn("🔑 رمز عبور", text)
        self.assertIn("با این نام کاربری و رمز عبور وارد برنامه ما،کانکتیکس شوید", text)
        self.assertIn("pass-123", text)
        self.assertIn("V2Ray", text)
        self.assertIn("https://example.test/v2ray", text)
        keyboard = bot.send_message.await_args.kwargs["reply_markup"]
        callbacks = [button["callback_data"] for row in keyboard for button in row]
        self.assertEqual(callbacks, ["rename_subscription_15", "main_menu",
                                     "useful_content", "useful_category_install",
                                     "useful_category_downloads"])
        self.assertEqual(keyboard[0][0]["style"], "success")
        bot.send_photo.assert_awaited_once()
        # The menu must be the last bot message, below the QR photo.
        self.assertEqual(bot.send_message.await_count, 1)
