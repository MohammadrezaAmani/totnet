"""Approve join requests to a brand's configured mandatory channel."""

import logging

from apps.accounts.models import User, UserProfile
from apps.brands.models import BrandConfiguration

logger = logging.getLogger(__name__)


def is_required_channel(chat, configured_channel_id) -> bool:
    if configured_channel_id is None:
        return False
    configured = str(configured_channel_id).strip()
    if not configured:
        return False
    if configured.startswith("@"):
        return configured[1:].casefold() == (getattr(chat, "username", None) or "").casefold()
    return configured == str(chat.id)


async def approve_required_channel_join(join_request, *, brand_id: int) -> bool:
    """Approve only registered brand users requesting its mandatory channel."""
    fields = await BrandConfiguration.objects.filter(brand_id=brand_id).values_list(
        "custom_fields", flat=True
    ).afirst()
    if not isinstance(fields, dict) or not is_required_channel(
        join_request.chat, fields.get("required_channel_id")
    ):
        return False

    user_id = await User.objects.filter(
        brand_id=brand_id,
        telegram_id=join_request.from_user.id,
        is_active=True,
    ).values_list("pk", flat=True).afirst()
    if user_id is None:
        return False

    try:
        approved = await join_request.approve()
    except Exception:
        logger.exception("Mandatory channel join approval failed for brand=%s", brand_id)
        return False

    if not approved:
        return False

    await UserProfile.objects.filter(
        user_id=user_id, channel_join_required=True
    ).aupdate(channel_join_verified=True)
    return True
