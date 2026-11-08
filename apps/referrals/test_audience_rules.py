"""Segmentation regression: customers remain targetable without extra registrations."""
from decimal import Decimal
from django.test import TestCase

from apps.accounts.models import User, UserProfile
from apps.brands.models import Brand
from apps.orders.models import Order, Payment
from apps.referrals.scheduling import audience_users
from apps.subscriptions.models import SubscriptionPlan


class SegmentedAudienceTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Audience brand", slug="audience-brand", currency="USD",
            bot_token="test-token-audience", contact_email="audience@example.invalid",
        )
        self.incomplete = User.objects.create_user(
            username="incomplete", telegram_id=123101, brand=self.brand,
        )
        self.initial = User.objects.create_user(
            username="initial", telegram_id=123102, brand=self.brand,
            full_name="Initial", phone_number="09120000000",
        )
        UserProfile.objects.create(user=self.initial, devices=["iphone"])
        self.complete = User.objects.create_user(
            username="complete", telegram_id=123103, brand=self.brand,
            full_name="Completed", phone_number="09120000001", birth_date="1990-01-01",
        )
        UserProfile.objects.create(
            user=self.complete, devices=["windows"],
            living_location="Tehran", gender="male",
        )

    def ids(self, audience):
        return set(audience_users(self.brand.pk, audience).values_list("pk", flat=True))

    def test_profile_segments_do_not_overlap(self):
        self.assertEqual(self.ids("profile_incomplete"), {self.incomplete.pk})
        self.assertEqual(self.ids("profile_initial"), {self.initial.pk})
        self.assertEqual(self.ids("profile_complete"), {self.complete.pk})
        self.assertEqual(self.ids("never_paid"), {
            self.incomplete.pk, self.initial.pk, self.complete.pk,
        })

    def test_confirmed_purchase_changes_audience_without_hiding_profile_stage(self):
        plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Audience plan", currency="USD",
            price=Decimal("100"), duration_value=1,
            duration_unit=SubscriptionPlan.DurationUnit.MONTHS,
        )
        order = Order.objects.create(
            brand=self.brand, user=self.initial, plan=plan,
            original_price=Decimal("100"), final_price=Decimal("100"),
            currency="USD", status=Order.OrderStatus.PENDING,
        )
        self.assertIn(self.initial.pk, self.ids("never_paid"))
        Payment.objects.create(
            brand=self.brand, user=self.initial, order=order,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            currency="USD", amount=Decimal("100"),
        )
        self.assertNotIn(self.initial.pk, self.ids("never_paid"))
        self.assertIn(self.initial.pk, self.ids("paid"))
        self.assertIn(self.initial.pk, self.ids("profile_initial"))
