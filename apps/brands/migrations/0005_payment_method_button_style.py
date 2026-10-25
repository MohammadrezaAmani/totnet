from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("brands", "0004_brand_gift_expiry_hours")]
    operations = [
        migrations.AddField(
            model_name="brandpaymentmethod", name="button_style",
            field=models.CharField(
                max_length=10, default="primary",
                choices=[("primary", "آبی"), ("success", "سبز"), ("danger", "قرمز")],
            ),
        ),
    ]
