from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("brands", "0007_brandconfiguration_allow_manual_referrer")]
    operations = [migrations.AddField(
        model_name="brandconfiguration", name="operations_channel_id",
        field=models.CharField(max_length=100, blank=True,
            help_text="شناسه عددی کانال یا نام کاربری @channel؛ ربات باید در کانال ادمین باشد.",
            verbose_name="کانال رویدادهای روزانه تلگرام"),
    )]
