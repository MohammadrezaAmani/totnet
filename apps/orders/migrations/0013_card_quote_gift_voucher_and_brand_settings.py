from django.db import migrations, models
import django.db.models.deletion
import django.core.validators

class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0012_order_gift_reward_challenge_and_more'),
        ('brands', '0004_brand_gift_expiry_hours'),
    ]
    operations = [
        migrations.AddField(
            model_name='paymentcard', name='unique_amount_enabled',
            field=models.BooleanField(default=False, verbose_name='افزودن شناسه مبلغ برای تطبیق پیامک',
                help_text='تنها برای کارت‌های دریافت‌کننده پیامک مجاز؛ تأیید نهایی نیازمند اعتبارسنجی مستقل تراکنش است.'),
        ),
        migrations.AddField(
            model_name='paymentcard', name='unique_amount_max_extra',
            field=models.PositiveSmallIntegerField(default=99, verbose_name='حداکثر اختلاف مبلغ (تومان)',
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(99)],
                help_text='عددی بین ۱ تا ۹۹ تومان؛ باید با واحد مبلغ سفارش سازگار باشد.'),
        ),
        migrations.CreateModel(name='CardTransferQuote', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('base_amount', models.DecimalField(max_digits=15, decimal_places=2)),
            ('expected_amount', models.DecimalField(max_digits=15, decimal_places=2)),
            ('unique_suffix', models.BooleanField(default=False)),
            ('reserved', models.BooleanField(default=False)),
            ('release_after', models.DateTimeField()),
            ('expires_at', models.DateTimeField()),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('card', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfer_quotes', to='orders.paymentcard')),
            ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='card_quote', to='orders.order')),
        ], options={'db_table': 'card_transfer_quotes'}),
        migrations.AddConstraint(model_name='cardtransferquote', constraint=models.UniqueConstraint(
            fields=('card', 'expected_amount'), condition=models.Q(reserved=True), name='uniq_card_quote_amount_forever')),
        migrations.CreateModel(name='GiftVoucher', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('token', models.CharField(editable=False, max_length=64, unique=True)),
            ('expires_at', models.DateTimeField()),
            ('claimed_at', models.DateTimeField(blank=True, null=True)),
            ('new_member_at_claim', models.BooleanField(default=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('claimed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='claimed_gift_vouchers', to='accounts.user')),
            ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='gift_voucher', to='orders.order')),
        ], options={'db_table': 'gift_vouchers'}),
        migrations.AddIndex(model_name='giftvoucher', index=models.Index(fields=['expires_at', 'claimed_at'], name='gift_voucher_expiry_idx')),
    ]
