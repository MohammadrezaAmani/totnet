from html import unescape
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.bot.handlers.purchase import PurchaseHandler
from apps.bot.handlers.referrals import ReferralsHandler
from apps.bot.handlers.rewards import RewardsHandler
from apps.bot.models import BotState
from apps.brands.models import Brand
from apps.referrals.models import (
    MarketingMaterial,
    Referral,
    ReferralLink,
    ReferralReward,
    RewardAccount,
    RewardPointLedger,
    RewardService,
)
from apps.subscriptions.models import SubscriptionPlan


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class RequestedBotFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(
            name="Test",
            slug="requested-flows",
            bot_token="test-token",
            bot_username="example_bot",
            contact_email="test@example.invalid",
        )
        self.user = User.objects.create_user(
            username="test-user", brand=self.brand, telegram_id=123
        )
        self.bot = MagicMock()
        self.bot.edit_message_text = AsyncMock()
        self.bot.send_photo = AsyncMock()
        self.bot.send_video = AsyncMock()
        self.callback = MagicMock()
        self.callback.message.chat.id = 123
        self.callback.message.message_id = 42
        self.callback.answer = AsyncMock()

    def handler(self, handler_class):
        handler = handler_class(self.bot, self.brand)
        handler.get_or_create_user = AsyncMock(return_value=(self.user, False))
        return handler

    def test_purchase_always_starts_with_service_menu(self):
        handler = self.handler(PurchaseHandler)
        for has_plan in (False, True):
            with self.subTest(has_plan=has_plan):
                if has_plan:
                    SubscriptionPlan.objects.create(
                        brand=self.brand,
                        name="Only normal plan",
                        plan_type="time_based",
                        price=10,
                    )
                async_to_sync(handler.show_subscription_plans)(self.callback)
                keyboard = self.bot.edit_message_text.call_args.kwargs["reply_markup"]
                callbacks = {
                    button.callback_data
                    for row in keyboard.inline_keyboard
                    for button in row
                }
                self.assertTrue(
                    {
                        "purchase_category_normal",
                        "purchase_category_royal",
                        "purchase_category_iran_ip",
                        "purchase_special",
                        "service_guide",
                    }
                    <= callbacks
                )
                self.assertFalse(
                    any(value.startswith("select_plan_") for value in callbacks)
                )
                self.assertEqual(
                    BotState.objects.get(user=self.user).state_data["step"],
                    "service_selection",
                )

    def test_referral_media_captions_keep_personal_link_when_truncated(self):
        ReferralLink.objects.create(user=self.user, brand=self.brand, code="MYCODE")
        handler = self.handler(ReferralsHandler)
        for material_type, field, sender in (
            ("banner", "image", self.bot.send_photo),
            ("video", "video", self.bot.send_video),
        ):
            with self.subTest(material_type=material_type):
                material = MarketingMaterial.objects.create(
                    brand=self.brand,
                    name="Helper",
                    material_type=material_type,
                    content="<hello> " + "😀" * 1200,
                    **{field: "marketing/test-file"},
                )
                with patch(
                    "apps.bot.handlers.referrals.os.path.isfile", return_value=True
                ):
                    async_to_sync(handler.show_referral_material)(
                        self.callback, material.pk
                    )
                caption = sender.call_args.kwargs["caption"]
                self.assertIn("https://t.me/example_bot?start=MYCODE", caption)
                self.assertIn("&lt;hello&gt;", caption)
                self.assertLessEqual(
                    len(unescape(caption).encode("utf-16-le")) // 2, 1024
                )
                self.assertEqual(sender.call_args.kwargs["parse_mode"], "HTML")

    def test_referral_text_contains_personal_link(self):
        ReferralLink.objects.create(user=self.user, brand=self.brand, code="TEXTCODE")
        material = MarketingMaterial.objects.create(
            brand=self.brand,
            name="Suggested text",
            material_type="text_template",
            content="Invite your friends",
        )
        async_to_sync(self.handler(ReferralsHandler).show_referral_material)(
            self.callback, material.pk
        )
        text = self.bot.edit_message_text.call_args.kwargs["text"]
        self.assertIn("Invite your friends", text)
        self.assertIn("https://t.me/example_bot?start=TEXTCODE", text)

    def test_spent_points_excludes_earned_pending_and_other_users(self):
        plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Reward plan", plan_type="time_based", price=10
        )
        service = RewardService.objects.create(brand=self.brand, plan=plan)
        account = RewardAccount.objects.create(
            user=self.user, brand=self.brand, lifetime_points=20
        )
        for entry_type, delta in (
            ("redeemed", -2),
            ("converted_to_wallet", -4),
            ("earned", 20),
            ("service_rebase", -8),
        ):
            RewardPointLedger.objects.create(
                account=account,
                service=service,
                entry_type=entry_type,
                points_delta=delta,
                value_delta=0,
                point_value_snapshot=1,
                idempotency_key=entry_type,
            )
        other = User.objects.create_user(username="other-user", brand=self.brand)
        other_account = RewardAccount.objects.create(user=other, brand=self.brand)
        RewardPointLedger.objects.create(
            account=other_account,
            service=service,
            entry_type="redeemed",
            points_delta=-100,
            value_delta=0,
            point_value_snapshot=1,
            idempotency_key="other-user",
        )
        link = ReferralLink.objects.create(
            user=self.user, brand=self.brand, code="REWARDCODE"
        )
        referral = Referral.objects.create(
            referrer=self.user, referee=other, brand=self.brand, referral_link=link
        )
        for status, cashed_out, amount in (
            ("processed", True, 3),
            ("processed", False, 1),
            ("pending", True, 30),
            ("cancelled", True, 60),
        ):
            ReferralReward.objects.create(
                referral=referral,
                user=self.user,
                brand=self.brand,
                reward_type="normal_point",
                amount=amount,
                status=status,
                is_cashed_out=cashed_out,
            )
        async_to_sync(self.handler(RewardsHandler).show_rewards)(self.callback)
        text = self.bot.edit_message_text.call_args.kwargs["text"]
        self.assertIn("تمام امتیازات خرج شده توسط شما تا الان:</b> 9", text)
        self.assertIn("امتیازهای موجود در جعبه فعلی: 1", text)
        self.assertIn("قرص‌های نقد: 1", text)
