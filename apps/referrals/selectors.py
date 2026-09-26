"""Read-only referral reward summaries for bot and admin-facing views."""

from apps.referrals.models import ReferralProgram, RewardAccount


async def reward_summary(*, user_id: int, brand_id: int) -> dict:
    account = await RewardAccount.objects.filter(
        user_id=user_id, brand_id=brand_id
    ).afirst()
    lifetime_points = account.lifetime_points if account else 0
    liquid_points = account.liquid_points if account else 0
    program = await ReferralProgram.objects.filter(
        brand_id=brand_id, is_active=True
    ).afirst()
    level = None
    if program:
        level = await program.levels.filter(
            min_lifetime_points__lte=lifetime_points
        ).order_by("-min_lifetime_points", "-level").afirst()
    return {
        "lifetime_points": lifetime_points,
        "liquid_points": liquid_points,
        "level_number": level.level if level else 0,
        "level_title": f"{level.badge} {level.name}" if level else "—",
    }
