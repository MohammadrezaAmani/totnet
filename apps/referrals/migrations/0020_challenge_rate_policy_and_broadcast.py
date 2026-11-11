from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def freeze_active_challenges(apps, schema_editor):
    Challenge = apps.get_model("referrals", "UserChallenge")
    ReferralProgram = apps.get_model("referrals", "ReferralProgram")
    ServiceRate = apps.get_model("referrals", "ServiceRewardRate")
    for challenge in Challenge.objects.filter(status="active").select_related("program").iterator():
        base = ReferralProgram.objects.filter(brand_id=challenge.brand_id, is_active=True).first()
        if base is None or base.purchase_reward_percent is None:
            raise RuntimeError(
                f"Cannot migrate active challenge {challenge.pk}: missing referral rate. "
                "Configure its brand referral program before migrating."
            )
        rates = ServiceRate.objects.filter(brand_id=challenge.brand_id, is_active=True)
        challenge.rate_snapshot = {
            "base_percent": str(base.purchase_reward_percent),
            "overrides": [
                {
                    "service_category": r.service_category,
                    "plan_type": r.plan_type,
                    "percent": str(r.percent),
                }
                for r in rates
            ],
        }
        challenge.success_multiplier_snapshot = challenge.program.success_multiplier
        challenge.failure_multiplier_snapshot = challenge.program.failure_multiplier
        challenge.save(update_fields=[
            "rate_snapshot", "success_multiplier_snapshot", "failure_multiplier_snapshot"
        ])


class Migration(migrations.Migration):
    dependencies = [
        ("referrals", "0019_challenge_entry_cash_and_tiers"),
    ]
    operations = [
        migrations.AlterField(
            model_name="referralprogram", name="purchase_reward_percent",
            field=models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                      help_text="درصد پایه پاداش معرفی؛ باید از مدیریت برند تنظیم شود."),
        ),
        migrations.AlterField(
            model_name="challengeprogram", name="is_active",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="challengeprogram", name="offer_delay_minutes",
            field=models.PositiveIntegerField(null=True, blank=True,
                help_text="مثلاً ۶۰ = یک ساعت، ۳۰۰ = پنج ساعت؛ از مبدأ انتخاب‌شده."),
        ),
        migrations.AlterField(
            model_name="challengeprogram", name="duration_days",
            field=models.PositiveSmallIntegerField(null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="challengeprogram", name="success_multiplier",
            field=models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="challengeprogram", name="failure_multiplier",
            field=models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="userchallenge", name="rate_snapshot",
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name="userchallenge", name="success_multiplier_snapshot",
            field=models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="userchallenge", name="failure_multiplier_snapshot",
            field=models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True),
        ),
        migrations.RunPython(freeze_active_challenges, migrations.RunPython.noop),
        migrations.RemoveField(model_name="challengeprogram", name="reward_percent"),
        migrations.RemoveField(model_name="challengeprogram", name="offer_delay_days"),
        migrations.RemoveField(model_name="userchallenge", name="reward_percent"),
        migrations.CreateModel(
            name="ChallengeBroadcast",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True)),
                ("status", models.CharField(max_length=16, default="queued", choices=[
                    ("queued", "Queued"), ("running", "Running"),
                    ("completed", "Completed"), ("cancelled", "Cancelled")
                ])),
                ("eligible_count", models.PositiveIntegerField(default=0)),
                ("scanned_count", models.PositiveIntegerField(default=0)),
                ("queued_count", models.PositiveIntegerField(default=0)),
                ("last_user_id", models.BigIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("finished_at", models.DateTimeField(null=True, blank=True)),
                ("program", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="broadcasts", to="referrals.challengeprogram")),
                ("started_by", models.ForeignKey(on_delete=django.db.models.deletion.SET_NULL,
                    null=True, related_name="challenge_broadcasts_started", to="accounts.user")),
            ],
            options={"db_table": "challenge_broadcasts"},
        ),
        migrations.AddConstraint(
            model_name="challengebroadcast",
            constraint=models.UniqueConstraint(
                fields=["program"], condition=models.Q(status__in=["queued", "running"]),
                name="uniq_active_challenge_broadcast",
            ),
        ),
        migrations.AddIndex(
            model_name="challengebroadcast", index=models.Index(
                fields=["status", "updated_at"], name="challenge_b_status_931bdd_idx"
            ),
        ),
    ]
