from django.db import migrations, models
from django.core.validators import MinValueValidator, MaxValueValidator


class Migration(migrations.Migration):
    dependencies = [("brands", "0008_brandconfiguration_operations_channel_id")]
    operations = [migrations.AlterField(
        model_name="brand", name="gift_expiry_hours",
        field=models.PositiveIntegerField(
            "مهلت استفاده از هدیه (ساعت)", default=72,
            validators=[MinValueValidator(1), MaxValueValidator(720)],
            help_text="پیش‌فرض ۷۲ ساعت پس از تأیید کامل پرداخت؛ حداکثر ۷۲۰ ساعت.",
        ),
    )]
