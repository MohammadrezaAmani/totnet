from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [('brands', '0003_brand_show_free_plans')]
    operations = [
        migrations.AddField(
            model_name='brand', name='gift_expiry_hours',
            field=models.PositiveIntegerField(
                default=72, verbose_name='مهلت استفاده از هدیه (ساعت)',
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(720)],
                help_text='پیش‌فرض ۷۲ ساعت پس از تأیید کامل پرداخت؛ حداکثر ۷۲۰ ساعت.',
            ),
        ),
    ]
