"""Regression coverage for referral /start attribution before profile setup."""

from unittest.mock import AsyncMock, MagicMock

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase, override_settings
from io import StringIO
from aiogram import types

from apps.accounts.models import User
from apps.bot.handlers.start import StartHandler
from apps.brands.models import Brand
from apps.referrals.models import Referral, ReferralClick, ReferralLink, ReferralProgram
from apps.referrals.services import attribute_referral


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ReferralStartFixTests(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(name="Referral fixes", slug="referral-fixes", bot_token="demo", bot_username="demo_bot")
        self.inviter = User.objects.create_user(username="inviter-demo", telegram_id=10001, brand=self.brand)
        self.link = ReferralLink.objects.create(user=self.inviter, brand=self.brand, code="L436ZP00")
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        self.handler = StartHandler(self.bot, self.brand)

    def _new_user(self, telegram_id=10002):
        return async_to_sync(self.handler.get_or_create_user)(
            types.User(id=telegram_id, is_bot=False, first_name="Referee"),
            referral_code="L436ZP00",
        )

    def test_first_signup_registers_without_phone_even_if_program_requires_it(self):
        ReferralProgram.objects.create(
            brand=self.brand, name="Referral", require_phone_verification=True,
            max_referrals_per_day=1, max_referrals_per_month=1,
        )
        user, created = self._new_user()
        self.assertTrue(created)
        outcome = async_to_sync(self.handler.process_referral_safely)(user, self.link.code, created)
        self.assertEqual(outcome, "registered")
        user.refresh_from_db()
        self.inviter.refresh_from_db()
        self.assertEqual(user.referred_by_id, self.inviter.pk)
        self.assertEqual(Referral.objects.filter(referee=user).count(), 1)
        self.assertEqual(self.inviter.referral_count, 1)
        self.assertEqual(self.link.visits.count(), 1)
        self.assertTrue(self.link.visits.get().is_registration)
        self.assertEqual(async_to_sync(self.handler.process_referral_safely)(user, self.link.code, False), "already_registered")
        self.assertEqual(Referral.objects.filter(referee=user).count(), 1)

    def test_postgresql_locks_link_without_locking_nullable_owner(self):
        # The link owner FK is nullable; select_related("user") produces a
        # LEFT OUTER JOIN. PostgreSQL only supports FOR UPDATE OF the link.
        referee, created = self._new_user()
        self.assertTrue(created)
        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(attribute_referral(
                user_id=referee.pk, brand_id=self.brand.pk, code=self.link.code,
            ))
        referee.refresh_from_db()
        self.assertEqual(referee.referred_by_id, self.inviter.pk)
        self.assertEqual(Referral.objects.filter(referee=referee).count(), 1)
        if connection.vendor == "postgresql":
            link_queries = [
                q["sql"] for q in queries.captured_queries
                if 'FROM "referral_links"' in q["sql"] and 'FOR UPDATE' in q["sql"]
            ]
            self.assertEqual(len(link_queries), 1)
            self.assertIn('FOR UPDATE OF "referral_links"', link_queries[0])

    def test_nullable_unowned_link_fails_without_db_error(self):
        referee, created = self._new_user()
        self.assertTrue(created)
        ReferralLink.objects.create(user=None, brand=self.brand, code="NOOWNER")
        self.assertFalse(attribute_referral(
            user_id=referee.pk, brand_id=self.brand.pk, code="NOOWNER",
        ))
        self.assertIsNone(User.objects.get(pk=referee.pk).referred_by_id)
        self.assertFalse(Referral.objects.filter(referee=referee).exists())

    def test_second_user_is_recorded_even_when_daily_reward_cap_is_one(self):
        ReferralProgram.objects.create(brand=self.brand, name="Capped", max_referrals_per_day=1)
        for i in (10002, 10003):
            user, created = self._new_user(i)
            self.assertEqual(async_to_sync(self.handler.process_referral_safely)(user, self.link.code, created), "registered")
        self.assertEqual(Referral.objects.filter(referrer=self.inviter).count(), 2)

    def test_existing_account_cannot_be_referred_by_a_new_deeplink(self):
        existing = User.objects.create_user(username="existing-demo", telegram_id=10003, brand=self.brand)
        self.assertEqual(async_to_sync(self.handler.process_referral_safely)(existing, self.link.code, False), "existing_account")
        existing.refresh_from_db()
        self.assertIsNone(existing.referred_by_id)
        self.assertFalse(Referral.objects.filter(referee=existing).exists())

    def test_can_repair_proven_first_touch_but_not_switch_link(self):
        other = User.objects.create_user(username="other-demo", telegram_id=10004, brand=self.brand)
        other_link = ReferralLink.objects.create(user=other, brand=self.brand, code="OTHER123")
        user, created = self._new_user()
        self.assertTrue(created)
        from apps.referrals.services import track_referral_click
        track_referral_click(code=other_link.code, brand_id=self.brand.pk, visitor_id=user.pk, is_registration=True)
        self.assertEqual(async_to_sync(self.handler.process_referral_safely)(user, self.link.code, True), "existing_account")
        self.assertIsNone(User.objects.get(pk=user.pk).referred_by_id)

    def test_repair_existing_first_registration_click(self):
        from apps.referrals.services import track_referral_click
        user, created = self._new_user()
        track_referral_click(code=self.link.code, brand_id=self.brand.pk, visitor_id=user.pk, is_registration=True)
        out = StringIO()
        call_command("audit_referral_deeplink", code="L436ZP00", stdout=out)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIsNone(User.objects.get(pk=user.pk).referred_by_id)
        call_command("audit_referral_deeplink", code="L436ZP00", apply=True, stdout=StringIO())
        self.assertEqual(User.objects.get(pk=user.pk).referred_by_id, self.inviter.pk)
        self.assertEqual(Referral.objects.filter(referee=user).count(), 1)
        call_command("audit_referral_deeplink", code="L436ZP00", apply=True, stdout=StringIO())
        self.assertEqual(Referral.objects.filter(referee=user).count(), 1)

    def test_invalid_and_ownerless_links_report_failure(self):
        user, created = self._new_user()
        self.assertEqual(async_to_sync(self.handler.process_referral_safely)(user, "BOGUS", True), "invalid_link")
        ReferralLink.objects.create(user=None, brand=self.brand, code="CAMPAIGNX")
        self.assertEqual(async_to_sync(self.handler.process_referral_safely)(user, "CAMPAIGNX", True), "unowned_link")
        self.assertIsNone(User.objects.get(pk=user.pk).referred_by_id)

    def test_joined_count_is_visible_before_any_purchase(self):
        from apps.bot.handlers.referrals import ReferralsHandler
        user, created = self._new_user()
        self.assertEqual(async_to_sync(self.handler.process_referral_safely)(user, self.link.code, created), "registered")
        handler = ReferralsHandler(self.bot, self.brand)
        handler.get_or_create_user = AsyncMock(return_value=(self.inviter, False))
        handler.edit_message_with_keyboard = AsyncMock()
        callback = MagicMock()
        callback.message.chat.id = 10001
        callback.message.message_id = 9
        callback.answer = AsyncMock()
        async_to_sync(handler.show_referral_stats)(callback)
        text = handler.edit_message_with_keyboard.call_args.args[2]
        self.assertIn("عضوشده با لینک شما: 1", text)
        self.assertIn("دارای خرید تأییدشده: 0", text)

    def test_legacy_user_referral_code_is_resolved_and_materialized(self):
        legacy_code = self.inviter.referral_code
        self.link.delete()
        self.assertFalse(ReferralLink.objects.filter(code=legacy_code).exists())
        user, created = async_to_sync(self.handler.get_or_create_user)(
            types.User(id=10007, is_bot=False, first_name="New"),
            referral_code=legacy_code,
        )
        self.assertTrue(created)
        self.assertEqual(
            async_to_sync(self.handler.process_referral_safely)(user, legacy_code, created),
            "registered",
        )
        self.assertEqual(ReferralLink.objects.get(code=legacy_code).user_id, self.inviter.pk)
        self.assertEqual(User.objects.get(pk=user.pk).referred_by_id, self.inviter.pk)
