"""Read-only referral reward summaries for bot and admin-facing views."""

from decimal import Decimal

from apps.referrals.models import Referral, ReferralLink, ReferralProgram, RewardAccount


async def referral_level_progress(*, user_id: int, brand_id: int) -> dict:
    """Resolve tiers from every configured threshold instead of points alone."""
    account = await RewardAccount.objects.filter(
        user_id=user_id, brand_id=brand_id
    ).afirst()
    lifetime_points = account.lifetime_points if account else Decimal("0")
    referral_count = await Referral.objects.filter(
        referrer_id=user_id, brand_id=brand_id
    ).acount()
    conversions = await Referral.objects.filter(
        referrer_id=user_id,
        brand_id=brand_id,
        status=Referral.ReferralStatus.REWARDED,
    ).acount()
    link = await ReferralLink.objects.filter(
        user_id=user_id, brand_id=brand_id
    ).afirst()
    clicks = link.click_count if link else 0
    conversion_rate = Decimal(conversions * 100) / Decimal(max(clicks, 1))
    program = await ReferralProgram.objects.filter(
        brand_id=brand_id, is_active=True
    ).select_related(
        "reference_service__plan"
    ).afirst()
    levels = []
    if program and program.enable_level_rewards:
        async for level in program.levels.order_by("level"):
            levels.append(level)
    qualified = [
        level
        for level in levels
        if level.min_lifetime_points <= lifetime_points
        and level.min_referrals <= referral_count
        and level.min_conversion_rate <= conversion_rate
    ]
    current = max(qualified, key=lambda item: item.level) if qualified else None
    remaining = [level for level in levels if not current or level.level > current.level]
    next_level = min(remaining, key=lambda item: item.level) if remaining else None
    return {
        "program": program,
        "account": account,
        "levels": levels,
        "current_level": current,
        "next_level": next_level,
        "lifetime_points": lifetime_points,
        "referral_count": referral_count,
        "conversions": conversions,
        "clicks": clicks,
        "conversion_rate": conversion_rate,
    }


async def reward_summary(*, user_id: int, brand_id: int) -> dict:
    progress = await referral_level_progress(user_id=user_id, brand_id=brand_id)
    account = progress["account"]
    lifetime_points = progress["lifetime_points"]
    liquid_points = account.liquid_points if account else 0
    level = progress["current_level"]
    return {
        "lifetime_points": lifetime_points,
        "liquid_points": liquid_points,
        "level_number": level.level if level else 0,
        "level_title": f"{level.badge} {level.name}" if level else "—",
    }


async def pill_progress(*, user_id: int, brand_id: int) -> dict:
    from django.db.models import Sum
    from apps.referrals.models import ReferralReward
    result = await ReferralReward.objects.filter(
        user_id=user_id, brand_id=brand_id,
        reward_type__in=["normal_point", "profile_point"],
        status=ReferralReward.RewardStatus.PROCESSED, is_cashed_out=False,
    ).aaggregate(total=Sum("amount"))
    count = int(result["total"] or 0)
    return {"outstanding": count, "remaining": 3 - count % 3}
