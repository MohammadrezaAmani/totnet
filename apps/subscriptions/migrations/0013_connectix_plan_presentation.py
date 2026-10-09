from decimal import Decimal

from django.db import migrations


def update_connectix_plans(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "SubscriptionPlan")
    plans = Plan.objects.using(schema_editor.connection.alias)
    duration_codes = {"days": "d", "weeks": "w", "months": "m", "years": "y"}

    def number(value):
        return format(Decimal(str(value)).normalize(), "f")

    for plan in (
        plans.filter(vpn_provider__provider_type="connectix")
        .exclude(upstream_plan_id="")
        .order_by("pk")
    ):
        category = plan.service_category
        if plan.upstream_group_name.strip().casefold() in {"", "default"}:
            category = "royal"
        if plan.plan_type == "unlimited":
            traffic = "unlimited"
        elif plan.traffic_limit_gb is not None:
            traffic = f"{number(plan.traffic_limit_gb)}g"
        else:
            traffic = "time"
        duration = "open"
        if plan.duration_value and plan.duration_unit in duration_codes:
            duration = (
                f"{number(plan.duration_value)}{duration_codes[plan.duration_unit]}"
            )
        base_name = f"{number(plan.max_users)}×{traffic}_{duration}_{category}"
        name = base_name
        others = plans.filter(brand_id=plan.brand_id).exclude(pk=plan.pk)
        collision_number = 0
        while others.filter(name=name).exists():
            suffix = f"_p{plan.vpn_provider_id}_{plan.upstream_plan_id[:12]}"
            if collision_number:
                suffix += f"_{collision_number}"
            name = f"{base_name[: 100 - len(suffix)]}{suffix}"
            collision_number += 1
        plan.name = name
        plan.service_category = category
        plan.save(update_fields=["name", "service_category"])


class Migration(migrations.Migration):
    dependencies = [
        (
            "subscriptions",
            "0012_rename_subscriptio_brand_i_a55573_idx_subscriptio_brand_i_22de41_idx_and_more",
        ),
        ("vpn_providers", "0005_alter_vpnprovider_api_key"),
    ]

    operations = [
        migrations.RunPython(update_connectix_plans, migrations.RunPython.noop)
    ]
