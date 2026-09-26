"""Transactional referral attribution and reward operations."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import F

from apps.accounts.models import User

from .models import Referral, ReferralLink


@transaction.atomic
def attribute_referral(*, user_id: int, brand_id: int, code: str) -> bool:
    """Set a user's first valid referral attribution for a brand once."""
    try:
        user = User.objects.select_for_update().get(pk=user_id, brand_id=brand_id)
    except User.DoesNotExist:
        return False
    if user.referred_by_id:
        return False

    referral_link = (
        ReferralLink.objects.select_for_update()
        .select_related("user")
        .filter(code=code, brand_id=brand_id, is_active=True)
        .first()
    )
    if (
        not referral_link
        or referral_link.user_id == user.pk
        or referral_link.user.brand_id != brand_id
    ):
        return False

    if Referral.objects.filter(referee_id=user.pk, brand_id=brand_id).exists():
        return False

    try:
        with transaction.atomic():
            Referral.objects.create(
                referrer_id=referral_link.user_id,
                referee_id=user.pk,
                brand_id=brand_id,
                referral_link=referral_link,
                status=Referral.ReferralStatus.PENDING,
            )
    except IntegrityError:
        # A concurrent first attribution won the unique referee/brand slot.
        return False

    user.referred_by_id = referral_link.user_id
    user.save(update_fields=["referred_by", "updated_at"])
    User.objects.filter(pk=referral_link.user_id).update(
        referral_count=F("referral_count") + 1
    )
    ReferralLink.objects.filter(pk=referral_link.pk).update(
        conversion_count=F("conversion_count") + 1
    )
    return True
