"""Brand-scoped admission for every Telegram entry point."""

from aiogram import BaseMiddleware, types
from aiogram.filters import CommandStart

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.referrals.models import ReferralLink


INVITE_REQUIRED_MESSAGE = (
    "ورود کاربران جدید به این ربات فقط با لینک دعوت امکان‌پذیر است. "
    "لطفاً از لینک دعوت معتبر استفاده کنید."
)


class InvitationRequired(Exception):
    pass


async def registration_allowed(brand_id: int, referral_code: str | None = None) -> bool:
    # Read current settings, even if the running handler holds an older Brand instance.
    invite_only = await Brand.objects.filter(
        pk=brand_id, invite_only_registration=True
    ).aexists()
    if not invite_only:
        return True
    if not referral_code:
        return False
    return await ReferralLink.objects.filter(
        brand_id=brand_id,
        user__brand_id=brand_id,
        is_active=True,
        code=referral_code,
    ).aexists()


class BrandAdmissionMiddleware(BaseMiddleware):
    def __init__(self, brand_id: int):
        self.brand_id = brand_id

    async def __call__(self, handler, event, data):
        telegram_user = event.from_user
        if telegram_user is None:
            return
        if await User.objects.filter(
            brand_id=self.brand_id, telegram_id=telegram_user.id
        ).aexists():
            return await handler(event, data)

        code = None
        if isinstance(event, types.Message):
            command = await CommandStart()(event, data["bot"])
            if command:
                code = (command["command"].args or "").strip() or None
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
