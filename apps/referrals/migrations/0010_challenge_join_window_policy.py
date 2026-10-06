from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("referrals", "0009_referral_cash_and_challenges"),
    ]

    operations = [
        migrations.AddField(
            model_name="challengeprogram",
            name="require_referral_join_during_challenge",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "اگر فعال باشد، فقط کاربری برای تارگت چالش حساب می‌شود که پس از شروع "
                    "چالش با لینک معرفی وارد شده باشد و در همان بازه خرید موفق انجام دهد."
                ),
            ),
        ),
    ]
