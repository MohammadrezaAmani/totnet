"""Seed a conservative, usable referral and rewards program per brand."""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F

from apps.brands.models import Brand
from apps.referrals.models import (
    Achievement,
    ReferralLevel,
    ReferralProgram,
    RewardBoxCapacity,
    RewardService,
)
from apps.referrals.services import (
    RewardConfigurationError,
    set_active_reference_service,
    validate_reward_service_configuration,
)
from apps.subscriptions.models import SubscriptionPlan
from apps.vpn_providers.models import VPNProvider

DEFAULT_LEVELS = (
    {
        "level": 1,
        "name": "معرف فعال",
        "min_referrals": 1,
        "min_lifetime_points": Decimal("0.1"),
        "min_conversion_rate": Decimal("0"),
        "reward_multiplier": Decimal("1.00"),
    },
    {
        "level": 2,
        "name": "معرف حرفه‌ای",
        "min_referrals": 5,
        "min_lifetime_points": Decimal("3"),
        "min_conversion_rate": Decimal("20"),
        "reward_multiplier": Decimal("1.10"),
    },
    {
        "level": 3,
        "name": "سفیر برند",
        "min_referrals": 15,
        "min_lifetime_points": Decimal("10"),
        "min_conversion_rate": Decimal("30"),
        "reward_multiplier": Decimal("1.20"),
    },
)

DEFAULT_ACHIEVEMENTS = (
    {
        "name": "اولین معرفی",
        "description": "یک دوست را با لینک اختصاصی خود دعوت کنید.",
        "achievement_type": Achievement.AchievementType.REFERRAL,
        "requirements": {"referrals": 1},
        "reward_points": 0,
    },
    {
        "name": "اولین خرید معرفی‌شده",
        "description": "اولین خرید سودآور یکی از دوستان معرفی‌شده را ثبت کنید.",
        "achievement_type": Achievement.AchievementType.REFERRAL,
        "requirements": {"conversions": 1},
        "reward_points": 0,
    },
    {
        "name": "سه خرید",
        "description": "سه خرید اشتراک انجام دهید.",
        "achievement_type": Achievement.AchievementType.PURCHASE,
        "requirements": {"purchases": 3},
        "reward_points": 0,
    },
    {
        "name": "معرف پنج دوست",
        "description": "پنج دوست را با لینک خود دعوت کنید.",
        "achievement_type": Achievement.AchievementType.REFERRAL,
        "requirements": {"referrals": 5},
        "reward_points": 0,
    },
    {
        "name": "اندوختهٔ امتیاز",
        "description": "پنج امتیاز را از سود واقعی خریدهای معرفی‌شده جمع کنید.",
        "achievement_type": Achievement.AchievementType.MILESTONE,
        "requirements": {"lifetime_points": 5},
        "reward_points": 1,
    },
    {
        "name": "معرف تأثیرگذار",
        "description": "ده امتیاز را از سود واقعی خریدهای معرفی‌شده جمع کنید.",
        "achievement_type": Achievement.AchievementType.MILESTONE,
        "requirements": {"lifetime_points": 10},
        "reward_points": 2,
    },
    {
        "name": "سفیر ممتاز",
        "description": "بیست‌وپنج امتیاز را از سود واقعی خریدهای معرفی‌شده جمع کنید.",
        "achievement_type": Achievement.AchievementType.MILESTONE,
        "requirements": {"lifetime_points": 25},
        "reward_points": 3,
    },
)


class Command(BaseCommand):
    help = "Create a conservative default referral, points, levels, and achievement program"

    def add_arguments(self, parser):
        parser.add_argument("--brand", help="Limit setup to one brand slug")

    def handle(self, *args, **options):
        brands = Brand.objects.all().order_by("slug")
        if options["brand"]:
            brands = brands.filter(slug=options["brand"])
            if not brands.exists():
                raise CommandError(f"Brand not found: {options['brand']}")

        configured = 0
        for brand in brands:
            candidate_plan = (
                SubscriptionPlan.objects.filter(
                    brand=brand,
                    is_active=True,
                    is_visible=True,
                    upstream_group_id__gt="",
                    price__gt=F("upstream_cost"),
                    vpn_provider__status=VPNProvider.ProviderStatus.ACTIVE,
                )
                .annotate(
                    reward_cost_ratio=ExpressionWrapper(
                        F("upstream_cost") / (F("price") - F("upstream_cost")),
                        output_field=DecimalField(max_digits=20, decimal_places=8),
                    )
                )
                .order_by("reward_cost_ratio", "price", "id")
                .first()
            )
            if not candidate_plan:
                self.stderr.write(
                    self.style.WARNING(
                        f"Skipped {brand.slug}: no active, visible, profitable provider plan."
                    )
                )
                continue

            with transaction.atomic():
                program, _ = ReferralProgram.objects.get_or_create(
                    brand=brand,
                    defaults={
                        "name": "معرفی دوستان و امتیازها",
                        "description": "با خرید سودآور دوستان و فعالیت در ربات امتیاز بگیرید و پلن جایزه دریافت کنید.",
                        "is_active": False,
                        "require_purchase": True,
                        "minimum_purchase_amount": Decimal("0"),
                        "conversion_window_days": 30,
                        "max_referrals_per_day": 10,
                        "max_referrals_per_month": 100,
                        "enable_level_rewards": True,
                        "require_phone_verification": False,
                    },
                )
                service = program.reference_service
                if service is not None:
                    try:
                        validate_reward_service_configuration(service)
                    except RewardConfigurationError:
                        service = None
                activating_default = service is None
                plan = service.plan if service else candidate_plan
                if service is None:
                    service, _ = RewardService.objects.get_or_create(
                        brand=brand,
                        plan=plan,
                        defaults={"free_points": Decimal("10"), "is_active": True},
                    )
                if not service.box_capacities.exists():
                    RewardBoxCapacity.objects.create(
                        service=service,
                        sequence=1,
                        capacity=Decimal("10"),
                    )
                if activating_default:
                    set_active_reference_service(
                        brand_id=brand.pk, service_id=service.pk
                    )

                if activating_default and not program.enable_level_rewards:
                    program.enable_level_rewards = True
                    program.save(update_fields=("enable_level_rewards", "updated_at"))
                for defaults in DEFAULT_LEVELS:
                    ReferralLevel.objects.get_or_create(
                        program=program,
                        level=defaults["level"],
                        defaults=defaults,
                    )
                for defaults in DEFAULT_ACHIEVEMENTS:
                    Achievement.objects.get_or_create(
                        brand=brand,
                        name=defaults["name"],
                        defaults={**defaults, "is_active": True},
                    )
            configured += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Configured {brand.slug}: {plan.name} ({plan.currency}) earns points; "
                    f"{service.free_points:g} completed points redeem this plan."
                )
            )

        if configured == 0:
            raise CommandError("No brands had a usable profitable plan to configure.")
