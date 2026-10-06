from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def seed_challenge_defaults(apps, schema_editor):
    Brand = apps.get_model("brands", "Brand")
    ChallengeProgram = apps.get_model("referrals", "ChallengeProgram")
    ChallengeTier = apps.get_model("referrals", "ChallengeTier")
    defaults = ((1, 50000, 1), (3, 150000, 2), (5, 250000, 3), (8, 350000, 4))
    for brand in Brand.objects.all():
        uses_toman_amounts = brand.currency in {"T", "IRT", "IRR"}
        program, _ = ChallengeProgram.objects.get_or_create(
            brand_id=brand.pk,
            defaults={
                "name": "پراپزینو",
                # The XMind entry amounts are specified in تومان. Do not silently
                # apply 50,000/150,000/... to USD or other-currency brands.
                "is_active": uses_toman_amounts,
                "offer_delay_days": 5,
                "duration_days": 7,
                "reward_percent": 13,
            },
        )
        if not uses_toman_amounts:
            continue
        for target, fee, order in defaults:
            ChallengeTier.objects.get_or_create(
                program_id=program.pk,
                target_referrals=target,
                defaults={"entry_fee": fee, "display_order": order, "is_active": True},
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0001_initial"),
        ("orders", "0011_wallet_challenge_frozen_balance"),
        ("referrals", "0008_userachievement_claim_count_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="referralprogram",
            name="purchase_reward_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=8,
                help_text="درصد ارزش نقدی هر امتیاز معرفی از مبلغ خرید مستقیم کاربر سطح یک.",
                max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name="referralreward",
            name="cash_value",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=20),
        ),
        migrations.AddField(
            model_name="referralreward",
            name="is_cashed_out",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="referralreward",
            name="cashed_out_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ChallengeProgram",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="پراپزینو", max_length=100)),
                ("is_active", models.BooleanField(default=True)),
                ("offer_delay_days", models.PositiveSmallIntegerField(default=5)),
                ("duration_days", models.PositiveSmallIntegerField(default=7)),
                ("reward_percent", models.DecimalField(decimal_places=2, default=13, max_digits=5)),
                ("intro_text", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brand", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="challenge_program", to="brands.brand")),
            ],
            options={"db_table": "challenge_programs"},
        ),
        migrations.CreateModel(
            name="ChallengeTier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("target_referrals", models.PositiveSmallIntegerField()),
                ("entry_fee", models.DecimalField(decimal_places=2, max_digits=20)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("program", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tiers", to="referrals.challengeprogram")),
            ],
            options={"db_table": "challenge_tiers", "ordering": ["display_order", "target_referrals"]},
        ),
        migrations.RunPython(seed_challenge_defaults, noop_reverse),
        migrations.CreateModel(
            name="UserChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("offered", "Offered"), ("declined", "Declined"), ("awaiting_funds", "Awaiting funds"), ("active", "Active"), ("succeeded", "Succeeded"), ("failed", "Failed")], default="offered", max_length=24)),
                ("offered_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("declined_at", models.DateTimeField(blank=True, null=True)),
                ("starts_at", models.DateTimeField(blank=True, null=True)),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("entry_amount", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("target_referrals", models.PositiveSmallIntegerField(default=0)),
                ("reward_percent", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("successful_referrals", models.PositiveSmallIntegerField(default=0)),
                ("reward_amount", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brand", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="user_challenges", to="brands.brand")),
                ("program", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="user_challenges", to="referrals.challengeprogram")),
                ("tier", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="user_challenges", to="referrals.challengetier")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="challenges", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "user_challenges"},
        ),
        migrations.CreateModel(
            name="ChallengeReferralEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reward_value", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("challenge", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referral_events", to="referrals.userchallenge")),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="challenge_referral_event", to="orders.order")),
                ("referral", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="challenge_events", to="referrals.referral")),
                ("reward", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="challenge_event", to="referrals.referralreward")),
            ],
            options={"db_table": "challenge_referral_events", "ordering": ["created_at"]},
        ),
        migrations.CreateModel(
            name="GamificationNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("notification_type", models.CharField(choices=[("survey", "Survey"), ("referral_intro", "Referral intro"), ("challenge_offer", "Challenge offer"), ("challenge_reminder", "Challenge reminder"), ("expiry_5d", "Subscription expiry 5d"), ("referral_reward", "Referral reward")], max_length=32)),
                ("dedupe_key", models.CharField(max_length=255, unique=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("brand", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="gamification_notifications", to="brands.brand")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="gamification_notifications", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "gamification_notifications"},
        ),
        migrations.CreateModel(
            name="ServiceSurvey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quality_rating", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("app_rating", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("support_rating", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brand", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_surveys", to="brands.brand")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_surveys", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "service_surveys"},
        ),
        migrations.AddConstraint(
            model_name="challengetier",
            constraint=models.UniqueConstraint(fields=("program", "target_referrals"), name="uniq_challenge_target_per_program"),
        ),
        migrations.AddConstraint(
            model_name="challengetier",
            constraint=models.CheckConstraint(condition=models.Q(entry_fee__gte=0), name="challenge_entry_fee_nonneg"),
        ),
        migrations.AddConstraint(
            model_name="userchallenge",
            constraint=models.UniqueConstraint(fields=("user", "program"), name="uniq_user_challenge_program"),
        ),
        migrations.AddIndex(
            model_name="userchallenge",
            index=models.Index(fields=["brand", "status", "ends_at"], name="user_challe_brand_i_95f9d1_idx"),
        ),
        migrations.AddIndex(
            model_name="userchallenge",
            index=models.Index(fields=["user", "status"], name="user_challe_user_id_c02c03_idx"),
        ),
        migrations.AddConstraint(
            model_name="challengereferralevent",
            constraint=models.UniqueConstraint(fields=("challenge", "referral"), name="uniq_challenge_referral_person"),
        ),
        migrations.AddIndex(
            model_name="gamificationnotification",
            index=models.Index(fields=["brand", "notification_type", "sent_at"], name="gamificatio_brand_i_b14651_idx"),
        ),
        migrations.AddConstraint(
            model_name="servicesurvey",
            constraint=models.UniqueConstraint(fields=("user", "brand"), name="uniq_service_survey_user_brand"),
        ),
        migrations.AddConstraint(
            model_name="servicesurvey",
            constraint=models.CheckConstraint(condition=models.Q(quality_rating__isnull=True) | (models.Q(quality_rating__gte=1) & models.Q(quality_rating__lte=5)), name="survey_quality_1_5"),
        ),
        migrations.AddConstraint(
            model_name="servicesurvey",
            constraint=models.CheckConstraint(condition=models.Q(app_rating__isnull=True) | (models.Q(app_rating__gte=1) & models.Q(app_rating__lte=5)), name="survey_app_1_5"),
        ),
        migrations.AddConstraint(
            model_name="servicesurvey",
            constraint=models.CheckConstraint(condition=models.Q(support_rating__isnull=True) | (models.Q(support_rating__gte=1) & models.Q(support_rating__lte=5)), name="survey_support_1_5"),
        ),
    ]
