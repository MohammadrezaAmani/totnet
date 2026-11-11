"""Persisted pricing and durable challenge broadcast regression tests."""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.referrals.broadcast import (
    cancel_challenge_broadcast, eligible_challenge_users, start_challenge_broadcast,
)
from apps.referrals.models import (
    ChallengeBroadcast, ChallengeProgram, ChallengeTier, GamificationNotification,
    ReferralProgram, ServiceRewardRate, UserChallenge,
)
from apps.referrals.rate_policy import challenge_rate_snapshot, snapshot_percent
from apps.referrals.tasks import process_challenge_broadcast
from apps.subscriptions.models import SubscriptionPlan
from apps.subscriptions.policy import effective_reward_percent
from apps.orders.models import Order


class ConfiguredChallengeRatesTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Rates Test", slug=f"rates-{uuid.uuid4().hex[:8]}",
            contact_email="rates@example.invalid", bot_token="test-token",
        )
        self.user = User.objects.create_user(
            username=f"rate-user-{uuid.uuid4().hex[:8]}", brand=self.brand,
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Test", price=Decimal("100"),
            service_category="normal", plan_type="unlimited",
            duration_value=30, duration_unit="days",
        )
        self.order = Order.objects.create(
            brand=self.brand, user=self.user, plan=self.plan,
            original_price=Decimal("100"), final_price=Decimal("100"),
        )

    def test_no_implicit_rate_and_configured_fallback(self):
        with self.assertRaises(ValueError):
            effective_reward_percent(self.order, None)
        self.assertEqual(effective_reward_percent(self.order, Decimal("11")), Decimal("11"))
        self.assertEqual(effective_reward_percent(self.order, Decimal("4.5")), Decimal("4.5"))

    def test_snapshot_uses_exact_then_category_then_base_without_repricing(self):
        program = ReferralProgram.objects.create(
            brand=self.brand, is_active=True, purchase_reward_percent=Decimal("11")
        )
        category = ServiceRewardRate.objects.create(
            brand=self.brand, service_category="normal", plan_type="all",
            percent=Decimal("7"),
        )
        exact = ServiceRewardRate.objects.create(
            brand=self.brand, service_category="normal", plan_type="unlimited",
            percent=Decimal("5"),
        )
        frozen = challenge_rate_snapshot(self.brand.pk)
        self.assertEqual(snapshot_percent(self.plan, frozen), Decimal("5"))
        self.assertEqual(effective_reward_percent(self.order, program.purchase_reward_percent), Decimal("5"))
        exact.is_active = False
        exact.save(update_fields=["is_active"])
        self.assertEqual(effective_reward_percent(self.order, program.purchase_reward_percent), Decimal("7"))
        category.percent = Decimal("4")
        category.save(update_fields=["percent"])
        program.purchase_reward_percent = Decimal("17")
        program.save(update_fields=["purchase_reward_percent"])
        self.assertEqual(snapshot_percent(self.plan, frozen), Decimal("5"))
        self.assertEqual(effective_reward_percent(self.order, program.purchase_reward_percent), Decimal("4"))

    def test_missing_admin_base_rate_is_rejected(self):
        ReferralProgram.objects.create(brand=self.brand, is_active=True)
        with self.assertRaises(ValidationError):
            challenge_rate_snapshot(self.brand.pk)


class ChallengeBroadcastTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Broadcast Test", slug=f"broadcast-{uuid.uuid4().hex[:8]}",
            contact_email="broadcast@example.invalid", bot_token="test-token",
        )
        self.admin = User.objects.create_user(
            username=f"admin-{uuid.uuid4().hex[:8]}", brand=self.brand,
        )
        ReferralProgram.objects.create(
            brand=self.brand, is_active=True, purchase_reward_percent=Decimal("9")
        )
        self.program = ChallengeProgram.objects.create(
            brand=self.brand, is_active=True, duration_days=4,
            offer_delay_minutes=0, success_multiplier=Decimal("2"),
            failure_multiplier=Decimal("0.5"),
        )
        ChallengeTier.objects.create(
            program=self.program, target_referrals=2, entry_fee=Decimal("25"),
        )
        self.members = [
            User.objects.create_user(
                username=f"broadcast-member-{i}-{uuid.uuid4().hex[:8]}",
                brand=self.brand, telegram_id=300000 + i,
            )
            for i in range(3)
        ]

    def test_global_launch_has_confirmation_dedupe_and_bounded_progress(self):
        job, created = start_challenge_broadcast(
            brand_id=self.brand.pk, admin_id=self.admin.pk,
        )
        self.assertTrue(created)
        again, created = start_challenge_broadcast(
            brand_id=self.brand.pk, admin_id=self.admin.pk,
        )
        self.assertFalse(created)
        self.assertEqual(again.pk, job.pk)
        process_challenge_broadcast.run(job.pk)
        process_challenge_broadcast.run(job.pk)
        process_challenge_broadcast.run(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, ChallengeBroadcast.Status.COMPLETED)
        self.assertEqual(job.queued_count, 3)
        self.assertEqual(UserChallenge.objects.filter(program=self.program).count(), 3)
        self.assertEqual(GamificationNotification.objects.filter(
            notification_type="challenge_offer", brand=self.brand,
        ).count(), 3)
        other_job, created = start_challenge_broadcast(
            brand_id=self.brand.pk, admin_id=self.admin.pk,
        )
        self.assertTrue(created)
        process_challenge_broadcast.run(other_job.pk)
        process_challenge_broadcast.run(other_job.pk)
        other_job.refresh_from_db()
        self.assertEqual(other_job.queued_count, 0)
        self.assertEqual(GamificationNotification.objects.filter(
            notification_type="challenge_offer", brand=self.brand,
        ).count(), 3)

    def test_cancel_prevents_enqueuing_and_finalized_offer_is_not_reopened(self):
        UserChallenge.objects.create(
            brand=self.brand, user=self.members[0], program=self.program,
            status=UserChallenge.Status.DECLINED,
        )
        self.assertFalse(eligible_challenge_users(self.program).filter(pk=self.members[0].pk).exists())
        job, _ = start_challenge_broadcast(brand_id=self.brand.pk, admin_id=self.admin.pk)
        cancel_challenge_broadcast(brand_id=self.brand.pk, broadcast_id=job.pk)
        process_challenge_broadcast.run(job.pk)
        self.assertEqual(GamificationNotification.objects.filter(brand=self.brand).count(), 0)

    def test_disabled_or_unconfigured_challenge_cannot_launch(self):
        self.program.is_active = False
        self.program.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            start_challenge_broadcast(brand_id=self.brand.pk, admin_id=self.admin.pk)
        self.program.is_active = True
        self.program.success_multiplier = None
        self.program.save(update_fields=["is_active", "success_multiplier"])
        with self.assertRaises(ValidationError):
            start_challenge_broadcast(brand_id=self.brand.pk, admin_id=self.admin.pk)
