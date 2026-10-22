from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_userprofile_devices_userprofile_living_location_and_more")]
    operations = [
        migrations.AddField(model_name="userprofile", name="channel_join_required", field=models.BooleanField(default=False, editable=False)),
        migrations.AddField(model_name="userprofile", name="channel_join_verified", field=models.BooleanField(default=False, editable=False)),
        migrations.AddField(model_name="userprofile", name="gender", field=models.CharField(blank=True, choices=[("female", "زن"), ("male", "مرد"), ("other", "سایر"), ("undisclosed", "ترجیح می‌دهم نگویم")], max_length=24)),
    ]
