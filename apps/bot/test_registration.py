from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, types
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageReplyMarkup, GetMe, SendMessage
from asgiref.sync import async_to_sync
from django.contrib.admin.sites import AdminSite
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User, UserProfile
from apps.bot.admission import InvitationRequired
from apps.bot.dispatcher import MultiBrandDispatcher
from apps.bot.handlers.profile import ProfileHandler
from apps.bot.handlers.purchase import PurchaseHandler
from apps.bot.handlers.start import StartHandler
from apps.bot.models import BotState
from apps.brands.admin import BrandAdmin
from apps.brands.models import Brand
from apps.referrals.models import Referral, ReferralLink
from apps.subscriptions.models import SubscriptionPlan


class RecordingSession(BaseSession):
    """Exercise real dispatcher routes without sending Telegram messages."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.next_id = 100

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return types.User(
                id=999, is_bot=True, first_name="Test", username="example_bot"
            )
        if method.__returning__ is types.Message:
            self.next_id += 1
            return types.Message(
                message_id=self.next_id,
                date=datetime.now(timezone.utc),
                chat=types.Chat(id=method.chat_id, type="private"),
                text=getattr(method, "text", None),
                reply_markup=getattr(method, "reply_markup", None)
                if isinstance(
                    getattr(method, "reply_markup", None), types.InlineKeyboardMarkup
                )
                else None,
            )
        return True


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class RegistrationFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(
            name="Registration",
            slug="registration",
            bot_token="token",
            contact_email="test@example.invalid",
            bot_username="example_bot",
            invite_only_registration=True,
        )
        self.owner = User.objects.create_user(
            username="owner", telegram_id=1, brand=self.brand
        )
        self.link = ReferralLink.objects.create(
            user=self.owner, brand=self.brand, code="VALID"
        )
        self.session = RecordingSession()
        self.bot = Bot("999:synthetic-token", session=self.session)
        self.handlers = {
            "start": StartHandler(self.bot, self.brand),
            "profile": ProfileHandler(self.bot, self.brand),
            "purchase": PurchaseHandler(self.bot, self.brand),
        }
        self.dp = Dispatcher()
        async_to_sync(MultiBrandDispatcher().setup_brand_routes)(
            self.dp, self.brand, self.handlers
        )

    def feed(self, *, text=None, callback=None, inline=None, user_id=2, contact=None):
        sender = {
            "id": user_id,
            "is_bot": False,
            "first_name": "User",
            "username": f"telegram_{user_id}",
        }
        message = {
            "message_id": 70,
            "date": 1700000000,
            "chat": {"id": user_id, "type": "private"},
            "from": sender,
        }
        payload = {"update_id": len(self.session.calls) + 1}
        if callback is not None:
            state = BotState.objects.filter(
                user__telegram_id=user_id, brand=self.brand
            ).first()
            message["message_id"] = (
                state.last_message_id if state and state.last_message_id else 70
            )
            payload["callback_query"] = {
                "id": "callback-id",
                "from": sender,
                "chat_instance": "test",
                "data": callback,
                "message": message,
            }
        elif inline is not None:
            payload["inline_query"] = {
                "id": "inline-id",
                "from": sender,
                "query": inline,
                "offset": "",
            }
        else:
            if contact is not None:
                message["contact"] = contact
            else:
                message["text"] = text
            payload["message"] = message
        update = types.Update.model_validate(payload, context={"bot": self.bot})
        async_to_sync(self.dp.feed_update)(self.bot, update)

    def test_new_users_cannot_bypass_invite_through_any_route(self):
        for event in (
            {"text": "/start"},
            {"text": "/start INVALID"},
            {"text": "/help"},
            {"text": "hello"},
            {"callback": "main_menu"},
            {"inline": "plans"},
            {
                "contact": {
                    "phone_number": "09123456789",
                    "first_name": "User",
                    "user_id": 2,
                }
            },
        ):
            with self.subTest(event=event):
                self.feed(**event)
                self.assertFalse(User.objects.filter(telegram_id=2).exists())
                self.assertEqual(BotState.objects.count(), 0)
        self.link.refresh_from_db()
        self.assertEqual(self.link.click_count, 0)

    def test_valid_invitation_enters_onboarding_and_attributes_once(self):
        self.feed(text="/start@EXAMPLE_BOT VALID")
        user = User.objects.get(telegram_id=2, brand=self.brand)
        self.assertEqual(user.referred_by_id, self.owner.pk)
        self.assertEqual(BotState.objects.get(user=user).current_state, "profile_setup")
        self.assertEqual(Referral.objects.filter(referee=user).count(), 1)
        self.feed(text="/start VALID")
        self.link.refresh_from_db()
        self.assertEqual(self.link.click_count, 1)
        self.assertEqual(Referral.objects.filter(referee=user).count(), 1)

    def test_invalid_links_include_inactive_cross_brand_and_wrong_bot(self):
        self.link.is_active = False
        self.link.save()
        self.feed(text="/start VALID")
        other_brand = Brand.objects.create(
            name="Other",
            slug="other",
            bot_token="other-token",
            contact_email="x@example.invalid",
        )
        other_owner = User.objects.create_user(
            username="other-owner", brand=other_brand
        )
        ReferralLink.objects.create(user=other_owner, brand=other_brand, code="OTHER")
        self.feed(text="/start OTHER")
        self.link.is_active = True
        self.link.save()
        self.feed(text="/start@another_bot VALID")
        self.assertFalse(User.objects.filter(telegram_id=2).exists())

    def test_existing_users_remain_unrestricted_and_setting_is_fresh(self):
        self.feed(text="/start", user_id=1)
        self.assertEqual(
            BotState.objects.get(user=self.owner).current_state, "main_menu"
        )
        Brand.objects.filter(pk=self.brand.pk).update(invite_only_registration=False)
        self.feed(text="/start")
        self.assertTrue(User.objects.filter(telegram_id=2).exists())
        Brand.objects.filter(pk=self.brand.pk).update(invite_only_registration=True)
        self.feed(text="/start", user_id=3)
        self.assertFalse(User.objects.filter(telegram_id=3).exists())
        self.feed(text="/start", user_id=2)
        self.assertEqual(
            BotState.objects.get(user__telegram_id=2).current_state, "main_menu"
        )

    def test_creation_guard_rejects_direct_calls_and_wrong_cached_identity(self):
        cache.set(f"{self.brand.pk}:2", self.owner.pk)
        sender = types.User(id=2, is_bot=False, first_name="New")
        with self.assertRaises(InvitationRequired):
            async_to_sync(self.handlers["start"].get_or_create_user)(sender)
        self.assertFalse(User.objects.filter(telegram_id=2).exists())

    def test_other_brand_user_does_not_grant_access(self):
        other_brand = Brand.objects.create(
            name="Other",
            slug="other",
            bot_token="other-token",
            contact_email="x@example.invalid",
        )
        User.objects.create_user(username="outside", brand=other_brand, telegram_id=2)
        self.feed(text="/start")
        self.assertFalse(User.objects.filter(brand=self.brand, telegram_id=2).exists())

    def test_onboarding_persian_phone_and_old_keyboards_are_retired(self):
        self.feed(text="/start VALID")
        user = User.objects.get(telegram_id=2)
        welcome_id = BotState.objects.get(user=user).last_message_id
        self.feed(callback="onboarding_no_subscription")
        name_id = BotState.objects.get(user=user).last_message_id
        self.feed(text="کاربر نمونه")
        phone_id = BotState.objects.get(user=user).last_message_id
        self.feed(callback="request_phone")
        self.feed(text="+۹۸ ۹۱۲-۳۴۵-۶۷۸۹")
        device_id = BotState.objects.get(user=user).last_message_id
        self.feed(callback="setup_device_linux")
        user.refresh_from_db()
        self.assertEqual(user.phone_number, "09123456789")
        self.assertEqual(UserProfile.objects.get(user=user).device_type, "linux")
        state = BotState.objects.get(user=user)
        self.assertEqual(state.current_state, "main_menu")
        self.assertIsNone(state.last_message_id)
        retired = {
            call.message_id
            for call in self.session.calls
            if isinstance(call, EditMessageReplyMarkup) and call.reply_markup is None
        }
        self.assertTrue({welcome_id, name_id, phone_id, device_id} <= retired)
        self.assertNotEqual(welcome_id, name_id)
        self.assertNotEqual(phone_id, device_id)
        self.assertTrue(
            any(
                isinstance(call, SendMessage)
                and isinstance(call.reply_markup, types.ReplyKeyboardRemove)
                for call in self.session.calls
            )
        )
        self.feed(callback="setup_device_windows")
        self.feed(callback="onboarding_no_subscription")
        self.assertEqual(UserProfile.objects.get(user=user).device_type, "linux")
        self.assertEqual(BotState.objects.get(user=user).current_state, "main_menu")

    def test_phone_edit_persian_and_arabic_input_share_canonical_storage(self):
        for phone in ("۰۹۱۲۳۴۵۶۷۸۹", "+٩٨٩١٢٣٤٥٦٧٨٩", "0098 912 345 6789"):
            with self.subTest(phone=phone):
                self.feed(callback="edit_phone", user_id=1)
                self.feed(text=phone, user_id=1)
                self.owner.refresh_from_db()
                self.assertEqual(self.owner.phone_number, "09123456789")
                self.assertEqual(
                    BotState.objects.get(user=self.owner).current_state, "main_menu"
                )
        self.feed(callback="edit_phone", user_id=1)
        self.feed(text="۰۸۱۲۳۴۵۶۷۸۹", user_id=1)
        self.assertEqual(
            BotState.objects.get(user=self.owner).current_state, "profile_edit"
        )
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.phone_number, "09123456789")
        self.feed(text="۰۹۱۲۳۴۵۶۷۸۹", user_id=1)
        self.assertIsNone(BotState.objects.get(user=self.owner).last_message_id)

    def test_onboarding_contact_only_accepts_own_number(self):
        self.feed(text="/start VALID")
        self.feed(callback="onboarding_no_subscription")
        self.feed(text="کاربر نمونه")
        contact = {
            "phone_number": "+٩٨٩١٢٣٤٥٦٧٨٩",
            "first_name": "User",
            "user_id": 3,
        }
        self.feed(contact=contact)
        user = User.objects.get(telegram_id=2)
        self.assertFalse(user.phone_number)
        self.assertEqual(BotState.objects.get(user=user).state_data["step"], "phone")
        contact["user_id"] = 2
        self.feed(contact=contact)
        user.refresh_from_db()
        self.assertEqual(user.phone_number, "09123456789")
        self.assertEqual(BotState.objects.get(user=user).state_data["step"], "device")

    def test_plan_copy_order_and_service_grid(self):
        plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="(1x) Unlimited-2M + 5D <test>",
            price=2400000,
            plan_type="unlimited",
            description="default · internal seller description",
        )
        self.feed(callback="purchase_subscription", user_id=1)
        keyboard = self.session.calls[-2].reply_markup
        self.assertEqual([len(row) for row in keyboard.inline_keyboard], [2, 2, 1, 1])
        self.feed(callback=f"select_plan_{plan.pk}", user_id=1)
        rendered = self.session.calls[-2].text
        self.assertIn(
            "📋 <b>نام پلن:</b>\n<code>(1x) Unlimited-2M + 5D &lt;test&gt;</code>",
            rendered,
        )
        self.assertNotIn("internal seller description", rendered)
        self.assertNotIn("جزئیات پلن", rendered)
        self.assertIn("هدف راه اندازی این سیستم خرید راحت و آزادانه شماست", rendered)
        self.assertLess(rendered.index("تعداد کاربر"), rendered.index("💰 قیمت"))

    def test_invitation_option_is_editable_in_brand_admin_and_default_off(self):
        admin = BrandAdmin(Brand, AdminSite())
        fields = [
            field for _, options in admin.fieldsets for field in options["fields"]
        ]
        self.assertIn("invite_only_registration", fields)
        self.assertFalse(Brand._meta.get_field("invite_only_registration").default)
