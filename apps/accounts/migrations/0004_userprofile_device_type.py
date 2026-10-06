from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_alter_user_telegram_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="device_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("iphone", "iPhone"),
                    ("android_samsung", "Android - Samsung"),
                    ("android_other", "Android - Xiaomi / Other"),
                    ("windows", "Windows"),
                    ("macos", "Macintosh"),
                    ("linux", "Linux"),
                ],
                max_length=32,
                null=True,
            ),
        ),
    ]
