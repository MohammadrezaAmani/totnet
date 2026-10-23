from decimal import Decimal

from django.db import migrations


def seed_rates(apps, schema_editor):
    Brand = apps.get_model("brands", "Brand")
    ServiceRewardRate = apps.get_model("referrals", "ServiceRewardRate")
    for brand_id in Brand.objects.values_list("pk", flat=True).iterator():
        for category in ("normal", "royal"):
            ServiceRewardRate.objects.get_or_create(
                brand_id=brand_id,
                service_category=category,
                plan_type="all",
                defaults={"percent": Decimal("8"), "is_active": True},
            )


class Migration(migrations.Migration):
    dependencies = [("referrals", "0016_referral_link_coupon_and_lifecycle_rules")]
    operations = [migrations.RunPython(seed_rates, migrations.RunPython.noop)]
