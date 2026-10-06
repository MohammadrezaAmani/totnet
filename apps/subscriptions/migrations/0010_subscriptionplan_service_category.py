# Generated for the XMind service menu structure.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0009_alter_subscription_traffic_limit_gb_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="service_category",
            field=models.CharField(
                choices=[
                    ("normal", "Normal service"),
                    ("royal", "Royal service"),
                    ("iran_ip", "Iran IP service"),
                ],
                default="normal",
                max_length=20,
            ),
        ),
    ]
