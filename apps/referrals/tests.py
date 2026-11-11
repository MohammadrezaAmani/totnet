import uuid
from django.utils import timezone
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
        self.assertFalse(program.is_active)
        self.assertIsNone(program.purchase_reward_percent)
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

    def test_royal_unlimited_uses_default_eight_percent_for_every_user_duration(self):
        """Real confirmed-purchase award, not only a display/helper test."""
        for users, months in ((1, 1), (2, 2), (3, 3), (4, 1)):
            with self.subTest(users=users, months=months):
                self.plan.plan_type = SubscriptionPlan.PlanType.UNLIMITED
                self.plan.service_category = SubscriptionPlan.ServiceCategory.ROYAL
                self.plan.duration_unit = SubscriptionPlan.DurationUnit.MONTHS
                self.plan.duration_value = months
                self.plan.max_users = users
                self.plan.save(update_fields=("plan_type", "service_category", "duration_unit", "duration_value", "max_users"))
                _order, payment = self._purchase("100.00")
                self.assertEqual(award_level_one_referral_for_payment(payment_id=str(payment.payment_id)), Decimal("1"))
                reward = ReferralReward.objects.get(order=payment.order)
                self.assertEqual(reward.cash_value, Decimal("8.00"))
                self.assertIn("8%", reward.notes)

    def test_royal_unlimited_uses_service_rate_without_hardcoded_four_percent(self):
        from apps.subscriptions.policy import effective_reward_percent
        self.plan.plan_type = SubscriptionPlan.PlanType.UNLIMITED
        self.plan.service_category = SubscriptionPlan.ServiceCategory.ROYAL
        self.plan.save(update_fields=("plan_type", "service_category"))
        _order, payment = self._purchase("100.00")
        self.assertEqual(effective_reward_percent(payment.order, Decimal("13")), Decimal("8"))
        self.plan.service_category = SubscriptionPlan.ServiceCategory.NORMAL
        self.plan.save(update_fields=("service_category",))
        self.assertEqual(effective_reward_percent(payment.order, Decimal("13")), Decimal("8"))

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
        from apps.orders.models import PaymentCard

        self.brand = Brand.objects.create(
            name="Challenge Test", slug=f"challenge-test-{uuid.uuid4().hex[:8]}",
            contact_email="challenge@example.invalid", bot_token=f"token-{uuid.uuid4().hex}",
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
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, name="Purchase", price=Decimal("100.00"),
            upstream_cost=Decimal("40.00"), currency="USD", duration_value=30,
            duration_unit=SubscriptionPlan.DurationUnit.DAYS,
        )
        self.program = ReferralProgram.objects.create(
            brand=self.brand, is_active=True,
            purchase_reward_percent=Decimal("8"), minimum_purchase_amount=Decimal("0"),
        )
        self.challenge_program = ChallengeProgram.objects.create(
            brand=self.brand, name="پراپزینو", is_active=True, duration_days=7,
            success_multiplier=Decimal("2"), failure_multiplier=Decimal("0.5"),
        )
        self.card = PaymentCard.objects.create(
            brand=self.brand, bank_name="Test", card_number="0000111122223333",
            cardholder_name="Test Holder", is_active=True,
        )
        self.wallet = Wallet.objects.create(
            user=self.referrer, brand=self.brand,
            currency=self.brand.currency, balance=Decimal("100.00"),
        )

    def _purchase(self, amount="100.00"):
        order = Order.objects.create(
            brand=self.brand, user=self.referee, plan=self.plan,
            original_price=Decimal(amount), final_price=Decimal(amount),
            currency="USD", status=Order.OrderStatus.PAID,
        )
        payment = Payment.objects.create(
            order=order, brand=self.brand, user=self.referee,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal(amount), currency="USD",
        )
        return order, payment

    def _activate(self, *, target: int, entry="50.00", preexisting_referral=False):
        from apps.orders.approval import review_direct_deposit
        from apps.referrals.gamification import (
            choose_challenge_tier, create_challenge_entry_payment,
        )

        if preexisting_referral:
            self.assertTrue(attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            ))
        tier = ChallengeTier.objects.create(
            program=self.challenge_program, target_referrals=target,
            entry_fee=Decimal(entry), display_order=target,
        )
        UserChallenge.objects.create(
            user=self.referrer, brand=self.brand, program=self.challenge_program,
            status=UserChallenge.Status.OFFERED,
        )
        challenge = choose_challenge_tier(
            user_id=self.referrer.pk, brand_id=self.brand.pk, tier_id=tier.pk,
        )
        payment = create_challenge_entry_payment(
            user_id=self.referrer.pk, brand_id=self.brand.pk,
            challenge_id=challenge.pk, card_id=self.card.pk,
            receipt_file="tg-file-id",
        )
        self.assertIsNone(payment.wallet_id)
        self.assertIsNone(payment.order_id)
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        review_direct_deposit(
            payment_id=payment.pk, brand_id=self.brand.pk,
            reviewer_id=self.referrer.pk, approve=True,
        )
        challenge.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(challenge.status, UserChallenge.Status.ACTIVE)
        self.assertEqual(self.wallet.challenge_frozen_balance, Decimal(entry))
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        if not preexisting_referral:
            self.assertTrue(attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            ))
        return challenge

    def _normal_pill(self, value):
        return ReferralReward.objects.create(
            user=self.referrer, brand=self.brand,
            reward_type="normal_point", amount=Decimal("1"),
            cash_value=Decimal(value), currency=self.brand.currency,
            status=ReferralReward.RewardStatus.PROCESSED,
            processed_at=timezone.now(),
        )

    def _purchase_for_challenge(self, user):
        if user.pk != self.referee.pk:
            self.assertTrue(attribute_referral(
                user_id=user.pk, brand_id=self.brand.pk, code=self.link.code,
            ))
        order = Order.objects.create(
            brand=self.brand, user=user, plan=self.plan,
            original_price=Decimal("100"), final_price=Decimal("100"),
            currency=self.brand.currency, status=Order.OrderStatus.PAID,
        )
        payment = Payment.objects.create(
            order=order, brand=self.brand, user=user,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status=Payment.PaymentStatus.CONFIRMED,
            amount=Decimal("100"), currency=self.brand.currency,
        )
        self.assertEqual(award_level_one_referral_for_payment(
            payment_id=str(payment.payment_id),
        ), Decimal("1"))
        return order

    def _end_challenge(self, challenge):
        from apps.referrals.gamification import settle_challenge
        UserChallenge.objects.filter(pk=challenge.pk).update(
            ends_at=timezone.now() - timedelta(seconds=1),
        )
        return settle_challenge(challenge_id=challenge.pk)

    def test_unfinished_challenge_does_not_enter_ordinary_pill_box(self):
        from apps.referrals.gamification import cash_out_completed_pills
        challenge = self._activate(target=3)
        existing = self._normal_pill("3.00")
        order, payment = self._purchase()
        self.assertEqual(award_level_one_referral_for_payment(
            payment_id=str(payment.payment_id),
        ), Decimal("1"))
        pending = ReferralReward.objects.get(order=order)
        self.assertEqual(pending.reward_type, "challenge_point")
        self.assertEqual(pending.status, ReferralReward.RewardStatus.PENDING)
        self.assertFalse(pending.is_cashed_out)
        self.assertEqual(cash_out_completed_pills(
            user_id=self.referrer.pk, brand_id=self.brand.pk,
        ), Decimal("0"))
        existing.refresh_from_db()
        self.assertFalse(existing.is_cashed_out)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100"))
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, UserChallenge.Status.ACTIVE)

    def test_challenge_pill_completes_existing_two_pill_box(self):
        challenge = self._activate(target=3)
        first = self._normal_pill("3.00")
        second = self._normal_pill("5.00")
        order, payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        reward = ReferralReward.objects.get(order=order)
        self.assertEqual(reward.cash_value, Decimal("16.00"))
        self.assertEqual(reward.status, ReferralReward.RewardStatus.PENDING)
        self._end_challenge(challenge)
        first.refresh_from_db()
        second.refresh_from_db()
        reward.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertTrue(first.is_cashed_out)
        self.assertTrue(second.is_cashed_out)
        self.assertTrue(reward.is_cashed_out)
        self.assertEqual(reward.cash_value, Decimal("4.00"))
        self.assertEqual(self.wallet.balance, Decimal("162.00"))
        self.assertEqual(ReferralReward.objects.filter(
            user=self.referrer, brand=self.brand, is_cashed_out=False,
            reward_type__in=["normal_point", "profile_point"],
            status=ReferralReward.RewardStatus.PROCESSED,
        ).count(), 0)

    def test_unfilled_box_keeps_released_pill_until_next_ordinary_pills(self):
        from apps.referrals.gamification import cash_out_completed_pills
        challenge = self._activate(target=3)
        order, payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        self._end_challenge(challenge)
        challenge_pill = ReferralReward.objects.get(order=order)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150.00"))
        self.assertFalse(challenge_pill.is_cashed_out)
        one = self._normal_pill("2.00")
        self.assertEqual(cash_out_completed_pills(
            user_id=self.referrer.pk, brand_id=self.brand.pk,
        ), Decimal("0"))
        two = self._normal_pill("3.00")
        self.assertEqual(cash_out_completed_pills(
            user_id=self.referrer.pk, brand_id=self.brand.pk,
        ), Decimal("9.00"))
        for reward in (challenge_pill, one, two):
            reward.refresh_from_db()
            self.assertTrue(reward.is_cashed_out)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("159.00"))

    def test_success_releases_pills_into_partly_filled_box(self):
        challenge = self._activate(target=3)
        old = self._normal_pill("3.00")
        order, payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        for index in (2, 3):
            user = User.objects.create_user(
                username=f"success-referee-{index}-{uuid.uuid4().hex[:8]}",
                brand=self.brand,
            )
            self._purchase_for_challenge(user)
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, UserChallenge.Status.SUCCEEDED)
        self.assertEqual(challenge.reward_amount, Decimal("48.00"))
        rewards = list(ReferralReward.objects.filter(
            user=self.referrer, brand=self.brand, reward_type="normal_point",
        ).exclude(pk=old.pk).order_by("pk"))
        self.assertEqual(len(rewards), 3)
        self.assertTrue(all(item.cash_value == Decimal("16.00") for item in rewards))
        old.refresh_from_db()
        self.assertTrue(old.is_cashed_out)
        self.assertEqual([item.is_cashed_out for item in rewards], [True, True, False])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("185.00"))

    def test_released_challenge_pills_follow_existing_ordinary_pills(self):
        challenge = self._activate(target=3)
        old = self._normal_pill("1.00")
        first_order, payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        new_referee = User.objects.create_user(
            username=f"challenge-second-{uuid.uuid4().hex[:8]}", brand=self.brand,
        )
        second_order = self._purchase_for_challenge(new_referee)
        ordinary = self._normal_pill("2.00")
        first_reward = ReferralReward.objects.get(order=first_order)
        second_reward = ReferralReward.objects.get(order=second_order)
        self._end_challenge(challenge)
        for reward in (old, ordinary, first_reward, second_reward):
            reward.refresh_from_db()
        self.assertTrue(old.is_cashed_out)
        self.assertTrue(ordinary.is_cashed_out)
        self.assertTrue(first_reward.is_cashed_out)
        self.assertFalse(second_reward.is_cashed_out)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("157.00"))
        self.assertEqual(second_reward.cash_value, Decimal("4.00"))

    def test_multiple_challenge_boxes_are_settled_once_without_value_loss(self):
        from apps.referrals.gamification import settle_challenge
        challenge = self._activate(target=8)
        prior = [self._normal_pill("3.00"), self._normal_pill("5.00")]
        orders = []
        for i in range(5):
            if i == 0:
                order, payment = self._purchase()
                self.assertEqual(award_level_one_referral_for_payment(
                    payment_id=str(payment.payment_id),
                ), Decimal("1"))
            else:
                user = User.objects.create_user(
                    username=f"challenge-extra-{i}-{uuid.uuid4().hex[:8]}",
                    brand=self.brand,
                )
                order = self._purchase_for_challenge(user)
            orders.append(order)
        self._end_challenge(challenge)
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, UserChallenge.Status.FAILED)
        self.assertEqual(challenge.reward_amount, Decimal("20.00"))
        rewards = list(ReferralReward.objects.filter(
            order__in=orders,
        ).order_by("pk"))
        self.assertEqual(sum(r.is_cashed_out for r in rewards), 4)
        for item in prior:
            item.refresh_from_db()
            self.assertTrue(item.is_cashed_out)
        self.assertEqual(sum(r.is_cashed_out for r in rewards), 4)
        self.assertEqual(sum(r.cash_value for r in rewards), Decimal("20"))
        self.assertEqual(sum(r.cash_value for r in rewards if not r.is_cashed_out), Decimal("4"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("174.00"))
        self.assertEqual(WalletTransaction.objects.filter(
            wallet=self.wallet,
            transaction_type=WalletTransaction.TransactionType.REFERRAL_REWARD,
        ).count(), 2)
        settle_challenge(challenge_id=challenge.pk)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("174.00"))
        self.assertEqual(WalletTransaction.objects.filter(
            wallet=self.wallet,
            transaction_type=WalletTransaction.TransactionType.REFERRAL_REWARD,
        ).count(), 2)

    def test_challenge_purchase_does_not_emit_ordinary_pill_notification(self):
        from apps.referrals.tasks import _earned_pill_purchase_message
        self._activate(target=1)
        order, payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        self.assertIsNone(_earned_pill_purchase_message(
            order=order, referrer=self.referrer,
        ))

    def test_entry_is_cash_only_and_confirmation_is_idempotent(self):
        from apps.orders.approval import ReceiptReviewError, review_direct_deposit
        challenge = self._activate(target=3)
        with self.assertRaises(ReceiptReviewError):
            review_direct_deposit(
                payment_id=challenge.entry_payment_id, brand_id=self.brand.pk,
                reviewer_id=self.referrer.pk, approve=True,
            )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.challenge_frozen_balance, Decimal("50.00"))
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_success_returns_entry_and_queues_pill_for_normal_box(self):
        challenge = self._activate(target=1)
        _, payment = self._purchase()
        self.assertEqual(award_level_one_referral_for_payment(
            payment_id=str(payment.payment_id)), Decimal("1"))
        challenge.refresh_from_db()
        self.wallet.refresh_from_db()
        reward = ReferralReward.objects.get(order__payments=payment)
        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        self.assertEqual(challenge.status, UserChallenge.Status.SUCCEEDED)
        self.assertEqual(challenge.reward_amount, Decimal("16.00"))
        self.assertEqual(self.wallet.challenge_frozen_balance, Decimal("0"))
        self.assertEqual(self.wallet.balance, Decimal("150.00"))
        self.assertEqual(reward.reward_type, "normal_point")
        self.assertEqual(reward.cash_value, Decimal("16.00"))
        self.assertEqual(reward.status, ReferralReward.RewardStatus.PROCESSED)
        self.assertFalse(reward.is_cashed_out)
        self.assertEqual(account.lifetime_points, Decimal("1"))

    def test_active_challenge_freezes_service_rates_and_multipliers_at_activation(self):
        from apps.referrals.models import ServiceRewardRate
        rate = ServiceRewardRate.objects.create(
            brand=self.brand, service_category=self.plan.service_category,
            plan_type="all", percent=Decimal("6"),
        )
        challenge = self._activate(target=3)
        self.program.purchase_reward_percent = Decimal("20")
        self.program.save(update_fields=["purchase_reward_percent"])
        rate.percent = Decimal("12")
        rate.save(update_fields=["percent"])
        self.challenge_program.success_multiplier = Decimal("3")
        self.challenge_program.failure_multiplier = Decimal("0.2")
        self.challenge_program.save(update_fields=["success_multiplier", "failure_multiplier"])
        order, payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        reward = ReferralReward.objects.get(order=order)
        event = challenge.referral_events.get(order=order)
        self.assertEqual(reward.cash_value, Decimal("12.00"))
        self.assertEqual(event.fallback_reward_value, Decimal("3.00"))
        self._end_challenge(challenge)
        reward.refresh_from_db()
        self.assertEqual(reward.cash_value, Decimal("3.00"))

    def test_failed_challenge_returns_entry_and_preserves_discounted_pill(self):
        from apps.referrals.gamification import settle_challenge
        challenge = self._activate(target=3)
        _, payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        reward = ReferralReward.objects.get(order__payments=payment)
        self.assertEqual(reward.status, ReferralReward.RewardStatus.PENDING)
        # Changing percentages during the week must not retroactively reprice the pill.
        self.program.purchase_reward_percent = Decimal("20")
        self.program.save(update_fields=["purchase_reward_percent"])
        UserChallenge.objects.filter(pk=challenge.pk).update(
            ends_at=timezone.now() - timedelta(seconds=1),
        )
        settle_challenge(challenge_id=challenge.pk)
        challenge.refresh_from_db()
        self.wallet.refresh_from_db()
        reward.refresh_from_db()
        account = RewardAccount.objects.get(user=self.referrer, brand=self.brand)
        referral = Referral.objects.get(referee=self.referee, brand=self.brand)
        self.assertEqual(challenge.status, UserChallenge.Status.FAILED)
        self.assertEqual(challenge.reward_amount, Decimal("4.00"))
        self.assertEqual(self.wallet.challenge_frozen_balance, Decimal("0"))
        self.assertEqual(self.wallet.balance, Decimal("150.00"))
        self.assertEqual(reward.status, ReferralReward.RewardStatus.PROCESSED)
        self.assertEqual(reward.cash_value, Decimal("4.00"))
        self.assertFalse(reward.is_cashed_out)
        self.assertEqual(account.lifetime_points, Decimal("1"))
        self.assertEqual(referral.referrer_reward_amount, Decimal("1"))

    def test_repeat_purchase_uses_ordinary_rate(self):
        challenge = self._activate(target=3)
        _, first_payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(first_payment.payment_id))
        _, second_payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(second_payment.payment_id))
        challenge.refresh_from_db()
        first_reward = ReferralReward.objects.get(order__payments=first_payment)
        second_reward = ReferralReward.objects.get(order__payments=second_payment)
        self.assertEqual(challenge.successful_referrals, 1)
        self.assertEqual(first_reward.reward_type, "challenge_point")
        self.assertEqual(first_reward.status, ReferralReward.RewardStatus.PENDING)
        self.assertEqual(second_reward.reward_type, "normal_point")
        self.assertEqual(second_reward.cash_value, Decimal("8.00"))

    def test_purchases_after_target_stay_normal(self):
        self._activate(target=1)
        _, first_payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(first_payment.payment_id))
        _, extra_payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(extra_payment.payment_id))
        extra_reward = ReferralReward.objects.get(order__payments=extra_payment)
        self.wallet.refresh_from_db()
        self.assertEqual(extra_reward.reward_type, "normal_point")
        self.assertEqual(extra_reward.cash_value, Decimal("8.00"))
        self.assertFalse(extra_reward.is_cashed_out)
        self.assertEqual(self.wallet.balance, Decimal("150.00"))

    def test_expired_challenge_reconciles_delayed_payment_reward(self):
        from apps.referrals.tasks import reconcile_confirmed_challenge_purchases
        from apps.referrals.gamification import settle_challenge
        challenge = self._activate(target=1)
        _, payment = self._purchase()
        starts = timezone.now() - timedelta(days=7)
        ends = timezone.now() - timedelta(seconds=1)
        UserChallenge.objects.filter(pk=challenge.pk).update(
            starts_at=starts, ends_at=ends,
        )
        Referral.objects.filter(referee=self.referee, brand=self.brand).update(
            created_at=starts + timedelta(days=1),
        )
        Payment.objects.filter(pk=payment.pk).update(
            verified_at=starts + timedelta(days=2),
        )
        self.assertEqual(reconcile_confirmed_challenge_purchases(challenge_id=challenge.pk), 1)
        settle_challenge(challenge_id=challenge.pk)
        challenge.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(challenge.status, UserChallenge.Status.SUCCEEDED)
        self.assertEqual(challenge.reward_amount, Decimal("16.00"))
        self.assertEqual(self.wallet.balance, Decimal("150.00"))
        self.assertEqual(reconcile_confirmed_challenge_purchases(challenge_id=challenge.pk), 0)

    def test_preexisting_referral_gets_ordinary_reward(self):
        challenge = self._activate(target=1, preexisting_referral=True)
        _, payment = self._purchase()
        award_level_one_referral_for_payment(payment_id=str(payment.payment_id))
        challenge.refresh_from_db()
        reward = ReferralReward.objects.get(order__payments=payment)
        self.assertEqual(challenge.successful_referrals, 0)
        self.assertEqual(challenge.status, UserChallenge.Status.ACTIVE)
        self.assertEqual(reward.reward_type, "normal_point")
        self.assertEqual(reward.cash_value, Decimal("8.00"))
