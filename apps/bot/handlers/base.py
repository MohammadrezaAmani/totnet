"""
Base handler class for the Multi-Tenant VPN Bot
"""

import logging

from aiogram import Bot, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.cache import cache

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.brands.models import Brand

logger = logging.getLogger(__name__)


class BaseHandler:
    """Base class for all bot handlers"""

    def __init__(self, bot: Bot, brand: Brand):
        self.bot = bot
        self.brand = brand

    async def get_or_create_user(
        self, telegram_user: types.User, use_cache: bool = True, cache_ttl: int = 300
    ) -> User:
        """Get or create user from Telegram user data"""
        CACHE_KEY: str = f"{self.brand.id}:{telegram_user}"
        created = False

        if use_cache:
            cached = cache.get(CACHE_KEY)
            if cached:
                return cached, created
        try:
            user = await User.objects.aget(
                telegram_id=telegram_user.id, brand=self.brand
            )
        except User.DoesNotExist:
            username = telegram_user.username or f"user_{telegram_user.id}"
            user = await User.objects.acreate(
                telegram_id=telegram_user.id,
                username=username,
                first_name=telegram_user.first_name or "",
                last_name=telegram_user.last_name or "",
                brand=self.brand,
                user_type=User.UserType.CUSTOMER,
            )
            created = True

            await BotState.objects.acreate(
                user=user, brand=self.brand, current_state=BotState.StateType.MAIN_MENU
            )
        if use_cache:
            cache.set(CACHE_KEY, user, timeout=cache_ttl)
        return user, created

    async def get_user_state(self, user: User) -> BotState:
        """Get user's current bot state"""
        try:
            state = await BotState.objects.aget(user=user, brand=self.brand)
        except BotState.DoesNotExist:
            state = await BotState.objects.acreate(
                user=user, brand=self.brand, current_state=BotState.StateType.MAIN_MENU
            )
        return state

    async def update_user_state(
        self, user: User, new_state: str, state_data: dict = None
    ):
        """Update user's bot state"""
        state = await self.get_user_state(user)
        state.current_state = new_state
        if state_data:
            state.state_data.update(state_data)
        else:
            state.state_data = {}
        await state.asave()

    def create_keyboard(self, buttons_data: list) -> InlineKeyboardMarkup:
        """Create inline keyboard from button data"""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])

        for row in buttons_data:
            button_row = []
            for button in row:
                if isinstance(button, dict):
                    btn = InlineKeyboardButton(
                        text=button["text"],
                        callback_data=button.get("callback_data"),
                        url=button.get("url"),
                        web_app=button.get("web_app"),
                    )
                    button_row.append(btn)
            keyboard.inline_keyboard.append(button_row)

        return keyboard

    async def get_main_menu_keyboard(self, user: User = None) -> InlineKeyboardMarkup:
        """Generate main menu keyboard with admin button for admins only"""
        buttons = [
            [
                {"text": "🛒 خرید اشتراک", "callback_data": "purchase_subscription"},
                {"text": "📊 پروفایل من", "callback_data": "my_profile"},
            ],
            [
                {"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"},
                {"text": "💰 کیف پول", "callback_data": "wallet"},
            ],
            [
                {"text": "👥 معرفی دوستان", "callback_data": "referral_system"},
                {"text": "🎁 جایزه‌ها", "callback_data": "rewards"},
            ],
            [
                {"text": "🔧 پشتیبانی", "callback_data": "support"},
                {"text": "📊 آمار", "callback_data": "statistics"},
                {"text": "📊 ادمین", "callback_data": "admin"},
            ],
            [
                {"text": "❓ راهنما", "callback_data": "help"},
            ],
        ]

        if user:
            is_admin = user.is_staff or user.is_superuser
            if not is_admin:
                try:
                    is_admin = await user.admin_brands.filter(
                        pk=self.brand.pk
                    ).aexists()
                except Exception as e:
                    logger.warning(f"Error checking admin brands: {e}")
                    is_admin = False

            if is_admin:
                buttons.append([{"text": "🔑 ادمین", "callback_data": "admin"}])

        return self.create_keyboard(buttons)

    def get_back_keyboard(
        self, callback_data: str = "main_menu"
    ) -> InlineKeyboardMarkup:
        """Create back button keyboard"""
        return self.create_keyboard(
            [[{"text": "🔙 بازگشت", "callback_data": callback_data}]]
        )

    async def send_message_with_keyboard(
        self,
        chat_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup = None,
        parse_mode: str = "HTML",
    ) -> types.Message:
        """Send message with optional keyboard"""
        return await self.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode=parse_mode
        )

    async def edit_message_with_keyboard(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup = None,
        parse_mode: str = "HTML",
    ):
        """Edit message with optional keyboard"""
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
        except Exception as e:
            error_msg = str(e).lower()

            if "not modified" in error_msg or "message is not modified" in error_msg:
                logger.debug(f"Message content unchanged, skipping edit: {message_id}")
            else:
                logger.warning(f"Could not edit message: {e}")

                await self.send_message_with_keyboard(
                    chat_id, text, keyboard, parse_mode
                )

    def format_price(self, amount: float, currency: str = "USD") -> str:
        """Format price with currency"""
        if currency == "USD":
            return f"${amount:,.2f}"
        elif currency == "IRR":
            return f"{amount:,.0f} تومان"
        else:
            return f"{amount:,.2f} {currency}"

    def format_duration(self, days: int) -> str:
        """Format duration in Persian"""
        if days == 1:
            return "یک روز"
        elif days == 7:
            return "یک هفته"
        elif days == 30:
            return "یک ماه"
        elif days == 90:
            return "سه ماه"
        elif days == 365:
            return "یک سال"
        else:
            return f"{days} روز"

    def format_traffic(self, gb: int) -> str:
        """Format traffic in Persian"""
        if gb < 1024:
            return f"{gb} گیگابایت"
        else:
            tb = gb / 1024
            return f"{tb:.1f} ترابایت"
