from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0014_admin_receipt_message")]
    operations = [
        migrations.AddField(
            model_name="order", name="use_wallet_credit",
            field=models.BooleanField(default=True),
        ),
    ]
