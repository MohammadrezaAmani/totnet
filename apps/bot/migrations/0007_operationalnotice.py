from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("bot", "0006_botstate_seen_sections"),
                    ("brands", "0008_brandconfiguration_operations_channel_id")]
    operations = [migrations.CreateModel(
        name="OperationalNotice",
        fields=[
            ("id", models.BigAutoField(primary_key=True, serialize=False)),
            ("dedupe_key", models.CharField(max_length=200, unique=True)),
            ("text", models.TextField()),
            ("telegram_message_id", models.BigIntegerField(null=True, blank=True)),
            ("delivery_claimed_at", models.DateTimeField(null=True, blank=True)),
            ("sent_at", models.DateTimeField(null=True, blank=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("brand", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                        related_name="operational_notices", to="brands.brand")),
        ],
        options={"db_table": "bot_operational_notices", "ordering": ("created_at", "pk"),
                 "indexes": [models.Index(fields=["sent_at", "created_at"], name="ops_notice_pending_idx")]},
    )]
