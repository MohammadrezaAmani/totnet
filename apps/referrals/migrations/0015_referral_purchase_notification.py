from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("referrals", "0014_service_reward_rate")]

    operations = [
        migrations.AlterField(
            model_name="gamificationnotification",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("survey", "Survey"),
                    ("referral_intro", "Referral intro"),
                    ("challenge_offer", "Challenge offer"),
                    ("challenge_reminder", "Challenge reminder"),
                    ("expiry_5d", "Subscription expiry 5d"),
                    ("referral_reward", "Referral reward"),
                    ("referral_purchase", "Confirmed referred purchase"),
                    ("profile_completion", "Profile completion reminder"),
                    ("custom", "Admin notification"),
                ],
                max_length=32,
            ),
        ),
    ]
