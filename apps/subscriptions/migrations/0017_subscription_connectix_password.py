from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0016_subscription_display_name")]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="connectix_password",
            field=models.CharField(max_length=255, blank=True, default="", editable=False),
        ),
    ]
