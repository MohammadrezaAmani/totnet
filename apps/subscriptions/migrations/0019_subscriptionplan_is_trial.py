from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0018_hiddensubscription")]
    operations = [
        migrations.AddField(
            model_name="subscriptionplan", name="is_trial",
            field=models.BooleanField(
                default=False, verbose_name="پلن تست مخصوص کاربران جدید",
                help_text="فقط برای ۱ کاربر در سرویس نرمال یا رویال؛ پس از سابقه اشتراک مخفی می‌شود.",
            ),
        ),
    ]
