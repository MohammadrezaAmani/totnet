import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import Order, Payment, Wallet, WalletTransaction
from apps.referrals.models import (
    Achievement,
    ChallengeProgram,
    ChallengeTier,
    Referral,
    ReferralClick,
    ReferralLink,
    ReferralLevel,
    ReferralProgram,
    ReferralReward,
    RewardAccount,
    UserChallenge,
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


class ReferralCashPointTests(TestCase):
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
        self.link = ReferralLink.objects.create(
            user=self.referrer, brand=self.brand, code=f"p-{uuid.uuid4().hex[:8]}"
        )
        self.assertTrue(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="Purchase",
            price=Decimal("100.00"),
            upstream_cost=Decimal("40.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.program = ReferralProgram.objects.create(
            brand=self.brand,
            is_active=True,
            purchase_reward_percent=Decimal("8"),
            minimum_purchase_amount=Decimal("0"),
        )

    def _purchase(self, amount: str = "100.00", *, user=None):
        buyer = user or self.referee
        order = Order.objects.create(
            brand=self.brand,
            user=buyer,
            plan=self.plan,
            original_price=Decimal(amount),
            final_price=Decimal(amount),
            currency="USD",
            status=Order.OrderStatus.PAID,
        )
        payment = Payment.objects.create(
            order=order,
            brand=self.brand,
            user=buyer,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal(amount),
            currency="USD",
        )
        return order, payment

    def test_each_direct_purchase_is_one_point_and_three_points_cash_out(self):
        expected_values = [Decimal("8.00"), Decimal("4.00"), Decimal("2.00")]
        for amount in ("100.00", "50.00", "25.00"):
            _order, payment = self._purchase(amount)
            self.assertEqual(
                award_level_one_referral_for_payment(payment_id=str(payment.payment_id)),
                Decimal("1"),
            )

        rewards = list(
            ReferralReward.objects.filter(user=self.referrer).order_by("created_at", "pk")
        )
        self.assertEqual(len(rewards), 3)
        self.assertEqual([reward.cash_value for reward in rewards], expected_values)
        self.assertTrue(all(reward.is_cashed_out for reward in rewards))
        self.assertEqual(
            Wallet.objects.get(user=self.referrer, brand=self.brand).balance,
            Decimal("14.00"),
        )
        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        self.assertEqual(account.lifetime_points, Decimal("3"))
        self.assertEqual(account.liquid_points, Decimal("0"))

    def test_partial_payment_does_not_award_full_order_early(self):
        order = Order.objects.create(
            brand=self.brand,
            user=self.referee,
            plan=self.plan,
            original_price=Decimal("100.00"),
            final_price=Decimal("100.00"),
            currency="USD",
            status=Order.OrderStatus.AWAITING_PAYMENT,
        )
        first = Payment.objects.create(
            order=order,
            brand=self.brand,
            user=self.referee,
            # Supported split checkout is a wallet part plus one external payment.
            payment_method=Payment.PaymentMethod.WALLET,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal("40.00"),
            currency="USD",
        )
        self.assertEqual(
            award_level_one_referral_for_payment(payment_id=str(first.payment_id)),
            Decimal("0"),
        )
        self.assertFalse(ReferralReward.objects.filter(order=order).exists())

        second = Payment.objects.create(
            order=order,
            brand=self.brand,
            user=self.referee,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal("60.00"),
            currency="USD",
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.OrderStatus.PAID)
        self.assertEqual(
            award_level_one_referral_for_payment(payment_id=str(second.payment_id)),
            Decimal("1"),
        )
        self.assertEqual(ReferralReward.objects.get(order=order).cash_value, Decimal("8.00"))

    def test_reward_processing_is_idempotent_per_order(self):
        _order, payment = self._purchase("100.00")
        first = award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        duplicate = award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        self.assertEqual(first, Decimal("1"))
        self.assertEqual(duplicate, Decimal("0"))
        self.assertEqual(ReferralReward.objects.count(), 1)

    def test_recovery_task_only_queues_unrewarded_referred_purchases(self):
        from apps.referrals.tasks import recover_pending_referral_rewards

        _order, payment = self._purchase("100.00")
        with patch("apps.referrals.tasks.process_referral_reward.delay") as queue:
            count = recover_pending_referral_rewards.run(limit=10)

        self.assertEqual(count, 1)
        queue.assert_called_once_with(str(payment.payment_id))


class PropzinoChallengeTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Challenge Test",
            slug=f"challenge-test-{uuid.uuid4().hex[:8]}",
            contact_email="challenge@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
            currency="USD",
        )
        self.referrer = User.objects.create_user(
            username=f"challenge-referrer-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.referee = User.objects.create_user(
            username=f"challenge-referee-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.link = ReferralLink.objects.create(
            user=self.referrer, brand=self.brand, code=f"c-{uuid.uuid4().hex[:8]}"
        )
        self.assertTrue(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand,
            name="Purchase",
            price=Decimal("100.00"),
            upstream_cost=Decimal("40.00"),
            currency="USD",
            duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.program = ReferralProgram.objects.create(
            brand=self.brand,
            is_active=True,
            purchase_reward_percent=Decimal("8"),
            minimum_purchase_amount=Decimal("0"),
        )
        self.challenge_program = ChallengeProgram.objects.create(
            brand=self.brand,
            name="پراپزینو",
            is_active=True,
            offer_delay_days=5,
            duration_days=7,
            reward_percent=Decimal("13"),
            # Most challenge mechanics tests use a referral created in setUp.
            # The separate policy test below verifies the stricter XMind rule.
            require_referral_join_during_challenge=False,
        )
        self.wallet = Wallet.objects.create(
            user=self.referrer,
            brand=self.brand,
            currency=self.brand.currency,
            balance=Decimal("100.00"),
        )

    def _purchase(self, amount: str = "100.00"):
        order = Order.objects.create(
            brand=self.brand,
            user=self.referee,
            plan=self.plan,
            original_price=Decimal(amount),
            final_price=Decimal(amount),
            currency="USD",
            status=Order.OrderStatus.PAID,
        )
        payment = Payment.objects.create(
            order=order,
            brand=self.brand,
            user=self.referee,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal(amount),
            currency="USD",
        )
        return order, payment

    def _activate(self, *, target: int, entry: str) -> UserChallenge:
        from apps.referrals.gamification import (
            activate_challenge_from_wallet,
            choose_challenge_tier,
        )

        tier = ChallengeTier.objects.create(
            program=self.challenge_program,
            target_referrals=target,
            entry_fee=Decimal(entry),
            display_order=target,
        )
        UserChallenge.objects.create(
            user=self.referrer,
            brand=self.brand,
            program=self.challenge_program,
            status=UserChallenge.Status.OFFERED,
        )
        choose_challenge_tier(
            user_id=self.referrer.pk, brand_id=self.brand.pk, tier_id=tier.pk
        )
        challenge = activate_challenge_from_wallet(
            user_id=self.referrer.pk, brand_id=self.brand.pk
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.challenge_frozen_balance, Decimal(entry))
        return challenge

    def test_success_returns_entry_and_cashes_challenge_reward_directly(self):
        challenge = self._activate(target=1, entry="50.00")
        _order, payment = self._purchase("100.00")

        self.assertEqual(
            award_level_one_referral_for_payment(payment_id=str(payment.payment_id)),
            Decimal("1"),
        )
        challenge.refresh_from_db()
        self.wallet.refresh_from_db()
        reward = ReferralReward.objects.get(order__payments=payment)

        self.assertEqual(challenge.status, UserChallenge.Status.SUCCEEDED)
        self.assertEqual(challenge.reward_amount, Decimal("13.00"))
        self.assertEqual(self.wallet.challenge_frozen_balance, Decimal("0"))
        self.assertEqual(self.wallet.balance, Decimal("113.00"))
        self.assertEqual(reward.reward_type, "challenge_point")
        self.assertEqual(reward.status, ReferralReward.RewardStatus.PROCESSED)
        self.assertTrue(reward.is_cashed_out)

    def test_failed_challenge_returns_entry_and_discards_provisional_points(self):
        from django.utils import timezone
        from apps.referrals.gamification import settle_challenge

        challenge = self._activate(target=3, entry="50.00")
        _order, payment = self._purchase("100.00")
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))

        reward = ReferralReward.objects.get(order__payments=payment)
        self.assertEqual(reward.status, ReferralReward.RewardStatus.PENDING)
        UserChallenge.objects.filter(pk=challenge.pk).update(
            ends_at=timezone.now() - timedelta(seconds=1)
        )
        settle_challenge(challenge_id=challenge.pk)

        challenge.refresh_from_db()
        self.wallet.refresh_from_db()
        reward.refresh_from_db()
        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        referral = Referral.objects.get(referee=self.referee, brand=self.brand)
        self.assertEqual(challenge.status, UserChallenge.Status.FAILED)
        self.assertEqual(challenge.reward_amount, Decimal("0"))
        self.assertEqual(self.wallet.challenge_frozen_balance, Decimal("0"))
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(reward.status, ReferralReward.RewardStatus.CANCELLED)
        self.assertEqual(account.lifetime_points, Decimal("0"))
        self.assertEqual(referral.referrer_reward_amount, Decimal("0"))

    def test_repeat_purchase_by_same_referee_does_not_advance_challenge_twice(self):
        challenge = self._activate(target=3, entry="50.00")
        _order, first_payment = self._purchase("100.00")
        award_level_one_referral_for_payment(payment_id=str(first_payment.payment_id))

        _order, second_payment = self._purchase("100.00")
        award_level_one_referral_for_payment(payment_id=str(second_payment.payment_id))

        challenge.refresh_from_db()
        first_reward = ReferralReward.objects.get(order__payments=first_payment)
        second_reward = ReferralReward.objects.get(order__payments=second_payment)
        self.assertEqual(challenge.successful_referrals, 1)
        self.assertEqual(first_reward.reward_type, "challenge_point")
        self.assertEqual(first_reward.status, ReferralReward.RewardStatus.PENDING)
        self.assertEqual(second_reward.reward_type, "normal_point")
        self.assertEqual(second_reward.cash_value, Decimal("8.00"))

    def test_purchases_after_target_use_normal_percentage_and_pill_rule(self):
        self._activate(target=1, entry="50.00")
        _order, first_payment = self._purchase("100.00")
        award_level_one_referral_for_payment(payment_id=str(first_payment.payment_id))

        _order, extra_payment = self._purchase("100.00")
        award_level_one_referral_for_payment(payment_id=str(extra_payment.payment_id))
        extra_reward = ReferralReward.objects.get(order__payments=extra_payment)
        self.wallet.refresh_from_db()

        self.assertEqual(extra_reward.reward_type, "normal_point")
        self.assertEqual(extra_reward.cash_value, Decimal("8.00"))
        self.assertFalse(extra_reward.is_cashed_out)
        self.assertEqual(self.wallet.balance, Decimal("113.00"))

    def test_join_window_policy_excludes_preexisting_referral_from_challenge(self):
        self.challenge_program.require_referral_join_during_challenge = True
        self.challenge_program.save(
            update_fields=["require_referral_join_during_challenge", "updated_at"]
        )
        challenge = self._activate(target=1, entry="50.00")

        _order, payment = self._purchase("100.00")
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))

        challenge.refresh_from_db()
        reward = ReferralReward.objects.get(order__payments=payment)
        self.assertEqual(challenge.successful_referrals, 0)
        self.assertEqual(challenge.status, UserChallenge.Status.ACTIVE)
        self.assertEqual(reward.reward_type, "normal_point")
        self.assertEqual(reward.cash_value, Decimal("8.00"))
