"""Regression tests for the combined time/usage warning."""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import Order
from apps.referrals.models import GamificationNotification
from apps.subscriptions.models import ProviderRemoteSubscription, Subscription, SubscriptionPlan
from apps.subscriptions.reminders import (
    scan_subscription_warnings,
    warning_cycle,
    warning_key,
    warning_reason,
)
from apps.vpn_providers.models import VPNProvider


class SubscriptionLowWarningTests(TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex[:10]
        self.now = timezone.now()
        self.brand = Brand.objects.create(
            name=f"Reminder brand {suffix}", slug=f"reminder-{suffix}",
            contact_email="reminders@example.invalid", bot_token=f"token-{suffix}",
            status=Brand.BrandStatus.ACTIVE,
        )
        self.user = User.objects.create_user(
            username=f"reminder-{suffix}", brand=self.brand, telegram_id=998877665,
        )
        self.provider = VPNProvider.objects.create(
            name=f"Reminder provider {suffix}", brand=self.brand,
            provider_type=VPNProvider.ProviderType.HIDDIFY,
            status=VPNProvider.ProviderStatus.ACTIVE,
            base_url="https://provider.example.invalid",
        )
        self.plan = SubscriptionPlan.objects.create(
            brand=self.brand, vpn_provider=self.provider, name="Normal 20 GB",
            plan_type=SubscriptionPlan.PlanType.HYBRID, price=Decimal("10.00"),
            traffic_limit_gb=Decimal("20.00"),
        )
        self.order = Order.objects.create(
            brand=self.brand, user=self.user, plan=self.plan,
            original_price=Decimal("10.00"), final_price=Decimal("10.00"),
            currency="USD", status=Order.OrderStatus.PAID,
        )
        self.sub = Subscription.objects.create(
            brand=self.brand, user=self.user, owner=self.user,
            plan=self.plan, order=self.order, vpn_provider=self.provider,
            status=Subscription.SubscriptionStatus.ACTIVE,
            starts_at=self.now - timedelta(days=10),
            expires_at=self.now + timedelta(days=9),
            traffic_limit_gb=Decimal("20.00"),
            traffic_used_gb=Decimal("0"),
        )

    def test_six_day_trigger(self):
        self.sub.expires_at = self.now + timedelta(days=6)
        self.assertEqual(warning_reason(self.sub, now=self.now)[0], "time")
        self.assertIn("۶ روز", warning_reason(self.sub, now=self.now)[1])

    def test_ninety_percent_used_triggers_volume_message(self):
        self.sub.traffic_used_gb = Decimal("18.00")
        self.assertEqual(warning_reason(self.sub, now=self.now)[0], "traffic")

    def test_time_and_traffic_together_prefers_time(self):
        self.sub.expires_at = self.now + timedelta(days=6)
        self.sub.traffic_used_gb = Decimal("19.00")
        self.assertEqual(warning_reason(self.sub, now=self.now)[0], "time")

    def test_no_notice_before_either_threshold(self):
        self.sub.traffic_used_gb = Decimal("17.90")
        self.assertIsNone(warning_reason(self.sub, now=self.now))

    def test_traffic_first_time_later_still_only_one_message(self):
        self.sub.traffic_used_gb = Decimal("18.00")
        self.sub.save(update_fields=("traffic_used_gb",))
        with patch("apps.referrals.tasks._publish_notification"):
            self.assertEqual(scan_subscription_warnings(now=self.now), 1)
            original = GamificationNotification.objects.get(dedupe_key=warning_key(self.sub))
            self.assertEqual(original.metadata["reason"], "traffic")
            self.assertEqual(original.metadata["delivery_payload"]["buttons_data"][0][0]["style"], "success")
            self.assertEqual(scan_subscription_warnings(now=self.now + timedelta(days=3)), 0)
            original.refresh_from_db()
            self.assertEqual(original.metadata["reason"], "traffic")
            self.assertEqual(GamificationNotification.objects.filter(notification_type="subscription_low").count(), 1)

    def test_renewal_creates_new_cycle(self):
        self.sub.traffic_used_gb = Decimal("18.00")
        self.sub.save(update_fields=("traffic_used_gb",))
        with patch("apps.referrals.tasks._publish_notification"):
            self.assertEqual(scan_subscription_warnings(now=self.now), 1)
            previous_cycle = warning_cycle(self.sub)
            self.sub.expires_at = self.now + timedelta(days=40)
            self.sub.traffic_used_gb = Decimal("0")
            self.sub.save(update_fields=("expires_at", "traffic_used_gb"))
            self.assertEqual(scan_subscription_warnings(now=self.now), 0)
            self.assertNotEqual(previous_cycle, warning_cycle(self.sub))
            self.assertEqual(scan_subscription_warnings(now=self.now + timedelta(days=35)), 1)
            self.assertEqual(GamificationNotification.objects.filter(notification_type="subscription_low").count(), 2)

    def test_duplicate_suppression_with_same_expiry(self):
        self.sub.expires_at = self.now + timedelta(days=6)
        self.sub.save(update_fields=("expires_at",))
        with patch("apps.referrals.tasks._publish_notification"):
            self.assertEqual(scan_subscription_warnings(now=self.now), 1)
            self.assertEqual(scan_subscription_warnings(now=self.now), 0)
        self.assertEqual(GamificationNotification.objects.filter(notification_type="subscription_low").count(), 1)

    def test_unlimited_volume_only_warns_on_time(self):
        self.sub.traffic_limit_gb = None
        self.sub.traffic_used_gb = Decimal("9999")
        self.assertIsNone(warning_reason(self.sub, now=self.now))
        self.sub.expires_at = self.now + timedelta(days=6)
        self.assertEqual(warning_reason(self.sub, now=self.now)[0], "time")

    def test_connectix_requires_known_traffic_units(self):
        self.provider.provider_type = VPNProvider.ProviderType.CONNECTIX
        self.provider.save(update_fields=("provider_type",))
        remote = ProviderRemoteSubscription.objects.create(
            subscription=self.sub, provider=self.provider,
            remote_id="remote-record-1", remote_status="active",
            last_synced_at=self.now,
            metadata={"used_traffic_raw": "18", "expire_date": "2026-12-20"},
        )
        self.assertIsNone(warning_reason(self.sub, now=self.now))
        remote.metadata["used_traffic_raw"] = "18 GB"
        remote.save(update_fields=("metadata",))
        self.sub.refresh_from_db()
        self.assertEqual(warning_reason(self.sub, now=self.now)[0], "traffic")

    def test_previous_five_day_alert_blocks_repeat_for_same_term(self):
        self.sub.expires_at = self.now + timedelta(days=4)
        self.sub.save(update_fields=("expires_at",))
        GamificationNotification.objects.create(
            user=self.user, brand=self.brand,
            notification_type=GamificationNotification.NotificationType.EXPIRY_5D,
            dedupe_key=f"expiry-5d:{self.sub.pk}:{self.sub.expires_at.isoformat()}",
            sent_at=self.now,
            metadata={"subscription_id": self.sub.pk,
                      "expires_at": self.sub.expires_at.isoformat()},
        )
        self.assertEqual(scan_subscription_warnings(now=self.now), 0)

    def test_inactive_subscription_never_notified(self):
        self.sub.status = Subscription.SubscriptionStatus.EXPIRED
        self.sub.expires_at = self.now + timedelta(days=1)
        self.sub.traffic_used_gb = Decimal("20")
        self.sub.save(update_fields=("status", "expires_at", "traffic_used_gb"))
        self.assertEqual(scan_subscription_warnings(now=self.now), 0)
