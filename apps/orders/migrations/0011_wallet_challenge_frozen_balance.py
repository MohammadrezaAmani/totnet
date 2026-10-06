from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0010_allow_partial_wallet_payments"),
    ]

    operations = [
        migrations.AddField(
            model_name="wallet",
            name="challenge_frozen_balance",
            field=models.DecimalField(decimal_places=10, default=0, max_digits=25),
        ),
        migrations.AddConstraint(
            model_name="wallet",
            constraint=models.CheckConstraint(
                condition=models.Q(challenge_frozen_balance__gte=0),
                name="wallet_challenge_frozen_nonneg",
            ),
        ),
    ]
