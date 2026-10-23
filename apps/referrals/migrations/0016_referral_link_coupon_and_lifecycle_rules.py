"""Referral-linked first-purchase coupons and delayed campaign triggers."""

from django.db import migrations, models
import django.db.models.deletion


TRIGGER_CHOICES = [
    ("joined", "عضویت در ربات"),
    ("first_purchase", "اولین خرید کاملاً پرداخت‌شده"),
    ("last_purchase", "هر خرید کاملاً پرداخت‌شده"),
    ("first_active_subscription", "اولین اشتراک فعال"),
    ("referral_link_join", "عضویت با لینک معرفی مشخص"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("referrals", "0015_referral_purchase_notification"),
        ("orders", "0013_card_quote_gift_voucher_and_brand_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="referrallink",
            name="welcome_coupon",
            field=models.ForeignKey(
                to="orders.coupon", on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name="welcome_referral_links",
                help_text="کد تخفیف خودکار خرید اول، فقط برای عضو جدیدی که با این لینک وارد شده.",
            ),
        ),
        migrations.AddField(
            model_name="referrallink",
            name="welcome_coupon_valid_hours",
            field=models.PositiveSmallIntegerField(
                default=48,
                help_text="مهلت تخفیف از اولین ورود با این لینک (ساعت).",
            ),
        ),
        migrations.AddField(
            model_name="notificationrule",
            name="referral_link",
            field=models.ForeignKey(
                to="referrals.referrallink", on_delete=django.db.models.deletion.PROTECT,
                null=True, blank=True, related_name="notification_rules",
                help_text="برای نوتیف ورود با لینک مشخص؛ خالی یعنی همه لینک‌های معرفی این برند.",
            ),
        ),
        migrations.AlterField(
            model_name="notificationrule", name="trigger",
            field=models.CharField(
                max_length=32, choices=TRIGGER_CHOICES, default="joined",
            ),
        ),
        migrations.AlterField(
            model_name="challengeprogram", name="offer_trigger",
            field=models.CharField(
                max_length=32, choices=TRIGGER_CHOICES, default="first_purchase",
            ),
        ),
    ]
