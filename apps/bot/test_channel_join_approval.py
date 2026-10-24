"""Regression tests for mandatory-channel join requests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.accounts.models import User, UserProfile
from apps.bot.channel_join import approve_required_channel_join, is_required_channel
from apps.brands.models import Brand, BrandConfiguration


class RequiredChannelJoinApprovalTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Join brand", slug="join-brand", bot_token="123:dummy",
            contact_email="join@example.invalid",
        )
        self.config = BrandConfiguration.objects.create(
            brand=self.brand,
            custom_fields={"required_channel_id": -1001234567890},
        )
        self.user = User.objects.create_user(
            username="join-user", telegram_id=12345, brand=self.brand
        )
        self.profile = UserProfile.objects.create(
            user=self.user, channel_join_required=True,
            channel_join_verified=False,
        )

    def request(self, *, channel_id=-1001234567890, username=None, telegram_id=12345):
        return SimpleNamespace(
            chat=SimpleNamespace(id=channel_id, username=username),
            from_user=SimpleNamespace(id=telegram_id),
            approve=AsyncMock(return_value=True),
        )

    def approve(self, req):
        return async_to_sync(approve_required_channel_join)(
            req, brand_id=self.brand.pk
        )

    def test_registered_user_is_approved_without_extra_opt_in_setting(self):
        req = self.request()
        self.assertTrue(self.approve(req))
        req.approve.assert_awaited_once_with()
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.channel_join_verified)

    def test_does_not_approve_other_channels(self):
        req = self.request(channel_id=-1009999999999)
        self.assertFalse(self.approve(req))
        req.approve.assert_not_awaited()
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.channel_join_verified)

    def test_does_not_approve_unknown_or_inactive_accounts(self):
        req = self.request(telegram_id=99999)
        self.assertFalse(self.approve(req))
        req.approve.assert_not_awaited()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        req = self.request()
        self.assertFalse(self.approve(req))
        req.approve.assert_not_awaited()

    def test_does_not_approve_other_brands_user(self):
        other = Brand.objects.create(
            name="Other join brand", slug="other-join-brand", bot_token="456:dummy",
            contact_email="other@example.invalid",
        )
        User.objects.create_user(
            username="outsider", telegram_id=99999, brand=other
        )
        req = self.request(telegram_id=99999)
        self.assertFalse(self.approve(req))
        req.approve.assert_not_awaited()

    def test_accepts_configured_channel_username(self):
        self.config.custom_fields = {"required_channel_id": "@PrivateChannel"}
        self.config.save(update_fields=["custom_fields"])
        req = self.request(channel_id=-1001234567890, username="privatechannel")
        self.assertTrue(self.approve(req))
        req.approve.assert_awaited_once_with()

    def test_api_error_does_not_mark_verified(self):
        req = self.request()
        req.approve.side_effect = RuntimeError("Telegram API failed")
        self.assertFalse(self.approve(req))
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.channel_join_verified)

    def test_missing_channel_configuration_never_approves(self):
        self.config.custom_fields = {}
        self.config.save(update_fields=["custom_fields"])
        req = self.request()
        self.assertFalse(self.approve(req))
        req.approve.assert_not_awaited()

    def test_numeric_id_requires_exact_channel_and_username_is_case_insensitive(self):
        self.assertTrue(is_required_channel(SimpleNamespace(id=-100123, username=None), "-100123"))
        self.assertFalse(is_required_channel(SimpleNamespace(id=-100123, username="x"), "-100124"))
        self.assertTrue(is_required_channel(SimpleNamespace(id=-100123, username="X"), "@x"))
        self.assertFalse(is_required_channel(SimpleNamespace(id=-100123, username="x"), None))
