from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("referrals", "0013_notificationrule_alter_referrallink_unique_together_and_more")]
    operations = [
        migrations.CreateModel(
            name="ServiceRewardRate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("service_category", models.CharField(max_length=20, choices=[("normal", "Normal"), ("royal", "Royal"), ("iran_ip", "Iran IP")])),
                ("plan_type", models.CharField(max_length=20, default="all", choices=[("all", "All plan types"), ("unlimited", "Unlimited"), ("traffic_based", "Traffic"), ("time_based", "Time"), ("hybrid", "Hybrid")])),
                ("percent", models.DecimalField(decimal_places=2, max_digits=5)),
                ("is_active", models.BooleanField(default=True)),
                ("brand", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_reward_rates", to="brands.brand")),
            ],
            options={"db_table": "service_reward_rates"},
        ),
        migrations.AddConstraint(
            model_name="servicerewardrate",
            constraint=models.UniqueConstraint(fields=("brand", "service_category", "plan_type"), name="unique_brand_service_reward_rate"),
        ),
    ]
