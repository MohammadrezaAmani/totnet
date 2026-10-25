from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0017_subscription_connectix_password"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HiddenSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("subscription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hidden_for_users", to="subscriptions.subscription")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hidden_subscriptions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "hidden_subscriptions"},
        ),
        migrations.AddConstraint(
            model_name="hiddensubscription",
            constraint=models.UniqueConstraint(fields=("user", "subscription"), name="uniq_hidden_subscription_user"),
        ),
    ]
