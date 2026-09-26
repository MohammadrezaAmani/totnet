import uuid

from django.test import TestCase

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.referrals.models import Referral, ReferralLink
from apps.referrals.services import attribute_referral


class ReferralAttributionTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Referral Test",
            slug=f"referral-test-{uuid.uuid4().hex[:8]}",
            contact_email="referrals@example.invalid",
            bot_token=f"token-{uuid.uuid4().hex}",
        )
        self.referrer = User.objects.create_user(
            username=f"referrer-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.referee = User.objects.create_user(
            username=f"referee-{uuid.uuid4().hex[:8]}", brand=self.brand
        )
        self.link = ReferralLink.objects.create(
            user=self.referrer,
            brand=self.brand,
            code=f"code-{uuid.uuid4().hex[:8]}",
        )

    def test_first_attribution_is_immutable_and_updates_referrer_counters(self):
        self.assertTrue(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.assertFalse(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )

        self.referrer.refresh_from_db()
        self.referee.refresh_from_db()
        self.link.refresh_from_db()
        self.assertEqual(self.referee.referred_by_id, self.referrer.pk)
        self.assertEqual(self.referrer.referral_count, 1)
        self.assertEqual(self.referee.referral_count, 0)
        self.assertEqual(self.link.conversion_count, 1)
        self.assertEqual(self.link.click_count, 0)
        self.assertEqual(
            Referral.objects.filter(referee=self.referee, brand=self.brand).count(), 1
        )

    def test_self_referral_and_inactive_link_are_rejected(self):
        self.assertFalse(
            attribute_referral(
                user_id=self.referrer.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.link.is_active = False
        self.link.save(update_fields=["is_active"])
        self.assertFalse(
            attribute_referral(
                user_id=self.referee.pk, brand_id=self.brand.pk, code=self.link.code
            )
        )
        self.assertFalse(Referral.objects.exists())
