from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0015_subscriptionplan_gift_days")]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="display_name",
            field=models.CharField(blank=True, help_text="فقط نام نمایشی ربات؛ شناسه اتصال در سرویس‌دهنده تغییر نمی‌کند.", max_length=40, verbose_name="نام دلخواه اشتراک"),
        ),
    ]
