"""Automatic deletion of inviter-only join messages ten hours after delivery."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.referrals.models import GamificationNotification
from apps.referrals.tasks import delete_expired_referrer_join_messages


class ReferrerJoinMessageDeletionTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Delete join test", slug=f"join-expiry-{uuid.uuid4().hex[:8]}",
            bot_token="123456:ABCDEF", currency="T",
        )
        self.user = User.objects.create_user(
            username=f"inviter-{uuid.uuid4().hex[:6]}",
            telegram_id=818181, brand=self.brand,
        )

    def notice(self, *, age_hours, event="referral_joined", message_id=99):
        return GamificationNotification.objects.create(
            user=self.user, brand=self.brand, notification_type="custom",
            dedupe_key=f"test-{uuid.uuid4().hex}",
            sent_at=timezone.now() - timedelta(hours=age_hours),
            metadata={"event": event, "telegram_message_id": message_id},
        )

    @patch("apps.referrals.tasks.Bot")
    def test_only_join_notices_older_than_ten_hours_are_deleted(self, bot_class):
        older = self.notice(age_hours=10.1, message_id=301)
        recent = self.notice(age_hours=9.9, message_id=302)
        other = self.notice(age_hours=12, event="referral_purchase", message_id=303)
        bot = MagicMock()
        bot.delete_message = AsyncMock(return_value=True)
        bot.session.close = AsyncMock()
        bot_class.return_value = bot

        self.assertEqual(delete_expired_referrer_join_messages(), 1)
        bot.delete_message.assert_awaited_once_with(chat_id=818181, message_id=301)
        older.refresh_from_db()
        recent.refresh_from_db()
        other.refresh_from_db()
        self.assertIn("auto_deleted_at", older.metadata)
        self.assertNotIn("auto_deleted_at", recent.metadata)
        self.assertNotIn("auto_deleted_at", other.metadata)

        self.assertEqual(delete_expired_referrer_join_messages(), 0)
        bot.delete_message.assert_awaited_once()

    @patch("apps.referrals.tasks.Bot")
    def test_temporary_telegram_failure_can_retry(self, bot_class):
        notice = self.notice(age_hours=11)
        bot = MagicMock()
        bot.delete_message = AsyncMock(side_effect=RuntimeError("temporary"))
        bot.session.close = AsyncMock()
        bot_class.return_value = bot

        self.assertEqual(delete_expired_referrer_join_messages(), 0)
        notice.refresh_from_db()
        self.assertIn("auto_delete_last_failed_at", notice.metadata)
        self.assertNotIn("auto_delete_claimed_at", notice.metadata)

        bot.delete_message = AsyncMock(return_value=True)
        self.assertEqual(delete_expired_referrer_join_messages(), 1)
        notice.refresh_from_db()
        self.assertIn("auto_deleted_at", notice.metadata)

    @patch("apps.referrals.tasks.Bot")
    def test_missing_message_id_is_marked_terminal(self, bot_class):
        notice = self.notice(age_hours=11, message_id=None)
        self.assertEqual(delete_expired_referrer_join_messages(), 0)
        notice.refresh_from_db()
        self.assertIn("auto_delete_terminal_at", notice.metadata)
        bot_class.assert_not_called()
