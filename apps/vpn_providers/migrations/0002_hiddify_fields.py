"""
Migration: Add Hiddify-specific fields and HiddifyAdmin model
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vpn_providers", "0001_initial"),
        ("accounts", "0002_user_admin_brands"),
    ]

    operations = [
        migrations.AddField(
            model_name="vpnprovider",
            name="proxy_path",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="vpnprovider",
            name="default_admin_uuid",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.CreateModel(
            name="HiddifyAdmin",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("uuid", models.CharField(max_length=100, unique=True)),
                ("name", models.CharField(max_length=200)),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("super_admin", "Super Admin"),
                            ("admin", "Admin"),
                            ("agent", "Agent"),
                        ],
                        default="admin",
                        max_length=20,
                    ),
                ),
                (
                    "lang",
                    models.CharField(
                        choices=[
                            ("en", "English"),
                            ("fa", "Persian"),
                            ("ru", "Russian"),
                            ("pt", "Portuguese"),
                            ("zh", "Chinese"),
                            ("my", "Malay"),
                        ],
                        default="fa",
                        max_length=5,
                    ),
                ),
                ("telegram_id", models.BigIntegerField(blank=True, null=True)),
                ("comment", models.TextField(blank=True, null=True)),
                ("can_add_admin", models.BooleanField(default=False)),
                ("max_users", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "max_active_users",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("is_synced", models.BooleanField(default=False)),
                ("last_sync", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "local_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="hiddify_admin_profiles",
                        to="accounts.user",
                    ),
                ),
                (
                    "parent_admin",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sub_admins",
                        to="vpn_providers.hiddifyadmin",
                    ),
                ),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hiddify_admins",
                        to="vpn_providers.vpnprovider",
                    ),
                ),
            ],
            options={
                "db_table": "hiddify_admins",
            },
        ),
        migrations.AddIndex(
            model_name="hiddifyadmin",
            index=models.Index(
                fields=["provider", "mode"], name="hiddify_admin_provider_mode_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="hiddifyadmin",
            index=models.Index(
                fields=["telegram_id"], name="hiddify_admin_telegram_idx"
            ),
        ),
    ]
