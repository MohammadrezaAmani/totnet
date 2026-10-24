from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("orders", "0013_card_quote_gift_voucher_and_brand_settings")]

    operations = [
        migrations.CreateModel(
            name="AdminReceiptMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chat_id", models.BigIntegerField()),
                ("message_id", models.BigIntegerField()),
                ("deleted_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                               related_name="admin_receipt_messages", to="orders.payment")),
            ],
            options={"db_table": "admin_receipt_messages"},
        ),
        migrations.AddConstraint(
            model_name="adminreceiptmessage",
            constraint=models.UniqueConstraint(fields=("payment", "chat_id", "message_id"),
                                               name="uniq_admin_receipt_message"),
        ),
        migrations.AddIndex(
            model_name="adminreceiptmessage",
            index=models.Index(fields=["payment", "deleted_at"], name="receipt_pending_idx"),
        ),
    ]
