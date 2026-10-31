"""Brand-scoped admission for every Telegram entry point."""

from aiogram import BaseMiddleware, types
import re

from django.db.models import Q

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.referrals.models import ReferralLink


INVITE_REQUIRED_MESSAGE = (
    "ورود کاربران جدید به این ربات فقط با لینک دعوت امکان‌پذیر است. "
    "لطفاً از لینک دعوت معتبر استفاده کنید."
)


class InvitationRequired(Exception):
    pass


async def extract_start_invitation(message: types.Message, bot) -> str | None:
    """Read a Telegram /start deep-link without relying on filter middleware state.

    This is run by an OUTER middleware (before CommandStart is evaluated by
    the router). Keep bot mentions checked, so a link intended for another bot
    never grants entry to this brand.
    """
    match = re.fullmatch(
        r"/start(?:@(?P<mention>[A-Za-z0-9_]+))?(?:[ \t]+(?P<code>[A-Za-z0-9_-]{1,64}))?[ \t]*",
        message.text or "",
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    mention = match.group("mention")
    if mention:
        identity = await bot.get_me()
        if not identity.username or mention.casefold() != identity.username.casefold():
            return None
    return match.group("code")


async def registration_allowed(brand_id: int, referral_code: str | None = None) -> bool:
    # Read current settings, even if the running handler holds an older Brand instance.
    invite_only = await Brand.objects.filter(
        pk=brand_id, invite_only_registration=True
    ).aexists()
    if not invite_only:
        return True
    if not referral_code:
        return False
    if referral_code.startswith('gift_'):
        from django.utils import timezone
        from apps.orders.models import GiftVoucher, Order
        return await GiftVoucher.objects.filter(
            token=referral_code[5:], order__brand_id=brand_id,
            claimed_by__isnull=True, expires_at__gt=timezone.now(),
            order__status__in=[Order.OrderStatus.PAID, Order.OrderStatus.PROCESSING, Order.OrderStatus.COMPLETED],
        ).aexists()
    registered = await ReferralLink.objects.filter(
        brand_id=brand_id, is_active=True, code=referral_code,
    ).filter(Q(user__isnull=True) | Q(user__brand_id=brand_id)).aexists()
    if registered:
        return True
    # Legacy links sometimes contain User.referral_code even if no
    # ReferralLink was generated. Admit only if no link with this code exists
    # in any brand (in particular never bypass a disabled/foreign link).
    if await ReferralLink.objects.filter(code=referral_code).aexists():
        return False
    return await User.objects.filter(
        brand_id=brand_id, referral_code=referral_code, is_active=True,
    ).aexists()


class BrandAdmissionMiddleware(BaseMiddleware):
    def __init__(self, brand_id: int):
        self.brand_id = brand_id

    async def __call__(self, handler, event, data):
        telegram_user = event.from_user
        if telegram_user is None:
            return
        existing = await User.objects.filter(brand_id=self.brand_id, telegram_id=telegram_user.id).afirst()
        if existing:
            if not existing.is_active:
                if isinstance(event, types.CallbackQuery):
                    await event.answer("حساب شما غیرفعال است.", show_alert=True)
                elif isinstance(event, types.Message):
                    await event.answer("حساب شما غیرفعال است؛ با پشتیبانی تماس بگیرید.")
                elif isinstance(event, types.PreCheckoutQuery):
                    await event.answer(ok=False, error_message="حساب غیرفعال است.")
                elif isinstance(event, types.InlineQuery):
                    await event.answer([], cache_time=0, is_personal=True)
                return
            return await handler(event, data)

        code = None
        if isinstance(event, types.Message):
            code = await extract_start_invitation(event, data["bot"])
        if await registration_allowed(self.brand_id, code):
            return await handler(event, data)

        if isinstance(event, types.CallbackQuery):
            await event.answer(INVITE_REQUIRED_MESSAGE, show_alert=True)
        elif isinstance(event, types.InlineQuery):
            await event.answer([], cache_time=0, is_personal=True)
        elif isinstance(event, types.PreCheckoutQuery):
            await event.answer(ok=False, error_message=INVITE_REQUIRED_MESSAGE)
        elif isinstance(event, types.Message):
            await event.answer(INVITE_REQUIRED_MESSAGE)
