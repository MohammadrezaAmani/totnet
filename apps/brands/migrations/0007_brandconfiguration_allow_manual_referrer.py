from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("brands", "0006_gift_expiry_48_hours")]
    operations = [
        migrations.AddField(
            model_name="brandconfiguration", name="allow_manual_referrer",
            field=models.BooleanField(
                default=False, verbose_name="اجازه ثبت دستی معرف",
                help_text="فقط کاربران بدون معرف قبلی می‌توانند یک معرف معتبر ثبت کنند.",
            ),
        ),
    ]
