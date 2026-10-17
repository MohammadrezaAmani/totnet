from unittest.mock import patch

from aiogram import Bot, Dispatcher
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.bot.dispatcher import MultiBrandDispatcher
from apps.bot.handlers.profile import ProfileHandler
from apps.bot.handlers.purchase import PurchaseHandler
from apps.bot.handlers.start import StartHandler
from apps.bot.models import BotState
from apps.bot import test_registration
from apps.brands.models import Brand
from apps.orders.models import Order
from apps.subscriptions.catalog import get_cached_plans, refresh_plan_catalog
from apps.subscriptions.models import SubscriptionPlan
from apps.subscriptions.selection import (
    recommended_plans,
    traffic_options,
    traffic_ranges,
    volume_user_price_is_constant,
)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class PlanPickerTests(TestCase):
    feed = test_registration.RegistrationFlowTests.feed

    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(
            name="Picker",
            slug="picker",
            bot_token="test-token",
            contact_email="test@example.invalid",
        )
        self.user = User.objects.create_user(
            username="picker-user", telegram_id=1, brand=self.brand
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
            self.dp, self.brand, self.handlers
        )
        self.counter = 0

    def plan(self, volume=20, users=1, months=1, category="normal", **kwargs):
        self.counter += 1
        return SubscriptionPlan.objects.create(
            brand=self.brand,
            name=f"Plan {self.counter}",
            service_category=category,
            plan_type="hybrid" if volume is not None else "unlimited",
            traffic_limit_gb=volume,
            max_users=users,
            duration_value=months,
            duration_unit="months",
            price=kwargs.pop("price", 100),
            **kwargs,
        )

    def picker(self):
        return BotState.objects.get(user=self.user).state_data["plan_picker"]

    def click(self, action, value=""):
        self.feed(callback=f"pf:{self.picker()['token']}:{action}:{value}", user_id=1)

    def screen(self):
        return next(
            call
            for call in reversed(self.session.calls)
            if hasattr(call, "text") and call.text
        )

    def buttons(self):
        return [
            button
            for row in self.screen().reply_markup.inline_keyboard
            for button in row
        ]

    def open_service(self, category="normal"):
        refresh_plan_catalog(self.brand.pk)
        self.feed(callback="purchase_subscription", user_id=1)
        self.feed(callback=f"purchase_category_{category}", user_id=1)

    def test_filters_back_and_detail_preserve_selection(self):
        target = self.plan(volume=20, users=2, months=3)
        self.plan(volume=20, users=1, months=1)
        self.plan(volume=50, users=4, months=3)
        self.plan(volume=20, users=2, months=3, category="royal")
        self.open_service()
        self.click("toggle", "v20")
        self.click("next")
        self.assertEqual(
            [b.text for b in self.buttons() if ":toggle:" in b.callback_data],
            ["1 کاربر", "2 کاربر"],
        )
        self.click("toggle", "2")
        self.click("next")
        self.assertEqual(self.picker()["stage"], "duration")
        self.click("toggle", "m3")
        self.click("next")
        self.assertEqual(
            [
                b.callback_data
                for b in self.buttons()
                if b.callback_data.startswith("select_plan_")
            ],
            [f"select_plan_{target.pk}"],
        )
        self.feed(callback=f"select_plan_{target.pk}", user_id=1)
        back = self.buttons()[-1].callback_data
        self.assertTrue(back.endswith(":stage:results"))
        self.feed(callback=back, user_id=1)
        self.click("back")
        self.assertEqual(self.picker()["durations"], ["m3"])
        self.click("back")
        self.assertEqual(self.picker()["users"], [2])
        self.click("back")
        self.assertEqual(self.picker()["traffic"], ["v20"])
        self.click("toggle", "v50")
        self.assertEqual(self.picker()["users"], [])
        self.assertEqual(self.picker()["durations"], [])

    def test_popularity_grid_styles_and_zero_query_cached_facets(self):
        plans = [self.plan(volume=n, price=n) for n in (10, 20, 30, 40, 50)]
        unlimited = self.plan(volume=None, price=1000)
        for index in range(3):
            Order.objects.create(
                brand=self.brand,
                user=self.user,
                plan=plans[-1],
                status="completed",
                original_price=50,
                final_price=50,
                order_number=f"popular-{index}",
            )
        Order.objects.create(
            brand=self.brand,
            user=self.user,
            plan=plans[0],
            status="completed",
            order_type="reward_redemption",
            original_price=10,
            final_price=0,
            order_number="reward",
        )
        self.open_service()
        quick = [
            b for b in self.buttons() if b.callback_data.startswith("select_plan_")
        ]
        self.assertEqual(len(quick), 4)
        self.assertEqual(quick[0].callback_data, f"select_plan_{plans[-1].pk}")
        self.assertTrue(
            all("کاربر" not in b.text and b.style == "primary" for b in quick)
        )
        self.assertEqual(
            [len(row) for row in self.screen().reply_markup.inline_keyboard[:2]], [2, 2]
        )
        self.assertEqual(
            next(
                b for b in self.buttons() if b.callback_data.endswith(":toggle:u")
            ).style,
            "primary",
        )
        with self.assertNumQueries(0):
            snapshot = async_to_sync(get_cached_plans)(self.brand.pk)
            self.assertEqual(recommended_plans(snapshot)[0].pk, plans[-1].pk)
            self.assertIn("u", traffic_options(snapshot))
        self.click("toggle", "u")
        self.assertEqual(
            next(
                b for b in self.buttons() if b.callback_data.endswith(":toggle:u")
            ).style,
            "success",
        )
        self.click("next")
        self.click("all")
        self.click("next")
        self.click("all")
        self.click("next")
        self.assertIn(
            f"select_plan_{unlimited.pk}", [b.callback_data for b in self.buttons()]
        )

    def test_large_catalog_ranges_multiselect_and_pagination(self):
        for volume in range(10, 310, 10):
            for users in (1, 2, 3, 4):
                self.plan(volume=volume, users=users)
        self.open_service()
        self.assertIn("صفحهٔ 1 از 4", self.screen().text)
        self.assertEqual(
            len([b for b in self.buttons() if ":toggle:" in b.callback_data]), 9
        )
        self.click("page", "2")
        self.click("toggle", "v100")
        self.click("page", "1")
        self.click("range", "0")
        self.assertEqual(
            set(self.picker()["traffic"]), {"v10", "v20", "v30", "v40", "v50", "v100"}
        )
        self.click("next")
        self.assertEqual(
            [b.text for b in self.buttons() if ":toggle:" in b.callback_data],
            ["1 کاربر", "2 کاربر", "3 کاربر", "4 کاربر"],
        )
        self.click("all")
        self.click("next")
        self.click("all")
        self.click("next")
        self.assertIn("24 پلن", self.screen().text)
        self.assertEqual(
            len(
                [
                    b
                    for b in self.buttons()
                    if b.callback_data.startswith("select_plan_")
                ]
            ),
            8,
        )
        self.click("page", "2")
        self.assertIn("صفحهٔ 2 از 3", self.screen().text)
        self.assertLess(len(self.screen().text), 4096)
        self.assertTrue(
            all(len(b.callback_data.encode()) <= 64 for b in self.buttons())
        )

    def test_missing_selection_invalid_choice_and_stale_menu(self):
        self.plan()
        self.open_service()
        self.click("next")
        self.assertEqual(self.picker()["stage"], "traffic")
        self.click("toggle", "v999")
        self.assertEqual(self.picker()["traffic"], [])
        old = f"pf:{self.picker()['token']}:toggle:v20"
        self.feed(callback="purchase_subscription", user_id=1)
        self.feed(callback=old, user_id=1)
        self.assertIsNone(self.picker())
        self.feed(callback="purchase_category_normal", user_id=1)
        current = self.picker()
        self.feed(callback=old, user_id=1)
        self.assertEqual(self.picker(), current)

    def test_changed_catalog_and_empty_service(self):
        plan = self.plan()
        self.open_service()
        self.click("toggle", "v20")
        self.click("next")
        self.click("toggle", "1")
        self.click("next")
        self.click("toggle", "m1")
        plan.is_visible = False
        plan.save()
        refresh_plan_catalog(self.brand.pk)
        self.click("next")
        self.assertEqual(self.picker()["stage"], "duration")
        self.feed(callback="purchase_category_royal", user_id=1)
        self.assertIn("پلن فعالی", self.screen().text)
        self.assertFalse(
            any(b.callback_data.startswith("select_plan_") for b in self.buttons())
        )

    def test_volume_price_claim_checks_real_prices(self):
        one = self.plan(users=1, price=100)
        two = self.plan(users=2, price=100)
        self.assertTrue(volume_user_price_is_constant([one, two]))
        two.price = 150
        two.save()
        self.assertFalse(volume_user_price_is_constant([one, two]))
        self.open_service()
        self.assertNotIn("تغییری ایجاد نمی‌کند", self.screen().text)

    def test_range_boundaries_are_disjoint(self):
        keys = ["v0.1", "v50", "v50.1", "v100", "v200", "v500", "v500.1", "u", "n"]
        covered = [key for _, _, options in traffic_ranges(keys) for key in options]
        self.assertCountEqual(covered, [key for key in keys if key.startswith("v")])

    def test_filtering_does_not_call_connectix_or_create_orders(self):
        self.plan()
        self.open_service()
        with patch(
            "apps.vpn_providers.services.connectix.ConnectixProvider",
            side_effect=AssertionError("Unexpected live request"),
        ):
            self.click("all")
            self.click("next")
            self.click("all")
            self.click("next")
            self.click("all")
            self.click("next")
        self.assertEqual(Order.objects.count(), 0)
