from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bot", "0005_behaviorevent")]
    operations = [migrations.AddField(model_name="botstate", name="seen_sections", field=models.JSONField(blank=True, default=dict))]
