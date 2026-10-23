"""Regression coverage for the user-first and duration-first purchase selector."""

from decimal import Decimal

from aiogram import Bot, Dispatcher
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.bot import test_registration
from apps.bot.dispatcher import MultiBrandDispatcher
from apps.bot.handlers.profile import ProfileHandler
from apps.bot.handlers.purchase import PurchaseHandler
from apps.bot.handlers.start import StartHandler
from apps.bot.models import BotState
from apps.brands.models import Brand
from apps.orders.models import Order
from apps.subscriptions.catalog import refresh_plan_catalog
from apps.subscriptions.models import SubscriptionPlan


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class PlanPickerTests(TestCase):
    feed = test_registration.RegistrationFlowTests.feed

    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(
            name="Picker", slug="picker", bot_token="test-token",
            contact_email="test@example.invalid",
        )
        self.user = User.objects.create_user(
            username="picker-user", telegram_id=1, brand=self.brand,
        )
        self.session = test_registration.RecordingSession()
        self.bot = Bot("999:synthetic-token", session=self.session)
        self.handlers = {
            "start": StartHandler(self.bot, self.brand),
            "purchase": PurchaseHandler(self.bot, self.brand),
            "profile": ProfileHandler(self.bot, self.brand),
        }
        self.dp = Dispatcher()
        async_to_sync(MultiBrandDispatcher().setup_brand_routes)(
            self.dp, self.brand, self.handlers,
        )
        self.counter = 0

    def plan(self, volume=20, users=1, months=1, category="normal", price=100):
        self.counter += 1
        return SubscriptionPlan.objects.create(
            brand=self.brand, name=f"Plan {self.counter}",
            service_category=category,
            plan_type="hybrid" if volume is not None else "unlimited",
            traffic_limit_gb=volume, max_users=users,
            duration_value=months, duration_unit="months", price=price,
        )

    def picker(self):
        return BotState.objects.get(user=self.user).state_data["plan_picker"]

    def click(self, action, value=""):
        self.feed(callback=f"pf:{self.picker()['token']}:{action}:{value}", user_id=1)

    def buttons(self):
        screen = next(m for m in reversed(self.session.calls) if hasattr(m, "text") and m.text)
        return [b for row in screen.reply_markup.inline_keyboard for b in row]

    def open_service(self, category="normal"):
        refresh_plan_catalog(self.brand.pk)
        self.feed(callback="purchase_subscription", user_id=1)
        self.feed(callback=f"purchase_category_{category}", user_id=1)

    @override_settings(TEMP_DISABLE_2_3_MONTH_PLANS=False)
    def test_initial_duration_and_users_come_before_ranges(self):
        chosen = self.plan(40, users=2, months=2)
        self.plan(40, users=1, months=1)
        self.plan(40, users=2, months=2, category="royal")
        self.open_service()
        self.assertEqual(self.picker()["stage"], "bundle")
        self.click("duration", "m2")
        self.click("users", "2")
        self.assertEqual(self.picker()["stage"], "ranges")
        self.assertEqual([b.callback_data.endswith(":range:b") for b in self.buttons()].count(True), 1)
        self.click("range", "b")
        self.assertEqual(self.picker()["stage"], "results")
        self.assertEqual([b.callback_data for b in self.buttons() if b.callback_data.startswith("select_plan_")],
                         [f"select_plan_{chosen.pk}"])
        self.click("back")
        self.assertEqual(self.picker()["stage"], "ranges")
        self.click("back")
        self.assertEqual(self.picker()["users"], 2)

    @override_settings(TEMP_DISABLE_2_3_MONTH_PLANS=False)
    def test_royal_volume_minimum_varies_by_duration(self):
        first = self.plan(10, category="royal")
        second = self.plan(40, category="royal", months=2)
        third = self.plan(60, category="royal", months=3)
        self.open_service("royal")
        for duration, users, expected, bucket in (
            ("m1", "1", first, "a"), ("m2", "1", second, "b"),
            ("m3", "1", third, "c"),
        ):
            self.click("duration", duration)
            self.click("users", users)
            self.assertEqual(self.picker()["stage"], "ranges")
            self.click("range", bucket)
            selected = [b.callback_data for b in self.buttons() if b.callback_data.startswith("select_plan_")]
            self.assertEqual(selected, [f"select_plan_{expected.pk}"])
            self.click("back")
            self.click("back")

    def test_all_plans_separates_volumetric_unlimited_and_time(self):
        volume = self.plan(20, users=1, months=1)
        self.plan(20, users=2, months=3, price=100)
        unlimited = self.plan(None, users=1, months=2)
        other = self.plan(None, users=4, months=3)
        self.open_service()
        self.click("all")
        labels = [b.text for b in self.buttons()]
        self.assertTrue(any("حجمی" in name for name in labels))
        self.assertTrue(any("نامحدود" in name for name in labels))
        self.click("type", "volume")
        selected = [b for b in self.buttons() if b.callback_data.startswith("select_plan_")]
        self.assertEqual([b.callback_data for b in selected], [f"select_plan_{volume.pk}"])
        self.assertNotIn("کاربر", selected[0].text)
        self.click("back")
        self.click("type", "unlimited")
        selected = [b.callback_data for b in self.buttons() if b.callback_data.startswith("select_plan_")]
        self.assertIn(f"select_plan_{unlimited.pk}", selected)
        self.click("allusers", "4")
        selected = [b.callback_data for b in self.buttons() if b.callback_data.startswith("select_plan_")]
        self.assertEqual(selected, [f"select_plan_{other.pk}"])

    def test_stale_filter_does_not_reset_active_session(self):
        self.plan()
        self.open_service()
        token = self.picker()["token"]
        self.feed(callback="purchase_subscription", user_id=1)
        self.feed(callback="purchase_category_normal", user_id=1)
        current = self.picker()
        self.feed(callback=f"pf:{token}:duration:m1", user_id=1)
        self.assertEqual(self.picker(), current)

    def test_range_boundaries_never_double_count_and_no_orders_during_browsing(self):
        self.plan(20)
        self.plan(30)
        self.plan(31)
        self.plan(59)
        self.plan(60)
        self.plan(99)
        self.plan(100)
        self.open_service()
        self.click("duration", "m1")
        self.click("users", "1")
        handler = self.handlers["purchase"]
        plans = async_to_sync(handler._picker_catalog)(self.picker())
        filtered = handler._filtered(plans, self.picker())
        matches = [p.pk for key, _ in handler._volume_ranges(self.picker(), filtered)
                   for p in handler._range_plans(filtered, {**self.picker(), "range": key})]
        self.assertEqual(sorted(matches), sorted(p.pk for p in filtered))
        self.assertEqual(Order.objects.count(), 0)

    def test_highest_available_range_offers_matching_unlimited(self):
        one_month = self.plan(volume=70, users=1, months=1)
        unlimited = self.plan(volume=None, users=1, months=1, price=160)
        other_users = self.plan(volume=None, users=2, months=1, price=300)
        other_duration = self.plan(volume=None, users=1, months=2, price=300)
        self.open_service()
        self.click("duration", "m1")
        self.click("users", "1")
        self.click("range", "c")  # Highest existing bucket: no 100GB plan.
        visible = [b.callback_data for b in self.buttons() if b.callback_data.startswith("select_plan_")]
        self.assertEqual(visible, [f"select_plan_{one_month.pk}", f"select_plan_{unlimited.pk}"])
        self.assertNotIn(f"select_plan_{other_users.pk}", visible)
        self.assertNotIn(f"select_plan_{other_duration.pk}", visible)

    def test_catalog_hides_archived_and_admin_hidden_and_sorts_by_admin_order(self):
        first = self.plan(volume=20)
        second = self.plan(volume=40)
        archived = self.plan(volume=60)
        hidden = self.plan(volume=80)
        SubscriptionPlan.objects.filter(pk=first.pk).update(display_order=90)
        SubscriptionPlan.objects.filter(pk=second.pk).update(display_order=1)
        SubscriptionPlan.objects.filter(pk=archived.pk).update(is_active=False)
        SubscriptionPlan.objects.filter(pk=hidden.pk).update(is_visible=False)
        self.open_service()
        self.click("duration", "m1")
        self.click("users", "1")
        self.click("range", "a")
        self.assertEqual([b.callback_data for b in self.buttons() if b.callback_data.startswith("select_plan_")],
                         [f"select_plan_{first.pk}"])
        self.click("back")
        self.click("range", "b")
        self.assertEqual([b.callback_data for b in self.buttons() if b.callback_data.startswith("select_plan_")],
                         [f"select_plan_{second.pk}"])
        self.click("all")
        self.click("type", "volume")
        self.assertEqual([b.callback_data for b in self.buttons() if b.callback_data.startswith("select_plan_")],
                         [f"select_plan_{second.pk}", f"select_plan_{first.pk}"])

    @override_settings(TEMP_DISABLE_2_3_MONTH_PLANS=True)
    def test_royal_two_month_blocked_but_normal_two_month_visible(self):
        normal_two = self.plan(volume=30, months=2, category="normal")
        normal_three = self.plan(volume=30, months=3, category="normal")
        royal_two = self.plan(volume=40, months=2, category="royal")
        self.open_service()
        self.assertTrue(any("۲" in b.text and "🔴" not in b.text for b in self.buttons()))
        self.assertFalse(any("۳ ماه" in b.text for b in self.buttons()))
        self.click("duration", "m2")
        self.click("users", "1")
        self.assertEqual(self.picker()["stage"], "ranges")
        self.feed(callback="purchase_subscription", user_id=1)
        self.feed(callback="purchase_category_royal", user_id=1)
        self.assertTrue(any("🔴" in b.text and "۲" in b.text for b in self.buttons()))
        self.feed(callback=f"select_plan_{royal_two.pk}", user_id=1)
        self.assertEqual(Order.objects.count(), 0)
        self.feed(callback=f"purchase_plan_{normal_three.pk}", user_id=1)
        self.assertEqual(Order.objects.count(), 0)

    def test_gift_days_appear_in_volume_and_unlimited_buttons(self):
        volume = self.plan(volume=20)
        unlimited = self.plan(volume=None)
        SubscriptionPlan.objects.filter(pk=volume.pk).update(gift_days=3)
        SubscriptionPlan.objects.filter(pk=unlimited.pk).update(gift_days=10)
        self.open_service()
        self.click("duration", "m1")
        self.click("users", "1")
        self.click("range", "a")
        self.assertTrue(any("+۳روز هدیه" in b.text for b in self.buttons()))
        self.click("all")
        self.click("type", "unlimited")
        self.assertTrue(any("+۱۰روز هدیه" in b.text for b in self.buttons()))

    def test_admin_deactivation_invalidates_cached_catalog_after_commit(self):
        from apps.subscriptions.catalog import get_cached_plans
        plan = self.plan(volume=20)
        refresh_plan_catalog(self.brand.pk)
        self.assertIn(plan.pk, [p.pk for p in async_to_sync(get_cached_plans)(self.brand.pk)])
        plan.is_active = False
        with self.captureOnCommitCallbacks(execute=True):
            plan.save(update_fields=("is_active", "updated_at"))
        self.assertNotIn(plan.pk, [p.pk for p in async_to_sync(get_cached_plans)(self.brand.pk)])
