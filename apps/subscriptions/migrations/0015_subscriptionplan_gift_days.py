from django.db import migrations, models
import re


def copy_existing_gift_days(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "SubscriptionPlan")
    digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    pattern = re.compile(r"\+\s*([0-9۰-۹٠-٩]{1,3})\s*d\b", re.I)
    for plan in Plan.objects.all().iterator(chunk_size=500):
        for source in (plan.upstream_plan_name, plan.name):
            match = pattern.search(source or "")
            if match:
                days = int(match.group(1).translate(digits))
                if 1 <= days <= 365:
                    Plan.objects.filter(pk=plan.pk).update(gift_days=days)
                break


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0014_plan_bot_settings")]
    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="gift_days",
            field=models.PositiveSmallIntegerField(
                "روز هدیه", default=0,
                help_text="روز هدیه (در سرویس‌دهنده به شکل +3d). فقط برای نمایش؛ ارائه واقعی توسط سرویس‌دهنده انجام می‌شود.",
            ),
        ),
        migrations.RunPython(copy_existing_gift_days, migrations.RunPython.noop),
    ]
