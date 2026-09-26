import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import Order, Payment, Wallet, WalletTransaction
from apps.referrals.models import (
    Achievement,
    Referral,
    ReferralClick,
    ReferralLink,
    ReferralLevel,
    ReferralProgram,
    ReferralReward,
    RewardAccount,
    RewardBoxCapacity,
    RewardPointBox,
    RewardPointLedger,
    RewardService,
)
from apps.referrals.services import (
    AchievementClaimError,
    attribute_referral,
    award_level_one_referral_for_payment,
    claim_user_achievement,
    refresh_user_achievements,
    set_active_reference_service,
    track_referral_click,
)
from apps.subscriptions.models import SubscriptionPlan
from apps.vpn_providers.models import VPNProvider


class DefaultGamificationSetupTests(TestCase):
    def test_default_program_is_complete_and_command_is_idempotent(self):
        brand = Brand.objects.create(
            name="Default Rewards",
            slug=f"default-rewards-{uuid.uuid4().hex[:8]}",
            contact_email="defaults@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
            currency="USD",
        )
        provider = VPNProvider.objects.create(
            name="Default Rewards Provider",
            provider_type=VPNProvider.ProviderType.CONNECTIX,
            base_url="https://api.example.invalid",
            api_key="masked-test-value",
            brand=brand,
            status=VPNProvider.ProviderStatus.ACTIVE,
            is_default=True,
        )
        plan = SubscriptionPlan.objects.create(
            brand=brand,
            vpn_provider=provider,
            upstream_group_id="mapped-group",
            name="Profitable reward plan",
            plan_type=SubscriptionPlan.PlanType.TIME_BASED,
            price=Decimal("100.00"),
            upstream_cost=Decimal("60.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )

        call_command("setup_default_gamification", brand=brand.slug, verbosity=0)
        program = ReferralProgram.objects.get(brand=brand)
        service = program.reference_service
        self.assertTrue(program.is_active)
        self.assertTrue(program.enable_level_rewards)
        self.assertEqual(service.plan_id, plan.pk)
        self.assertEqual(service.free_points, Decimal("10"))
        self.assertEqual(
            list(RewardBoxCapacity.objects.filter(service=service).values_list("capacity", flat=True)),
            [Decimal("10")],
        )
        self.assertEqual(program.levels.count(), 3)
        self.assertEqual(Achievement.objects.filter(brand=brand, is_active=True).count(), 7)

        call_command("setup_default_gamification", brand=brand.slug, verbosity=0)
        self.assertEqual(RewardService.objects.filter(brand=brand).count(), 1)
        self.assertEqual(program.levels.count(), 3)
        self.assertEqual(Achievement.objects.filter(brand=brand, is_active=True).count(), 7)


class ReferralAttributionTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Referral Test",
            slug=f"referral-test-{uuid.uuid4().hex[:8]}",
            contact_email="referrals@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
        )
        self.referrer = User.objects.create_user(
            username=f"referrer-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.referee = User.objects.create_user(
            username=f"referee-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.link = ReferralLink.objects.create(
            user=self.referrer,
            brand=self.brand,
            code=f"code-{uuid.uuid4().hex[:8]}",
        )

    def test_first_attribution_is_immutable_and_updates_referrer_counters(self):
        self.assertTrue(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.assertFalse(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )

        self.referrer.refresh_from_db()
        self.referee.refresh_from_db()
        self.link.refresh_from_db()
        self.assertEqual(self.referee.referred_by_id, self.referrer.pk)
        self.assertEqual(self.referrer.referral_count, 1)
        self.assertEqual(self.referee.referral_count, 0)
        self.assertEqual(self.link.conversion_count, 1)
        self.assertEqual(self.link.click_count, 0)
        self.assertEqual(
            Referral.objects.filter(referee=self.referee, brand=self.brand).count(), 1
        )

    def test_self_referral_and_inactive_link_are_rejected(self):
        self.assertFalse(
            attribute_referral(
                user_id=self.referrer.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.link.is_active = False
        self.link.save(update_fields=["is_active"])
        self.assertFalse(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.assertFalse(Referral.objects.exists())

    def test_referral_start_is_counted_once_per_visitor(self):
        self.assertTrue(
            track_referral_click(
                code=self.link.code,
                brand_id=self.brand.pk,
                visitor_id=self.referee.pk,
            )
        )
        self.assertFalse(
            track_referral_click(
                code=self.link.code,
                brand_id=self.brand.pk,
                visitor_id=self.referee.pk,
            )
        )
        self.link.refresh_from_db()
        self.assertEqual(self.link.click_count, 1)
        self.assertEqual(ReferralClick.objects.filter(link=self.link).count(), 1)


class AchievementTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Achievement Test",
            slug=f"achievement-test-{uuid.uuid4().hex[:8]}",
            contact_email="achievements@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
            currency="USD",
        )
        self.user = User.objects.create_user(
            username=f"achievement-user-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.referee = User.objects.create_user(
            username=f"achievement-referee-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.link = ReferralLink.objects.create(
            user=self.user,
            brand=self.brand,
            code=f"achievement-{uuid.uuid4().hex[:8]}",
        )
        self.achievement = Achievement.objects.create(
            brand=self.brand,
            name="First referral",
            description="Make a referral",
            achievement_type=Achievement.AchievementType.REFERRAL,
            requirements={"referrals": 1},
            reward_amount=Decimal("2.50"),
            is_repeatable=True,
        )

    def test_achievement_progress_claim_and_repeatable_threshold(self):
        self.assertTrue(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        progress = refresh_user_achievements(
            user_id=self.user.pk, brand_id=self.brand.pk
        )
        self.assertEqual(progress[0].progress, Decimal("100.00"))
        claim_user_achievement(
            user_id=self.user.pk,
            brand_id=self.brand.pk,
            achievement_id=self.achievement.pk,
        )
        progress = refresh_user_achievements(
            user_id=self.user.pk, brand_id=self.brand.pk
        )
        self.assertEqual(progress[0].claim_count, 1)
        self.assertEqual(progress[0].progress, Decimal("50.00"))
        self.assertFalse(progress[0].is_completed)
        self.assertEqual(
            Wallet.objects.get(user=self.user, brand=self.brand).balance,
            Decimal("2.50"),
        )

    def test_claim_rechecks_requirements_after_admin_changes_them(self):
        attribute_referral(
            user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
        )
        refresh_user_achievements(user_id=self.user.pk, brand_id=self.brand.pk)
        self.achievement.requirements = {"referrals": 2}
        self.achievement.save(update_fields=["requirements"])

        with self.assertRaises(AchievementClaimError):
            claim_user_achievement(
                user_id=self.user.pk,
                brand_id=self.brand.pk,
                achievement_id=self.achievement.pk,
            )
        self.assertFalse(Wallet.objects.filter(user=self.user, brand=self.brand).exists())


class ReferralProfitPointTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Points Test",
            slug=f"points-test-{uuid.uuid4().hex[:8]}",
            contact_email="points@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
            currency="USD",
        )
        self.referrer = User.objects.create_user(
            username=f"points-referrer-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.referee = User.objects.create_user(
            username=f"points-referee-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.provider = VPNProvider.objects.create(
            name="Reward Provider",
            provider_type=VPNProvider.ProviderType.CONNECTIX,
            base_url="https://api.example.invalid",
            api_key="masked-test-value",
            brand=self.brand,
            status=VPNProvider.ProviderStatus.ACTIVE,
            is_default=True,
        )
        self.link = ReferralLink.objects.create(
            user=self.referrer, brand=self.brand, code=f"p-{uuid.uuid4().hex[:8]}"
        )
        self.assertTrue(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.purchase_plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="Purchase",
            price=Decimal("15.00"),
            upstream_cost=Decimal("5.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.reference_plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="Reference",
            price=Decimal("5.00"),
            upstream_cost=Decimal("1.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.lifetime_plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="Lifetime Reference",
            price=Decimal("10.00"),
            upstream_cost=Decimal("5.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.service = RewardService.objects.create(
            brand=self.brand, plan=self.reference_plan, free_points=Decimal("1")
        )
        self.lifetime_service = RewardService.objects.create(
            brand=self.brand, plan=self.lifetime_plan
        )
        RewardBoxCapacity.objects.create(
            service=self.service, sequence=1, capacity=Decimal("1")
        )
        RewardBoxCapacity.objects.create(
            service=self.service, sequence=2, capacity=Decimal("2")
        )
        RewardBoxCapacity.objects.create(
            service=self.lifetime_service, sequence=1, capacity=Decimal("1")
        )
        self.program = ReferralProgram.objects.create(
            brand=self.brand,
            reference_service=self.service,
            lifetime_reference_service=self.lifetime_service,
        )
        self.order = Order.objects.create(
            brand=self.brand,
            user=self.referee,
            plan=self.purchase_plan,
            original_price=Decimal("15.00"),
            final_price=Decimal("15.00"),
            currency="USD",
            status=Order.OrderStatus.PAID,
        )
        self.payment = Payment.objects.create(
            order=self.order,
            brand=self.brand,
            user=self.referee,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal("15.00"),
            currency="USD",
        )

    def test_confirmed_order_awards_fractional_level_one_points_once(self):
        self.purchase_plan.upstream_cost = Decimal("8.00")
        self.purchase_plan.save(update_fields=["upstream_cost"])
        points = award_level_one_referral_for_payment(payment_id=str(self.payment.payment_id))
        duplicate = award_level_one_referral_for_payment(payment_id=str(self.payment.payment_id))
        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        referral = Referral.objects.get(referee=self.referee, brand=self.brand)

        self.assertEqual(points, Decimal("2.50000000"))
        self.assertEqual(duplicate, Decimal("0"))
        self.assertEqual(account.lifetime_profit, Decimal("10.00"))
        self.assertEqual(account.lifetime_points, Decimal("2.00000000"))
        self.assertEqual(account.liquid_points, Decimal("1.00000000"))
        self.assertEqual(
            list(RewardPointBox.objects.filter(account=account).values_list("filled", flat=True)),
            [Decimal("1.00000000"), Decimal("1.50000000")],
        )
        self.assertEqual(ReferralReward.objects.filter(order=self.order).count(), 1)
        self.assertEqual(RewardPointLedger.objects.filter(order=self.order, entry_type="earned").count(), 1)
        self.assertEqual(referral.status, Referral.ReferralStatus.REWARDED)

    def test_qualified_level_multiplier_and_bonus_are_applied_once(self):
        self.program.enable_level_rewards = True
        self.program.save(update_fields=["enable_level_rewards", "updated_at"])
        ReferralLevel.objects.create(
            program=self.program,
            level=1,
            name="Starter",
            min_referrals=1,
            min_lifetime_points=Decimal("0"),
            min_conversion_rate=Decimal("0"),
            reward_multiplier=Decimal("2"),
            bonus_reward=Decimal("3.00"),
        )

        points = award_level_one_referral_for_payment(
            payment_id=str(self.payment.payment_id)
        )
        award_level_one_referral_for_payment(payment_id=str(self.payment.payment_id))

        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        wallet = Wallet.objects.get(user=self.referrer, brand=self.brand)
        self.assertEqual(points, Decimal("5.00000000"))
        self.assertEqual(account.lifetime_points, Decimal("4.00000000"))
        self.assertEqual(wallet.balance, Decimal("3.00"))
        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=wallet, idempotency_key__startswith="referral-level-bonus:"
            ).count(),
            1,
        )

    def test_recovery_task_only_queues_unrewarded_referred_purchases(self):
        from apps.referrals.tasks import recover_pending_referral_rewards

        with patch("apps.referrals.tasks.process_referral_reward.delay") as queue:
            count = recover_pending_referral_rewards.run(limit=10)

        self.assertEqual(count, 1)
        queue.assert_called_once_with(str(self.payment.payment_id))

    def test_direct_referral_earns_again_on_later_profitable_orders(self):
        first_points = award_level_one_referral_for_payment(
            payment_id=str(self.payment.payment_id)
        )
        later_order = Order.objects.create(
            brand=self.brand,
            user=self.referee,
            plan=self.purchase_plan,
            original_price=Decimal("8.00"),
            final_price=Decimal("8.00"),
            currency="USD",
            status=Order.OrderStatus.PAID,
        )
        later_payment = Payment.objects.create(
            order=later_order,
            brand=self.brand,
            user=self.referee,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal("8.00"),
            currency="USD",
        )
        later_points = award_level_one_referral_for_payment(
            payment_id=str(later_payment.payment_id)
        )

        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        referral = Referral.objects.get(referee=self.referee, brand=self.brand)
        self.assertEqual(first_points, Decimal("2.50000000"))
        self.assertEqual(later_points, Decimal("0.75000000"))
        self.assertEqual(account.lifetime_profit, Decimal("13.00"))
        self.assertEqual(account.lifetime_points, Decimal("2.60000000"))
        self.assertEqual(ReferralReward.objects.filter(referral=referral).count(), 2)
        self.assertEqual(referral.conversion_order_id, self.order.pk)

    def test_reference_change_converts_completed_points_and_rebases_partial_box(self):
        award_level_one_referral_for_payment(payment_id=str(self.payment.payment_id))
        new_plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="New Reference",
            price=Decimal("8.00"),
            upstream_cost=Decimal("1.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        new_service = RewardService.objects.create(brand=self.brand, plan=new_plan)
        RewardBoxCapacity.objects.create(
            service=new_service, sequence=1, capacity=Decimal("2")
        )
        set_active_reference_service(brand_id=self.brand.pk, service_id=new_service.pk)

        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        wallet = Wallet.objects.get(user=self.referrer, brand=self.brand)
        self.assertEqual(wallet.balance, Decimal("4.00000005"))
        self.assertEqual(account.reference_service_id, new_service.pk)
        self.assertEqual(account.liquid_points, Decimal("0"))
        new_boxes = list(
            RewardPointBox.objects.filter(account=account, service=new_service).order_by("cycle")
        )
        self.assertEqual(len(new_boxes), 1)
        self.assertEqual(new_boxes[0].filled, Decimal("0.85714285"))
        self.assertEqual(new_boxes[0].state, RewardPointBox.State.OPEN)

    def test_configured_point_valuations_are_snapshotted(self):
        self.reference_plan.price = Decimal("9.00")
        self.reference_plan.save(update_fields=["price"])
        self.lifetime_plan.upstream_cost = Decimal("8.00")
        self.lifetime_plan.save(update_fields=["upstream_cost"])
        self.service.refresh_from_db()
        self.program.refresh_from_db()

        self.assertEqual(self.service.point_value, Decimal("4.00"))
        self.assertEqual(self.program.lifetime_point_value, Decimal("5.00"))

    def test_reward_redemption_debits_points_and_is_idempotent(self):
        from apps.referrals.services import redeem_reward_service

        award_level_one_referral_for_payment(payment_id=str(self.payment.payment_id))
        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        key = account.redemption_nonce
        order, replay = redeem_reward_service(
            user_id=self.referrer.pk, brand_id=self.brand.pk, request_key=key
        )
        duplicate, was_replay = redeem_reward_service(
            user_id=self.referrer.pk, brand_id=self.brand.pk, request_key=key
        )

        account.refresh_from_db()
        self.assertFalse(replay)
        self.assertTrue(was_replay)
        self.assertEqual(order.pk, duplicate.pk)
        self.assertEqual(order.order_type, Order.OrderType.REWARD_REDEMPTION)
        self.assertEqual(order.status, Order.OrderStatus.PAID)
        self.assertEqual(order.final_price, Decimal("0"))
        self.assertEqual(order.subscriptions.count(), 1)
        self.assertEqual(account.liquid_points, Decimal("0"))
        self.assertEqual(RewardPointLedger.objects.filter(order=order, entry_type="redeemed").count(), 1)

    def test_reference_change_does_not_convert_already_spent_box_points(self):
        from apps.referrals.services import redeem_reward_service

        award_level_one_referral_for_payment(payment_id=str(self.payment.payment_id))
        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        redeem_reward_service(
            user_id=self.referrer.pk,
            brand_id=self.brand.pk,
            request_key=account.redemption_nonce,
        )
        old_completed_box = RewardPointBox.objects.get(
            account=account,
            service=self.service,
            state=RewardPointBox.State.COMPLETE,
        )
        self.assertEqual(old_completed_box.spent_points, Decimal("1.00000000"))

        new_plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="Post-redemption Reference",
            price=Decimal("3.00"),
            upstream_cost=Decimal("1.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        new_service = RewardService.objects.create(brand=self.brand, plan=new_plan)
        RewardBoxCapacity.objects.create(
            service=new_service, sequence=1, capacity=Decimal("2")
        )
        set_active_reference_service(brand_id=self.brand.pk, service_id=new_service.pk)

        account.refresh_from_db()
        old_completed_box.refresh_from_db()
        self.assertEqual(account.liquid_points, Decimal("2.00000000"))
        self.assertEqual(old_completed_box.state, RewardPointBox.State.CONVERTED)
        self.assertEqual(
            Wallet.objects.filter(user=self.referrer, brand=self.brand).count(), 0
        )
