"""Single six-day or low-traffic subscription warning."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("referrals", "0017_default_eight_percent_service_rates")]

    operations = [
        migrations.AlterField(
            model_name="gamificationnotification",
            name="notification_type",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("survey", "Survey"),
                    ("referral_intro", "Referral intro"),
                    ("challenge_offer", "Challenge offer"),
                    ("challenge_reminder", "Challenge reminder"),
                    ("expiry_5d", "Subscription expiry 5d"),
                    ("subscription_low", "Subscription low time or traffic"),
                    ("referral_reward", "Referral reward"),
                    ("referral_purchase", "Confirmed referred purchase"),
                    ("profile_completion", "Profile completion reminder"),
                    ("custom", "Admin notification"),
                ],
            ),
        ),
    ]
