from django.db import migrations, models
import django.db.models.deletion


def adjust_seeded_fees(apps, schema_editor):
    Tier = apps.get_model("referrals", "ChallengeTier")
    # Preserve any fee subsequently edited by the brand administrator.
    for target, old, new in ((3, 150000, 130000), (5, 250000, 200000), (8, 350000, 300000)):
        Tier.objects.filter(program__brand__currency__in=["T", "IRT", "IRR"],
                            target_referrals=target, entry_fee=old).update(entry_fee=new)


class Migration(migrations.Migration):
    dependencies = [("referrals", "0018_subscription_low_notification"), ("orders", "0015_order_wallet_opt_out")]
    operations = [
        migrations.AddField(
            model_name="userchallenge", name="entry_payment",
            field=models.OneToOneField(to="orders.payment", null=True, blank=True,
                                        on_delete=django.db.models.deletion.PROTECT,
                                        related_name="entry_challenge"),
        ),
        migrations.AddField(model_name="challengereferralevent", name="fallback_reward_value",
                            field=models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)),
        migrations.AddField(model_name="challengeprogram", name="success_multiplier",
                            field=models.DecimalField(default=2, decimal_places=2, max_digits=5)),
        migrations.AddField(model_name="challengeprogram", name="failure_multiplier",
                            field=models.DecimalField(default=0.5, decimal_places=2, max_digits=5)),
        migrations.AlterField(
            model_name="notificationrule", name="audience",
            field=models.CharField(max_length=20, default="all", choices=[
                ("all", "همه کاربران"), ("active", "دارای اشتراک فعال"),
                ("inactive", "بدون اشتراک فعال"), ("royal", "اشتراک رویال فعال"),
                ("normal", "اشتراک نرمال فعال"), ("iran_ip", "اشتراک آی‌پی ایران فعال"),
                ("never_paid", "بدون خرید موفق"), ("paid", "دارای خرید موفق"),
                ("profile_incomplete", "اطلاعات اولیه ناقص"),
                ("profile_initial", "اطلاعات اولیه کامل / پروفایل ناقص"),
                ("profile_complete", "پروفایل کامل"),
            ]),
        ),
        migrations.RunPython(adjust_seeded_fees, migrations.RunPython.noop),
    ]
