"""Regression coverage for referral-link visitor lists and private admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram import types
from asgiref.sync import async_to_sync
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.bot.admission import extract_start_invitation, registration_allowed
from apps.bot.handlers.admin_hiddify import HiddifyAdminHandler
from apps.brands.models import Brand
from apps.referrals.admin import ReferralClickAdmin, ReferralLinkAdmin
from apps.referrals.models import Referral, ReferralClick, ReferralLink


class ReferralLinkVisitorAdminTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Visitor brand", slug="visitor-brand", bot_token="visitor-token",
            bot_username="visitor_bot", invite_only_registration=True,
        )
        self.other_brand = Brand.objects.create(
            name="Another visitor brand", slug="another-visitor-brand", bot_token="another-token"
        )
        self.owner = User.objects.create_user(username="visitor-owner", brand=self.brand)
        self.visitor = User.objects.create_user(
            username="visiting-user", telegram_id=45599001, brand=self.brand
        )
        self.previous = User.objects.create_user(
            username="previous-user", telegram_id=45599002, brand=self.brand
        )
        self.foreign = User.objects.create_user(username="foreign-visitor", brand=self.other_brand)
        self.no_reward_link = ReferralLink.objects.create(
            brand=self.brand, user=None, campaign_name="Current users2", code="2fcb3d6b94ba4d66bc12",
        )
        self.reward_link = ReferralLink.objects.create(
            brand=self.brand, user=self.owner, code="OWNEDLINK",
        )
        self.other_link = ReferralLink.objects.create(
            brand=self.other_brand, user=None, code="OTHERLINK",
        )
        ReferralClick.objects.create(
            brand=self.brand, link=self.no_reward_link, visitor=self.visitor,
            is_registration=True,
        )
        ReferralClick.objects.create(
            brand=self.brand, link=self.no_reward_link, visitor=self.previous,
            is_registration=False,
        )
        ReferralClick.objects.create(
            brand=self.other_brand, link=self.other_link, visitor=self.foreign,
            is_registration=True,
        )

    def test_ownerless_and_reward_links_are_valid_when_invite_only(self):
        self.assertTrue(async_to_sync(registration_allowed)(
            self.brand.pk, self.no_reward_link.code,
        ))
        self.assertTrue(async_to_sync(registration_allowed)(
            self.brand.pk, self.reward_link.code,
        ))
        self.assertFalse(async_to_sync(registration_allowed)(self.brand.pk, None))
        self.assertFalse(async_to_sync(registration_allowed)(self.brand.pk, "INVALID"))
        self.assertFalse(async_to_sync(registration_allowed)(self.brand.pk, "OTHERLINK"))
        self.no_reward_link.is_active = False
        self.no_reward_link.save(update_fields=["is_active"])
        self.assertFalse(async_to_sync(registration_allowed)(
            self.brand.pk, self.no_reward_link.code,
        ))

    def test_start_parser_accepts_deeplink_payload_and_checks_bot_mention(self):
        bot = MagicMock()
        bot.get_me = AsyncMock(return_value=SimpleNamespace(username="visitor_bot"))
        def message(text):
            return types.Message(
                message_id=1, date=1700000000,
                chat=types.Chat(id=101, type="private"), text=text,
            )
        self.assertEqual(
            async_to_sync(extract_start_invitation)(
                message("/start 2fcb3d6b94ba4d66bc12"), bot,
            ), "2fcb3d6b94ba4d66bc12",
        )
        self.assertEqual(
            async_to_sync(extract_start_invitation)(
                message("/start@VISITOR_BOT OWNEDLINK"), bot,
            ), "OWNEDLINK",
        )
        self.assertIsNone(async_to_sync(extract_start_invitation)(
            message("/start@another_bot OWNEDLINK"), bot,
        ))
        self.assertIsNone(async_to_sync(extract_start_invitation)(
            message("/help OWNEDLINK"), bot,
        ))

    def test_telegram_admin_lists_visitors_for_correct_link_only(self):
        handler = HiddifyAdminHandler(MagicMock(), self.brand)
        handler.control_screen = AsyncMock()
        async_to_sync(handler.control_link_visits)(MagicMock(), self.no_reward_link.pk)
        args = handler.control_screen.call_args
        text, rows = args.args[1], args.args[2]
        self.assertIn("Current users2", text)
        self.assertIn("تعداد یکتا: 2", text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {r[0]["callback_data"].split(":")[3] for r in rows},
            {str(self.visitor.pk), str(self.previous.pk)},
        )
        self.assertIn("🆕", rows[1][0]["text"])
        self.assertIn("👤", rows[0][0]["text"])

    def test_django_admin_shows_real_visit_count_and_filtered_visits(self):
        site = AdminSite()
        link_admin = ReferralLinkAdmin(ReferralLink, site)
        visit_admin = ReferralClickAdmin(ReferralClick, site)
        superuser = User.objects.create_superuser(
            username="visitor-superadmin", password="test-pass",
        )
        request = RequestFactory().get("/admin/referrals/referrallink/")
        request.user = superuser
        row = link_admin.get_queryset(request).get(pk=self.no_reward_link.pk)
        self.assertEqual(link_admin.unique_visits(row), 2)
        url = str(link_admin.visit_list(row))
        self.assertIn("link__id__exact", url)
        self.assertIn(str(self.no_reward_link.pk), url)
        self.assertIn(
            reverse("admin:referrals_referralclick_changelist"), url,
        )
        self.assertEqual(visit_admin.get_queryset(request).filter(
            link_id=self.no_reward_link.pk,
        ).count(), 2)
        brand_admin = User.objects.create_user(username="scoped-visit-admin")
        brand_admin.admin_brands.add(self.brand)
        request.user = brand_admin
        self.assertEqual(visit_admin.get_queryset(request).count(), 2)
        self.assertEqual(link_admin.get_queryset(request).count(), 2)
        self.assertFalse(visit_admin.get_queryset(request).filter(
            link_id=self.other_link.pk,
        ).exists())
        self.assertFalse(Referral.objects.filter(
            referral_link=self.no_reward_link,
        ).exists())
