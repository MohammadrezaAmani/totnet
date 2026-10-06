from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0010_subscriptionplan_service_category"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionClaim",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(max_length=100)),
                ("status", models.CharField(choices=[("pending", "Pending review"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending", max_length=20)),
                ("admin_note", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brand", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subscription_claims", to="brands.brand")),
                ("matched_subscription", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ownership_claims", to="subscriptions.subscription")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_subscription_claims", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subscription_claims", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "subscription_claims",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["brand", "status"], name="subscriptio_brand_i_22de41_idx"),
                    models.Index(fields=["user", "status"], name="subscriptio_user_id_e33833_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("user", "brand", "username"), name="uniq_subscription_claim_username")
                ],
            },
        ),
    ]
