from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0009_alter_couponusage_order_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="payment",
            name="uniq_confirmed_payment_per_order",
        ),
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.UniqueConstraint(
                fields=("order",),
                condition=models.Q(("status", "confirmed"))
                & ~models.Q(("payment_method", "wallet")),
                name="uniq_confirmed_payment_per_order",
            ),
        ),
    ]
