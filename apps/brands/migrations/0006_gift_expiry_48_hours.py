from django.db import migrations, models
import django.core.validators


def change_previous_default(apps, schema_editor):
    Brand = apps.get_model("brands", "Brand")
    Brand.objects.filter(gift_expiry_hours=72).update(gift_expiry_hours=48)


class Migration(migrations.Migration):
    dependencies = [("brands", "0005_payment_method_button_style")]
    operations = [
        migrations.AlterField(
            model_name="brand", name="gift_expiry_hours",
            field=models.PositiveIntegerField(
                default=48, verbose_name="مهلت استفاده از هدیه (ساعت)",
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(720)],
                help_text="پیش‌فرض ۴۸ ساعت پس از تأیید کامل پرداخت؛ حداکثر ۷۲۰ ساعت.",
            ),
        ),
        migrations.RunPython(change_previous_default, migrations.RunPython.noop),
    ]
