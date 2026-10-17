"""Emergency contact export: filter correctness, tenant isolation, XLSX integrity."""

from datetime import timedelta
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.orders.models import Order
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.vpn_providers.models import VPNProvider


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class EmergencyExportTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="A", slug="export-a", contact_email="a@example.invalid", bot_token="export-a")
        self.other = Brand.objects.create(name="B", slug="export-b", contact_email="b@example.invalid", bot_token="export-b")
        self.admin = User.objects.create_superuser(username="export-admin", email="admin@example.invalid", password="testing-only")
        self.manager = User.objects.create_user(username="brand-manager", password="testing-only", is_staff=True, brand=self.brand)
        self.manager.admin_brands.add(self.brand)
        self.customer = User.objects.create_user(username="customer", phone_number="09120000001", telegram_id=121, brand=self.brand)
        self.without = User.objects.create_user(username="without", phone_number="09120000002", telegram_id=122, brand=self.brand)
        self.expired = User.objects.create_user(username="expired", phone_number="09120000003", telegram_id=123, brand=self.brand)
        self.other_customer = User.objects.create_user(username="other", phone_number="09990000000", telegram_id=124, brand=self.other)
        provider = VPNProvider.objects.create(name="Provider", provider_type="connectix", base_url="https://example.invalid", brand=self.brand)
        plan = SubscriptionPlan.objects.create(brand=self.brand, vpn_provider=provider, name="Royal", plan_type="unlimited", service_category="royal", price=100)
        for index, (customer, exp) in enumerate(((self.customer, timezone.now() + timedelta(days=30)), (self.expired, timezone.now() - timedelta(days=2))), 1):
            order = Order.objects.create(brand=self.brand, user=customer, plan=plan, original_price=100, final_price=100)
            Subscription.objects.create(brand=self.brand, user=customer, owner=customer,
                                        plan=plan, order=order, vpn_provider=provider,
                                        status=Subscription.SubscriptionStatus.ACTIVE,
                                        starts_at=timezone.now() - timedelta(days=2), expires_at=exp)

    def _rows(self, response):
        payload = b"".join(response.streaming_content)
        with ZipFile(BytesIO(payload)) as archive:
            self.assertIsNone(archive.testzip())
            ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertIsNotNone(root.find("x:autoFilter", ns))
            rows = []
            for row in root.findall("x:sheetData/x:row", ns):
                rows.append([node.text or "" for node in row.findall("x:c/x:is/x:t", ns)])
            return rows

    def test_export_all_filtered_uses_exact_active_subscription_and_preserves_phone(self):
        self.client.force_login(self.admin)
        url = reverse("admin:accounts_user_export_filtered_xlsx")
        response = self.client.get(url + "?has_active_subscription=yes&active_service=royal")
        self.assertEqual(response.status_code, 200)
        rows = self._rows(response)
        self.assertEqual(len(rows), 2)  # header + one active customer
        self.assertEqual(rows[1][0], "09120000001")
        self.assertEqual(rows[1][1], "121")
        self.assertEqual(rows[1][6], "1")
        self.assertEqual(rows[1][7], "Royal service")

    def test_negative_filter_does_not_include_expired_as_active(self):
        self.client.force_login(self.admin)
        url = reverse("admin:accounts_user_export_filtered_xlsx")
        rows = self._rows(self.client.get(url + "?has_active_subscription=no"))
        phones = [r[0] for r in rows[1:]]
        self.assertIn("09120000002", phones)
        self.assertIn("09120000003", phones)
        self.assertNotIn("09120000001", phones)

    def test_non_superuser_cannot_export_other_brand(self):
        from django.contrib.auth.models import Permission
        self.manager.user_permissions.add(Permission.objects.get(codename="view_user", content_type__app_label="accounts"))
        self.client.force_login(self.manager)
        url = reverse("admin:accounts_user_export_filtered_xlsx")
        rows = self._rows(self.client.get(url))
        self.assertFalse(any("09990000000" in row for row in rows))
        self.assertTrue(any("09120000001" in row for row in rows))
