from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("brands", "0001_initial"),
        ("support", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="UsefulContent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(choices=[
                    ("downloads", "App files and download links"),
                    ("install", "Install and import subscription"),
                    ("app_usage", "Application usage guide"),
                    ("faq", "Frequently asked questions"),
                ], max_length=24)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("content", models.TextField(blank=True)),
                ("download_url", models.URLField(blank=True, max_length=1000)),
                ("file", models.FileField(blank=True, null=True, upload_to="useful_content/")),
                ("display_order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brand", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="useful_contents", to="brands.brand")),
            ],
            options={
                "db_table": "useful_content",
                "ordering": ["category", "display_order", "title"],
            },
        ),
        migrations.AddIndex(
            model_name="usefulcontent",
            index=models.Index(fields=["brand", "category", "is_active"], name="useful_content_brand_cat_idx"),
        ),
    ]
