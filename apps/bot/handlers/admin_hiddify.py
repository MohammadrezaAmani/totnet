import logging
from enum import Enum
from typing import Dict, List, Optional, Tuple

from aiogram import types
from asgiref.sync import sync_to_async
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.orders.models import Order
from apps.subscriptions.models import Subscription
from apps.support.models import SupportTicket
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.services.hiddify import HiddifyAdmin as HiddifyAdminData
from apps.vpn_providers.services.hiddify import (
    HiddifyAdminMode,
    HiddifyLanguage,
    HiddifyProvider,
    HiddifyUser,
)

from .base import BaseHandler

logger = logging.getLogger(__name__)

USERS_PER_PAGE = 5


class AdminRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MODERATOR = "moderator"
    SUPPORT = "support"


class HiddifyAdminHandler(BaseHandler):
    """Enhanced admin handler with Hiddify panel integration"""

    def __init__(self, bot, brand):
        super().__init__(bot, brand)

        self._search_cache: Dict[int, list] = {}

    def _usage_bar(
        self, used_gb: float, limit_gb: Optional[float], width: int = 10
    ) -> str:
        """Visual progress bar for usage"""
        if not limit_gb or limit_gb <= 0:
            return ""
        percent = min((used_gb / limit_gb) * 100, 100)
        filled = int((percent / 100) * width)
        empty = width - filled
        if percent >= 90:
            emoji = "🔴"
        elif percent >= 70:
            emoji = "🟠"
        elif percent >= 50:
            emoji = "🟡"
        else:
            emoji = "🟢"
        bar = "█" * filled + "░" * empty
        return f"[{bar}] {percent:.0f}%"

    def _mode_persian(self, mode: Optional[str]) -> str:
        """Translate mode enum to Persian"""
        modes = {
            "weekly": "هفتگی",
            "monthly": "ماهانه",
            "yearly": "سالانه",
            "no_reset": "بدون ریست",
            "daily": "روزانه",
            "monthly_on_first": "ماهانه (اول ماه)",
            "on_first_of_month": "ماهانه (اول ماه)",
        }
        return modes.get(mode, mode or "نامشخص")

    def _days_status(self, days: Optional[int]) -> str:
        """Return days with appropriate emoji"""
        if days is None:
            return "♾️ نامحدود"
        if days <= 0:
            return "منقضی"
        if days <= 3:
            return f"{days} روز"
        if days <= 7:
            return f"{days} روز"
        if days <= 30:
            return f"{days} روز"
        return f"{days} روز"

    def _get_page_range(self, current: int, total: int, max_show: int = 5) -> List[int]:
        """Calculate which page numbers to show in pagination bar"""
        if total <= max_show:
            return list(range(1, total + 1))
        half = max_show // 2
        start = max(1, current - half)
        end = min(total, start + max_show - 1)
        if end - start < max_show - 1:
            start = max(1, end - max_show + 1)
        return list(range(start, end + 1))

    def _build_user_list_page2(
        self, users: list, page: int = 1, title: Optional[str] = None
    ) -> Tuple[str, list]:
        """Build text + keyboard for a user‑list page (no I/O)"""
        total = len(users)
        total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
        page = max(1, min(page, total_pages))
        start = (page - 1) * USERS_PER_PAGE
        end = start + USERS_PER_PAGE
        page_users = users[start:end]

        if not users:
            text = "❌ هیچ کاربری یافت نشد."
            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "➕ افزودن کاربر",
                            "callback_data": "admin_add_panel_user",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_panel_users"}],
                ]
            )
            return text, keyboard

        header = title or "📋 لیست کاربران"
        text = f"{header}\n"
        text += f"صفحه {page} از {total_pages}  •  مجموع {total} نفر\n"
        text += "━" * 28 + "\n\n"

        for i, u in enumerate(page_users, start + 1):
            active = u.is_active and u.enable
            status = "🟢" if active else "🔴"
            suffix = "" if active else "  ⚠️ غیرفعال"

            text += f"<b>{i}.</b> {status} <code>{u.name}</code>{suffix}\n"

            used = u.current_usage_GB or 0
            limit = u.usage_limit_GB
            if limit:
                bar = self._usage_bar(used, limit)
                text += f"   💾 {used:.2f} / {limit:.1f} GB  {bar}\n"
            else:
                text += f"   💾 {used:.2f} GB  ♾️ نامحدود\n"

            text += f"   📅 {self._days_status(u.package_days)}"
            text += f"  |  🔄 {self._mode_persian(u.mode)}\n"

            if u.comment:
                text += f"   💬 {u.comment}\n"

            short = (u.uuid[:12] + "…") if u.uuid else "—"
            text += f"   🔑 <code>{short}</code>\n"
            text += "\n"

        buttons: list = []

        buttons.append(
            [
                {"text": "➕ افزودن", "callback_data": "admin_add_panel_user"},
                {"text": "🔍 جستجو", "callback_data": "admin_search_panel_user"},
            ]
        )

        nav: list = []
        if page > 1:
            nav.append({"text": "◀️", "callback_data": f"admin_users_page_{page - 1}"})

        for p in self._get_page_range(page, total_pages):
            label = f"【{p}】" if p == page else str(p)
            nav.append({"text": label, "callback_data": f"admin_users_page_{p}"})

        if page < total_pages:
            nav.append({"text": "▶️", "callback_data": f"admin_users_page_{page + 1}"})

        if nav:
            buttons.append(nav)

        buttons.append(
            [
                {"text": "🔄 بروزرسانی", "callback_data": "admin_list_panel_users"},
                {"text": "🔙 بازگشت", "callback_data": "admin_panel_users"},
            ]
        )

        return text, self.create_keyboard(buttons)

    def _build_user_list_page(
        self, users: list, page: int = 1, title: Optional[str] = None
    ) -> Tuple[str, list]:
        """Build text + keyboard for a user-list page (no I/O)"""
        total = len(users)
        total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
        page = max(1, min(page, total_pages))
        start = (page - 1) * USERS_PER_PAGE
        end = start + USERS_PER_PAGE
        page_users = users[start:end]

        if not users:
            text = "❌ هیچ کاربری یافت نشد."
            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "➕ افزودن کاربر",
                            "callback_data": "admin_add_panel_user",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_panel_users"}],
                ]
            )
            return text, keyboard

        header = title or "📋 لیست کاربران"
        text = f"{header}\n"
        text += f"صفحه {page} از {total_pages}  •  مجموع {total} نفر\n"
        text += "━" * 28 + "\n\n"

        for i, u in enumerate(page_users, start + 1):
            active = u.is_active and u.enable
            status = "🟢" if active else "🔴"
            suffix = "" if active else "  ⚠️ غیرفعال"

            text += f"<b>{i}.</b> {status} <code>{u.name}</code>{suffix}\n"

            used = u.current_usage_GB or 0
            limit = u.usage_limit_GB
            if limit:
                bar = self._usage_bar(used, limit)
                text += f"   💾 {used:.2f} / {limit:.1f} GB  {bar}\n"
            else:
                text += f"   💾 {used:.2f} GB  ♾️ نامحدود\n"

            text += f"   📅 {self._days_status(u.package_days)}"
            text += f"  |  🔄 {self._mode_persian(u.mode)}\n"

            if u.comment:
                text += f"   💬 {u.comment}\n"

            short = (u.uuid[:12] + "…") if u.uuid else "—"
            text += f"   🔑 <code>{short}</code>\n"
            text += "\n"

        buttons: list = []

        for u in page_users:
            active = u.is_active and u.enable
            name_cut = (
                (u.name[:18] + "…") if len(u.name or "") > 18 else (u.name or "?")
            )

            row = [
                {
                    "text": f"ویرایش: {name_cut}",
                    "callback_data": f"avu_{u.uuid}",
                }
            ]
            buttons.append(row)

        buttons.append([{"text": "━" * 20, "callback_data": "admin_noop"}])

        buttons.append(
            [
                {"text": "➕ افزودن", "callback_data": "admin_add_panel_user"},
                {"text": "🔍 جستجو", "callback_data": "admin_search_panel_user"},
            ]
        )

        nav: list = []
        if page > 1:
            nav.append({"text": "◀️", "callback_data": f"aup_{page - 1}"})

        for p in self._get_page_range(page, total_pages):
            label = f"【{p}】" if p == page else str(p)
            nav.append({"text": label, "callback_data": f"aup_{p}"})

        if page < total_pages:
            nav.append({"text": "▶️", "callback_data": f"aup_{page + 1}"})

        if nav:
            buttons.append(nav)

        buttons.append(
            [
                {"text": "🔄 بروزرسانی", "callback_data": "admin_list_panel_users"},
                {"text": "🔙 بازگشت", "callback_data": "admin_panel_users"},
            ]
        )

        return text, self.create_keyboard(buttons)

    async def check_admin_access(self, user: User) -> bool:
        if user.is_staff or user.is_superuser:
            return True
        try:
            return await user.admin_brands.filter(pk=self.brand.pk).aexists()
        except Exception as e:
            logger.error(f"Error checking admin access: {e}")
            return False

    async def check_admin_role(self, user: User) -> Optional[AdminRole]:
        if user.is_superuser:
            return AdminRole.SUPER_ADMIN
        if user.is_staff:
            return AdminRole.ADMIN
        try:
            if await user.admin_brands.filter(pk=self.brand.pk).aexists():
                return AdminRole.ADMIN
        except Exception as e:
            logger.error(f"Error checking admin role: {e}")
        return None

    async def get_hiddify_provider(self) -> Optional[HiddifyProvider]:
        try:
            provider = await VPNProvider.objects.filter(
                brand=self.brand,
                provider_type=VPNProvider.ProviderType.HIDDIFY,
                status=VPNProvider.ProviderStatus.ACTIVE,
            ).afirst()
            if not provider:
                return None
            return HiddifyProvider(
                base_url=provider.base_url,
                api_key=provider.api_key,
                proxy_path=provider.proxy_path,
                admin_uuid=provider.default_admin_uuid,
            )
        except Exception as e:
            logger.error(f"Error getting Hiddify provider: {e}")
            return None

    async def list_panel_users(self, callback: types.CallbackQuery):
        """Entry point – fetches all users and renders page 1"""

        self._search_cache.pop(callback.from_user.id, None)

        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            users = await provider.get_all_users()
            text, keyboard = self._build_user_list_page(users, page=1)
        except Exception as e:
            text = f"❌ خطا در دریافت لیست کاربران:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin_panel_users")
        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_user_list_page(self, callback: types.CallbackQuery, page: int):
        """Pagination callback – re‑uses cached search or fetches all"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            cached = self._search_cache.get(callback.from_user.id)
            users = cached if cached is not None else await provider.get_all_users()
            text, keyboard = self._build_user_list_page(users, page=page)
        except Exception as e:
            text = f"❌ خطا: <code>{e}</code>"
            keyboard = self.get_back_keyboard("admin_panel_users")
        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def view_panel_user_details(
        self, callback: types.CallbackQuery, user_uuid: str
    ):
        """Rich detail view for a single user"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            u = await provider.get_user(user_uuid)
            if not u:
                text = "❌ کاربر یافت نشد"
                keyboard = self.get_back_keyboard("admin_list_panel_users")
            else:
                active = u.is_active and u.enable
                dot = "🟢" if active else "🔴"
                label = "فعال" if active else "غیرفعال"

                used = u.current_usage_GB or 0
                limit = u.usage_limit_GB

                text = f"{dot}  <b>جزئیات کاربر</b>\n"
                text += "━━━━━━━━━━━━━━━━━━\n"
                text += f"📌 نام:  <code>{u.name}</code>\n"
                text += f"🔑 UUID: <code>{u.uuid}</code>\n"
                text += "━━━━━━━━━━━━━━━━━━\n"
                text += f"✅ وضعیت:  {label}\n"
                text += "━━━━━━━━━━━━━━━━━━\n"
                text += "📊 مصرف ترافیک:\n"

                if limit:
                    pct = min((used / limit) * 100, 100)
                    w = 18
                    filled = int((pct / 100) * w)
                    empty = w - filled
                    bar = "█" * filled + "░" * empty
                    text += f"   {used:.3f} / {limit:.1f} GB\n"
                    text += f"   [{bar}]  {pct:.1f}%\n"
                else:
                    text += f"   {used:.3f} GB  ♾️ نامحدود\n"

                text += "━━━━━━━━━━━━━━━━━━\n"
                text += f"📅 روزهای باقی‌مانده:  {self._days_status(u.package_days)}\n"
                text += f"🔄 حالت تمدید:  {self._mode_persian(u.mode)}\n"

                if u.comment:
                    text += "━━━━━━━━━━━━━━━━━━\n"
                    text += f"💬 توضیحات:  {u.comment}\n"

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✏️ ویرایش",
                                "callback_data": f"admin_edit_panel_user_{user_uuid}",
                            },
                            {
                                "text": "🔄 تغییر وضعیت",
                                "callback_data": f"admin_toggle_user_{user_uuid}",
                            },
                        ],
                        [
                            {
                                "text": "📤 ریست مصرف",
                                "callback_data": f"admin_reset_user_{user_uuid}",
                            },
                            {
                                "text": "🗑️ حذف",
                                "callback_data": f"admin_delete_panel_user_{user_uuid}",
                            },
                        ],
                        [
                            {
                                "text": "🔙 بازگشت به لیست",
                                "callback_data": "admin_list_panel_users",
                            }
                        ],
                    ]
                )
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin_list_panel_users")
        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_edit_panel_user(
        self, callback: types.CallbackQuery, user_uuid: str
    ):
        """Show edit menu with all current values"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            u = await provider.get_user(user_uuid)
            if not u:
                text = "❌ کاربر یافت نشد"
                keyboard = self.get_back_keyboard("admin_list_panel_users")
                await self.edit_message_with_keyboard(
                    callback.message.chat.id,
                    callback.message.message_id,
                    text,
                    keyboard,
                )
                await callback.answer()
                return

            active = u.is_active and u.enable
            status = "🟢 فعال" if active else "🔴 غیرفعال"
            limit_s = f"{u.usage_limit_GB:.1f} GB" if u.usage_limit_GB else "نامحدود"
            days_s = str(u.package_days) if u.package_days else "نامحدود"

            text = f"""✏️  <b>ویرایش کاربر</b>:  <code>{u.name}</code>

━━━━━━━━━━━━━━━━━━
📝 نام:  {u.name}
💾 حجم:  {limit_s}
📅 روزها:  {days_s}
🔄 حالت:  {self._mode_persian(u.mode)}
💬 توضیح:  {u.comment or "—"}
✅ وضعیت:  {status}
━━━━━━━━━━━━━━━━━━

چه موردی را تغییر می‌دهید؟"""

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "📝 تغییر نام",
                            "callback_data": f"admin_euf_{user_uuid}_name",
                        }
                    ],
                    [
                        {
                            "text": "💾 تغییر حجم",
                            "callback_data": f"admin_euf_{user_uuid}_usage",
                        }
                    ],
                    [
                        {
                            "text": "📅 تغییر روزها",
                            "callback_data": f"admin_euf_{user_uuid}_days",
                        }
                    ],
                    [
                        {
                            "text": "🔄 تغییر حالت تمدید",
                            "callback_data": f"admin_euf_{user_uuid}_mode",
                        }
                    ],
                    [
                        {
                            "text": "💬 تغییر توضیح",
                            "callback_data": f"admin_euf_{user_uuid}_comment",
                        }
                    ],
                    [
                        {
                            "text": "✅ فعال / غیرفعال",
                            "callback_data": f"admin_toggle_user_{user_uuid}",
                        }
                    ],
                    [
                        {
                            "text": "📤 ریست مصرف حجم",
                            "callback_data": f"admin_reset_user_{user_uuid}",
                        }
                    ],
                    [
                        {
                            "text": "🔙 بازگشت به جزئیات",
                            "callback_data": f"admin_view_panel_user_{user_uuid}",
                        }
                    ],
                ]
            )
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin_list_panel_users")
        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_edit_user_field(
        self, callback: types.CallbackQuery, user_uuid: str, field: str
    ):
        """Begin editing a single field — sets state or shows inline picker"""
        user, _ = await self.get_or_create_user(callback.from_user)

        if field == "mode":
            text = "🔄 حالت تمدید جدید را انتخاب کنید:"
            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "📅 هفتگی",
                            "callback_data": f"admin_eum_{user_uuid}_weekly",
                        },
                        {
                            "text": "📆 ماهانه",
                            "callback_data": f"admin_eum_{user_uuid}_monthly",
                        },
                    ],
                    [
                        {
                            "text": "🗓️ سالانه",
                            "callback_data": f"admin_eum_{user_uuid}_yearly",
                        },
                        {
                            "text": "♾️ بدون ریست",
                            "callback_data": f"admin_eum_{user_uuid}_no_reset",
                        },
                    ],
                    [
                        {
                            "text": "❌ انصراف",
                            "callback_data": f"admin_edit_panel_user_{user_uuid}",
                        }
                    ],
                ]
            )
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
            await callback.answer()
            return

        field_labels = {
            "name": "نام",
            "usage": "حجم ترافیک (گیگابایت)",
            "days": "تعداد روزها",
            "comment": "توضیحات",
        }
        hints = {
            "usage": "\nمقدار <code>0</code> = نامحدود",
            "days": "\nمقدار <code>0</code> = نامحدود",
        }

        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {
                "action": "edit_panel_user",
                "user_uuid": user_uuid,
                "field": field,
                "step": "input",
            },
        )

        label = field_labels.get(field, field)
        hint = hints.get(field, "")
        text = f"✏️ مقدار جدید برای «{label}» را وارد کنید:{hint}"

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "❌ انصراف",
                        "callback_data": f"admin_edit_panel_user_{user_uuid}",
                    }
                ]
            ]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def apply_user_mode_change(
        self, callback: types.CallbackQuery, user_uuid: str, mode: str
    ):
        """Apply mode change directly (no text input needed)"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            u = await provider.get_user(user_uuid)
            if not u:
                text = "❌ کاربر یافت نشد"
            else:
                u.mode = mode
                result = await provider.update_hiddify_user(user_uuid, u)
                text = (
                    f"✅ حالت تمدید به «{self._mode_persian(mode)}» تغییر کرد"
                    if result
                    else "❌ خطا در بروزرسانی"
                )
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
        finally:
            await provider.close()

        keyboard = self.get_back_keyboard(f"admin_edit_panel_user_{user_uuid}")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def _apply_user_field_edit(
        self, message: types.Message, user: User, user_uuid: str, field: str, value: str
    ):
        """Apply a text‑field edit (called from handle_admin_message)"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await message.reply("❌ پنل Hiddify تنظیم نشده")
            return
        try:
            u = await provider.get_user(user_uuid)
            if not u:
                text = "❌ کاربر یافت نشد"
            else:
                if field == "name":
                    if not value.strip():
                        await message.reply("❌ نام نمی‌تواند خالی باشد.")
                        return
                    u.name = value.strip()

                elif field == "usage":
                    try:
                        v = float(value)
                        u.usage_limit_GB = v if v > 0 else None
                    except ValueError:
                        await message.reply(
                            "❌ لطفاً یک عدد معتبر وارد کنید.\nمثال: <code>50</code> یا <code>0</code>"
                        )
                        return

                elif field == "days":
                    try:
                        v = int(value)
                        u.package_days = v if v > 0 else None
                    except ValueError:
                        await message.reply(
                            "❌ لطفاً یک عدد صحیح معتبر وارد کنید.\nمثال: <code>30</code> یا <code>0</code>"
                        )
                        return

                elif field == "comment":
                    u.comment = value.strip() if value.strip() else None

                else:
                    text = "❌ فیلد نامعتبر"
                    await message.reply(text)
                    return

                result = await provider.update_hiddify_user(user_uuid, u)
                field_names = {
                    "name": "نام",
                    "usage": "حجم ترافیک",
                    "days": "تعداد روزها",
                    "comment": "توضیحات",
                }
                fname = field_names.get(field, field)
                text = (
                    f"✅ {fname} با موفقیت بروزرسانی شد"
                    if result
                    else "❌ خطا در بروزرسانی"
                )
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
        finally:
            await provider.close()

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "✏️ ادامه ویرایش",
                        "callback_data": f"admin_edit_panel_user_{user_uuid}",
                    }
                ],
                [
                    {
                        "text": "🔙 بازگشت به لیست",
                        "callback_data": "admin_list_panel_users",
                    }
                ],
            ]
        )
        await self.send_message_with_keyboard(message.chat.id, text, keyboard)
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    async def toggle_panel_user_status(
        self, callback: types.CallbackQuery, user_uuid: str
    ):
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            u = await provider.get_user(user_uuid)
            if not u:
                text = "❌ کاربر یافت نشد"
                keyboard = self.get_back_keyboard("admin_list_panel_users")
            else:
                u.enable = not u.enable
                result = await provider.update_hiddify_user(user_uuid, u)
                if result:
                    s = "فعال 🟢" if u.enable else "غیرفعال 🔴"
                    text = f"✅ کاربر «{u.name}» حالا <b>{s}</b> است"
                    keyboard = self.create_keyboard(
                        [
                            [
                                {
                                    "text": "👤 مشاهده جزئیات",
                                    "callback_data": f"admin_view_panel_user_{user_uuid}",
                                }
                            ],
                            [
                                {
                                    "text": "🔙 بازگشت به لیست",
                                    "callback_data": "admin_list_panel_users",
                                }
                            ],
                        ]
                    )
                else:
                    text = "❌ خطا در تغییر وضعیت"
                    keyboard = self.get_back_keyboard("admin_list_panel_users")
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin_list_panel_users")
        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def delete_panel_user(self, callback: types.CallbackQuery, user_uuid: str):
        """Ask for confirmation before deleting"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            u = await provider.get_user(user_uuid)
            if not u:
                text = "❌ کاربر یافت نشد"
                keyboard = self.get_back_keyboard("admin_list_panel_users")
            else:
                used = u.current_usage_GB or 0
                text = f"""⚠️  <b>حذف کاربر</b>

نام:  <code>{u.name}</code>
مصرف:  {used:.3f} GB
UUID:  <code>{u.uuid}</code>

❗ این عمل <b>غیرقابل بازگشت</b> است!

آیا مطمئن هستید؟"""

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✅ بله، حذف شود",
                                "callback_data": f"admin_cdu_{user_uuid}",
                            },
                            {
                                "text": "❌ خیر، انصراف",
                                "callback_data": f"admin_view_panel_user_{user_uuid}",
                            },
                        ]
                    ]
                )
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin_list_panel_users")
        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def confirm_delete_user(self, callback: types.CallbackQuery, user_uuid: str):
        """Execute deletion after confirmation"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            result = await provider.delete_hiddify_user(user_uuid)
            text = "✅ کاربر با موفقیت حذف شد" if result else "❌ خطا در حذف کاربر"
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
        finally:
            await provider.close()

        keyboard = self.create_keyboard(
            [
                {
                    "text": "📋 لیست کاربران",
                    "callback_data": "admin_list_panel_users",
                },
                {"text": "🔙 بازگشت", "callback_data": "admin_panel_users"},
            ]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def reset_user_usage(self, callback: types.CallbackQuery, user_uuid: str):
        """Ask for confirmation before resetting usage"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            u = await provider.get_user(user_uuid)
            if not u:
                text = "❌ کاربر یافت نشد"
                keyboard = self.get_back_keyboard("admin_list_panel_users")
            else:
                used = u.current_usage_GB or 0
                text = f"""📤  <b>ریست مصرف کاربر</b>

نام:  <code>{u.name}</code>
مصرف فعلی:  <b>{used:.3f} GB</b>
حد مجاز:  {f"{u.usage_limit_GB:.1f} GB" if u.usage_limit_GB else "نامحدود"}

مصرف این کاربر صفر شود؟"""

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✅ بله، ریست شود",
                                "callback_data": f"admin_cru_{user_uuid}",
                            },
                            {
                                "text": "❌ انصراف",
                                "callback_data": f"admin_view_panel_user_{user_uuid}",
                            },
                        ]
                    ]
                )
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin_list_panel_users")
        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def confirm_reset_usage(self, callback: types.CallbackQuery, user_uuid: str):
        """Execute usage reset after confirmation"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            if hasattr(provider, "reset_user_usage"):
                result = await provider.reset_user_usage(user_uuid)
            else:
                u = await provider.get_user(user_uuid)
                if u:
                    u.current_usage_GB = 0
                    result = await provider.update_hiddify_user(user_uuid, u)
                else:
                    result = False

            text = (
                "✅ مصرف کاربر با موفقیت ریست شد"
                if result
                else "❌ خطا در ریست مصرف کاربر"
            )
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
        finally:
            await provider.close()

        keyboard = self.create_keyboard(
            [
                {
                    "text": "👤 مشاهده جزئیات",
                    "callback_data": f"admin_view_panel_user_{user_uuid}",
                },
                {
                    "text": "🔙 بازگشت به لیست",
                    "callback_data": "admin_list_panel_users",
                },
            ]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_search_panel_user(self, callback: types.CallbackQuery):
        """Ask admin for search query"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "search_panel_user"},
        )

        text = """🔍  <b>جستجوی کاربر پنل</b>

نام، UUID یا بخشی از آن‌ها را وارد کنید:

مثال: <code>ali</code>  یا  <code>0ce60acb</code>"""

        keyboard = self.get_back_keyboard("admin_panel_users")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def _perform_user_search(self, message: types.Message, query: str):
        """Execute search and send results as new message"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await message.reply("❌ پنل Hiddify تنظیم نشده")
            return
        try:
            all_users = await provider.get_all_users()
            q = query.lower().strip()
            results = [
                u
                for u in all_users
                if q in (u.name or "").lower() or q in (u.uuid or "").lower()
            ]

            self._search_cache[message.from_user.id] = results

            if not results:
                text = f"❌ هیچ کاربری با «<code>{query}</code>» یافت نشد"
                keyboard = self.create_keyboard(
                    [
                        {
                            "text": "🔍 جستجوی مجدد",
                            "callback_data": "admin_search_panel_user",
                        },
                        {"text": "🔙 بازگشت", "callback_data": "admin_panel_users"},
                    ]
                )
                await self.send_message_with_keyboard(message.chat.id, text, keyboard)
            else:
                text, keyboard = self._build_user_list_page(
                    results, page=1, title=f"🔍 نتایج جستجو: «{query}»"
                )
                await self.send_message_with_keyboard(message.chat.id, text, keyboard)
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin_panel_users")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)
        finally:
            await provider.close()

    async def show_panel_users_menu(self, callback: types.CallbackQuery):
        """Show panel users management menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        if not await self.check_admin_access(user):
            await callback.answer("❌ دسترسی ندارید")
            return

        self._search_cache.pop(callback.from_user.id, None)

        text = """👥  مدیریت کاربران پنل Hiddify

از این بخش می‌توانید کاربران VPN را مدیریت کنید."""

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "📋 لیست کاربران",
                        "callback_data": "admin_list_panel_users",
                    }
                ],
                [
                    {
                        "text": "➕ افزودن کاربر",
                        "callback_data": "admin_add_panel_user",
                    }
                ],
                [
                    {
                        "text": "🔍 جستجوی کاربر",
                        "callback_data": "admin_search_panel_user",
                    }
                ],
                [
                    {
                        "text": "📊 بروزرسانی مصرف",
                        "callback_data": "admin_update_usage",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def handle_admin_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle admin-related text messages"""
        action = state.state_data.get("action")
        step = state.state_data.get("step")

        if action == "add_panel_admin":
            await self.handle_add_panel_admin_step(message, user, state)

        elif action == "add_panel_user":
            await self.handle_add_panel_user_step(message, user, state)

        elif action == "edit_panel_user" and step == "input":
            field = state.state_data.get("field")
            user_uuid = state.state_data.get("user_uuid")
            if user_uuid and field:
                await self._apply_user_field_edit(
                    message, user, user_uuid, field, message.text.strip()
                )
            else:
                await message.reply("❌ داده‌های نامعتبر. لطفاً دوباره تلاش کنید.")
                await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        elif action == "search_panel_user":
            query = message.text.strip()
            if not query:
                await message.reply("❌ لطفاً عبارتی برای جستجو وارد کنید.")
                return
            await self._perform_user_search(message, query)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        elif state.state_data.get("admin_action") == "search_user":
            query = message.text.strip()
            await message.reply(f"🔍 جستجو برای: {query}")
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        else:
            await message.reply(
                "لطفاً از منوی زیر استفاده کنید:",
                reply_markup=await self._get_main_menu_kb(user),
            )

    async def _get_main_menu_kb(self, user):
        """Quick helper – get main menu keyboard"""
        from apps.bot.handlers.start import StartHandler

        temp = StartHandler(self.bot, self.brand)
        return await temp.get_main_menu_keyboard(user)

    async def start_add_panel_user(self, callback: types.CallbackQuery):
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "add_panel_user", "step": "name"},
        )
        text = """➕ افزودن کاربر جدید به پنل

لطفاً نام کاربر را وارد کنید:"""
        keyboard = self.create_keyboard(
            [{"text": "❌ انصراف", "callback_data": "admin_panel_users"}]
        )
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_add_panel_user_step(
        self, message: types.Message, user: User, state: BotState
    ):
        step = state.state_data.get("step")
        user_data = state.state_data.get("user_data", {})

        if step == "name":
            user_data["name"] = message.text.strip()
            state.state_data["user_data"] = user_data
            state.state_data["step"] = "usage_limit"
            await state.asave()
            text = (
                "حجم ترافیک به گیگابایت را وارد کنید (یا <code>0</code> برای نامحدود):"
            )
            keyboard = self.get_back_keyboard("admin_panel_users")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "usage_limit":
            try:
                limit = float(message.text.strip())
                user_data["usage_limit_GB"] = limit if limit > 0 else None
            except ValueError:
                await message.reply("❌ لطفاً یک عدد معتبر وارد کنید.")
                return
            state.state_data["user_data"] = user_data
            state.state_data["step"] = "package_days"
            await state.asave()
            text = "تعداد روزهای اشتراک را وارد کنید (یا <code>0</code> برای نامحدود):"
            keyboard = self.get_back_keyboard("admin_panel_users")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "package_days":
            try:
                days = int(message.text.strip())
                user_data["package_days"] = days if days > 0 else None
            except ValueError:
                await message.reply("❌ لطفاً یک عدد معتبر وارد کنید.")
                return

            provider = await self.get_hiddify_provider()
            if not provider:
                await message.reply("❌ پنل Hiddify تنظیم نشده")
                return
            try:
                new_user = HiddifyUser(
                    name=user_data["name"],
                    usage_limit_GB=user_data.get("usage_limit_GB"),
                    package_days=user_data.get("package_days"),
                    enable=True,
                    is_active=True,
                )
                created = await provider.create_hiddify_user(new_user)
                if created:
                    text = f"""✅ کاربر با موفقیت ایجاد شد!

👤 نام:  {created.name}
🔑 UUID:  <code>{created.uuid}</code>
📊 حجم:  {created.usage_limit_GB or "نامحدود"} GB
📅 روزها:  {created.package_days or "نامحدود"}"""
                else:
                    text = "❌ خطا در ایجاد کاربر. لطفاً دوباره تلاش کنید."
            except Exception as e:
                text = f"❌ خطا:\n<code>{e}</code>"
            finally:
                await provider.close()

            keyboard = self.create_keyboard(
                [
                    {
                        "text": "📋 لیست کاربران",
                        "callback_data": "admin_list_panel_users",
                    },
                    {"text": "🔙 بازگشت", "callback_data": "admin_panel_users"},
                ]
            )
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    async def update_user_usage(self, callback: types.CallbackQuery):
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return
        try:
            result = await provider.update_user_usage()
            text = (
                "✅ مصرف کاربران با موفقیت بروزرسانی شد."
                if result
                else "❌ خطا در بروزرسانی مصرف کاربران."
            )
        except Exception as e:
            text = f"❌ خطا:\n<code>{e}</code>"
        finally:
            await provider.close()
        keyboard = self.get_back_keyboard("admin_panel_users")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_admin_menu(self, callback: types.CallbackQuery):
        """Show admin main menu with Hiddify integration"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ شما دسترسی ادمین ندارید")
            return

        role = await self.check_admin_role(user)
        role_name = {
            AdminRole.SUPER_ADMIN: "🔴 سوپر ادمین",
            AdminRole.ADMIN: "🟠 ادمین",
            AdminRole.MODERATOR: "🟡 مدیریت\u200cکننده",
            AdminRole.SUPPORT: "🟢 پشتیبانی",
        }.get(role, "نامشخص")

        has_hiddify = await VPNProvider.objects.filter(
            brand=self.brand, provider_type=VPNProvider.ProviderType.HIDDIFY
        ).aexists()

        text = f"""
🛠️ پنل مدیریت

👤 نام کاربری: {user.username}
📊 نقش: {role_name}
🏢 برند: {self.brand.name}
{"🌐 پنل Hiddify: متصل" if has_hiddify else "⚠️ پنل VPN: تنظیم نشده"}

چه کاری می\u200cخواهید انجام دهید؟
        """

        buttons = []

        if role in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            buttons.extend(
                [
                    [{"text": "📊 داشبورد", "callback_data": "admin_dashboard"}],
                    [
                        {
                            "text": "👥 مدیریت کاربران پنل",
                            "callback_data": "admin_panel_users",
                        }
                    ],
                    [
                        {
                            "text": "👤 مدیریت ادمین\u200cهای پنل",
                            "callback_data": "admin_panel_admins",
                        }
                    ],
                    [{"text": "📊 آمار سرور", "callback_data": "admin_server_status"}],
                    [
                        {
                            "text": "🛒 مدیریت سفارش\u200cها",
                            "callback_data": "admin_orders",
                        }
                    ],
                    [
                        {
                            "text": "🎫 تیکت\u200cهای پشتیبانی",
                            "callback_data": "admin_tickets",
                        }
                    ],
                    [
                        {
                            "text": "📢 ارسال پیام جمعی",
                            "callback_data": "admin_broadcast",
                        }
                    ],
                    [{"text": "⚙️ تنظیمات", "callback_data": "admin_settings"}],
                ]
            )

        elif role == AdminRole.MODERATOR:
            buttons.extend(
                [
                    [{"text": "📊 آمار", "callback_data": "admin_dashboard"}],
                    [{"text": "👥 کاربران", "callback_data": "admin_panel_users"}],
                    [{"text": "🛒 سفارش\u200cها", "callback_data": "admin_orders"}],
                ]
            )

        elif role == AdminRole.SUPPORT:
            buttons.extend(
                [
                    [{"text": "🎫 تیکت\u200cها", "callback_data": "admin_tickets"}],
                    [{"text": "👥 جستجوی کاربر", "callback_data": "admin_search_user"}],
                ]
            )

        buttons.append([{"text": "🔙 بازگشت", "callback_data": "main_menu"}])
        keyboard = self.create_keyboard(buttons)

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit admin menu: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_dashboard(self, callback: types.CallbackQuery):
        """Show admin dashboard with server stats"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        total_users = await User.objects.filter(brand=self.brand).acount()
        active_subs = await Subscription.objects.filter(
            brand=self.brand, status="active"
        ).acount()
        pending_orders = await Order.objects.filter(
            brand=self.brand, status="pending"
        ).acount()
        open_tickets = await SupportTicket.objects.filter(
            brand=self.brand, status="open"
        ).acount()

        server_status = None
        provider = await self.get_hiddify_provider()
        if provider:
            try:
                server_status = await provider.get_server_status()
            except Exception as e:
                logger.error(f"Error getting server status: {e}")
            finally:
                await provider.close()

        text = f"""
📊 داشبورد مدیریت

🎯 آمار کاربران:
• کل کاربران: {total_users}
• اشتراک\u200cهای فعال: {active_subs}
• سفارش\u200cهای در انتظار: {pending_orders}
• تیکت\u200cهای باز: {open_tickets}
"""

        if server_status:
            stats = server_status.get("stats")
            text += f"""
🖥️ وضعیت سرور:
• کاربران آنلاین: {server_status.get("usage_history", {}).get("m5", {}).get("online", "نامشخص")}
• استفاده CPU: {stats.get("system", {}).get("cpu_percent", "نامشخص")} / 100
• استفاده RAM: {stats.get("system", {}).get("ram_used", "نامشخص")} / {stats.get("system", {}).get("ram_total", "نامشخص")}
• کاربران فعال: {server_status.get("usage_history", {}).get("total", {}).get("online", "نامشخص")}
"""

        text += "\nچه کاری می\u200cخواهید انجام دهید؟"

        keyboard = self.create_keyboard(
            [
                [{"text": "🔄 بروزرسانی", "callback_data": "admin_dashboard"}],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        try:
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
        except Exception as e:
            logger.warning(f"Could not edit dashboard: {e}")
            await self.send_message_with_keyboard(
                callback.message.chat.id, text, keyboard
            )

        await callback.answer()

    async def show_server_status(self, callback: types.CallbackQuery):
        """Show detailed server status from Hiddify (clean UI)"""

        user, _ = await self.get_or_create_user(callback.from_user)
        if not await self.check_admin_access(user):
            await callback.answer("❌ دسترسی ندارید")
            return

        provider = await self.get_hiddify_provider()
        if not provider:
            text = "❌ پنل Hiddify برای این برند تنظیم نشده است."
            keyboard = self.get_back_keyboard("admin")
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
            await callback.answer()
            return

        try:
            status = await provider.get_server_status()
            panel_info = await provider.get_panel_info()

            system = (status or {}).get("stats", {}).get("system", {}) if status else {}
            usage = (status or {}).get("usage_history", {}) if status else {}

            def mb(x):
                return round(float(x), 2) if x is not None else 0

            def gb(x):
                return round(float(x) / 1024, 2) if x else 0

            text = f"""
    🖥️ <b>Hiddify Server Status</b>

    ━━━━━━━━━━━━━━
    📌 Panel
    • Version: <code>{panel_info.get("version", "N/A") if panel_info else "N/A"}</code>

    ━━━━━━━━━━━━━━
    💻 System
    • CPU Cores: {system.get("num_cpus", "N/A")}
    • Load (1m / 5m / 15m): {system.get("load_avg_1min", 0):.2f} / {system.get("load_avg_5min", 0):.2f} / {system.get("load_avg_15min", 0):.2f}

    ━━━━━━━━━━━━━━
    🧠 Memory
    • Used: {mb(system.get("ram_used"))} GB
    • Total: {mb(system.get("ram_total"))} GB

    ━━━━━━━━━━━━━━
    💾 Disk
    • Used: {mb(system.get("disk_used"))} GB
    • Total: {mb(system.get("disk_total"))} GB

    ━━━━━━━━━━━━━━
    🌐 Network
    • Sent: {system.get("bytes_sent_cumulative", 0)} bytes
    • Received: {system.get("bytes_recv_cumulative", 0)} bytes
    • Connections: {system.get("total_connections", 0)}
    • Unique IPs: {system.get("total_unique_ips", 0)}

    ━━━━━━━━━━━━━━
    📊 Usage
    • Today: {usage.get("today", {}).get("usage", 0)}
    • 24h Online: {usage.get("h24", {}).get("online", 0)}
    • Total Users: {usage.get("total", {}).get("users", 0)}
    """

            keyboard = self.create_keyboard(
                [
                    [{"text": "🔄 Refresh", "callback_data": "admin_server_status"}],
                    [
                        {
                            "text": "⚙️ Panel Settings",
                            "callback_data": "admin_panel_settings",
                        }
                    ],
                    [{"text": "🔙 Back", "callback_data": "admin"}],
                ]
            )

        except Exception as e:
            text = f"❌ Error fetching server status:\n<code>{e}</code>"
            keyboard = self.get_back_keyboard("admin")

        finally:
            try:
                await provider.close()
            except Exception as _:
                pass

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_panel_admins_menu(self, callback: types.CallbackQuery):
        """Show Hiddify panel admins management"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        text = """
👤 مدیریت ادمین\u200cهای پنل Hiddify

از این بخش می\u200cتوانید ادمین\u200cهای پنل VPN را مدیریت کنید.
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "📋 لیست ادمین\u200cها",
                        "callback_data": "admin_list_panel_admins",
                    }
                ],
                [{"text": "➕ افزودن ادمین", "callback_data": "admin_add_panel_admin"}],
                [
                    {
                        "text": "🔍 جستجوی ادمین",
                        "callback_data": "admin_search_panel_admin",
                    }
                ],
                [
                    {
                        "text": "🔄 همگام\u200cسازی",
                        "callback_data": "admin_sync_panel_admins",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def list_panel_admins(self, callback: types.CallbackQuery):
        """List all panel admins from Hiddify"""
        user, _ = await self.get_or_create_user(callback.from_user)

        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            admins = await provider.get_all_admins()

            if not admins:
                text = "❌ هیچ ادمینی یافت نشد."
            else:
                text = f"📋 لیست ادمین\u200cها ({len(admins)} نفر):\n\n"

                for i, admin in enumerate(admins[:10], 1):
                    mode_emoji = {
                        HiddifyAdminMode.SUPER_ADMIN: "🔴",
                        HiddifyAdminMode.ADMIN: "🟠",
                        HiddifyAdminMode.AGENT: "🟡",
                    }.get(admin.mode, "⚪")

                    text += f"{i}. {mode_emoji} {admin.name}\n"
                    text += f"   نوع: {admin.get_mode_display()}\n"
                    if admin.telegram_id:
                        text += f"   تلگرام: {admin.telegram_id}\n"
                    text += "\n"

                if len(admins) > 10:
                    text += f"... و {len(admins) - 10} ادمین دیگر"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "➕ افزودن ادمین",
                            "callback_data": "admin_add_panel_admin",
                        }
                    ],
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_list_panel_admins",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_panel_admins"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا در دریافت لیست ادمین\u200cها: {e}"
            keyboard = self.get_back_keyboard("admin_panel_admins")

        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_add_panel_admin(self, callback: types.CallbackQuery):
        """Start process of adding a new panel admin"""
        user, _ = await self.get_or_create_user(callback.from_user)

        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "add_panel_admin", "step": "name"},
        )

        text = """
➕ افزودن ادمین جدید به پنل

لطفاً نام ادمین را وارد کنید:
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "❌ انصراف", "callback_data": "admin_panel_admins"}],
            ]
        )

        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_add_panel_admin_step(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle steps for adding a panel admin"""
        step = state.state_data.get("step")
        admin_data = state.state_data.get("admin_data", {})

        if step == "name":
            admin_data["name"] = message.text.strip()
            state.state_data["admin_data"] = admin_data
            state.state_data["step"] = "mode"
            await state.asave()

            text = """
نوع ادمین را انتخاب کنید:
            """
            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🔴 سوپر ادمین",
                            "callback_data": "admin_mode_super_admin",
                        }
                    ],
                    [{"text": "🟠 ادمین", "callback_data": "admin_mode_admin"}],
                    [{"text": "🟡 نماینده", "callback_data": "admin_mode_agent"}],
                    [{"text": "❌ انصراف", "callback_data": "admin_panel_admins"}],
                ]
            )
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "telegram_id":
            try:
                telegram_id = int(message.text.strip())
                admin_data["telegram_id"] = telegram_id
            except ValueError:
                await message.reply("❌ لطفاً یک عدد معتبر وارد کنید.")
                return

            state.state_data["admin_data"] = admin_data
            state.state_data["step"] = "can_add_admin"
            await state.asave()

            text = "آیا این ادمین می\u200cتواند ادمین دیگر اضافه کند؟"
            keyboard = self.create_keyboard(
                [
                    [{"text": "✅ بله", "callback_data": "admin_can_add_yes"}],
                    [{"text": "❌ خیر", "callback_data": "admin_can_add_no"}],
                ]
            )
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    async def handle_panel_admin_mode_selection(
        self, callback: types.CallbackQuery, mode: str
    ):
        """Handle admin mode selection"""
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)

        if not state.state_data:
            await callback.answer("❌ خطا در داده‌ها")
            return

        state.state_data.setdefault("admin_data", {})["mode"] = mode
        state.state_data["step"] = "telegram_id"
        await state.asave()

        text = """
شماره تلگرام ادمین را وارد کنید (یا 0 برای رد کردن):
        """
        keyboard = self.get_back_keyboard("admin_panel_admins")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_panel_admin_can_add(
        self, callback: types.CallbackQuery, can_add: bool
    ):
        """Handle can_add_admin selection and create admin"""
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)

        if not state.state_data or "admin_data" not in state.state_data:
            await callback.answer("❌ خطا در داده‌ها")
            return

        admin_data = state.state_data["admin_data"]
        admin_data["can_add_admin"] = can_add
        admin_data["lang"] = "fa"

        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            new_admin = HiddifyAdminData(
                name=admin_data["name"],
                mode=HiddifyAdminMode(admin_data["mode"]),
                lang=HiddifyLanguage.FA,
                can_add_admin=admin_data["can_add_admin"],
                telegram_id=(
                    admin_data.get("telegram_id")
                    if admin_data.get("telegram_id", 0) > 0
                    else None
                ),
            )

            created = await provider.create_admin(new_admin)

            if created:
                text = f"""
✅ ادمین با موفقیت ایجاد شد!

👤 نام: {created.name}
📋 نوع: {created.get_mode_display()}
🔑 UUID: {created.uuid}
                """
            else:
                text = "❌ خطا در ایجاد ادمین. لطفاً دوباره تلاش کنید."

        except Exception as e:
            text = f"❌ خطا: {e}"

        finally:
            await provider.close()

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "📋 لیست ادمین\u200cها",
                        "callback_data": "admin_list_panel_admins",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin_panel_admins"}],
            ]
        )

        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    def _format_bytes(self, bytes_val: int) -> str:
        """Format bytes to human readable format"""
        if bytes_val < 1024:
            return f"{bytes_val} B"
        elif bytes_val < 1024**2:
            return f"{bytes_val / 1024:.2f} KB"
        elif bytes_val < 1024**3:
            return f"{bytes_val / (1024**2):.2f} MB"
        elif bytes_val < 1024**4:
            return f"{bytes_val / (1024**3):.2f} GB"
        else:
            return f"{bytes_val / (1024**4):.2f} TB"

    async def show_users_management(self, callback: types.CallbackQuery):
        """Show user management menu"""
        await self.show_panel_users_menu(callback)

    async def show_orders_management(self, callback: types.CallbackQuery):
        """Show orders management menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        try:
            pending_count = await Order.objects.filter(
                brand=self.brand, status="pending"
            ).acount()
            completed_count = await Order.objects.filter(
                brand=self.brand, status="completed"
            ).acount()
            failed_count = await Order.objects.filter(
                brand=self.brand, status="failed"
            ).acount()

        except Exception as e:
            logger.error(f"Error fetching orders: {e}")
            await callback.answer("❌ خطا در بارگذاری سفارش\u200cها")
            return

        text = f"""
🛒 مدیریت سفارش\u200cها

📊 وضعیت سفارش\u200cها:
• در انتظار: {pending_count}
• تکمیل شده: {completed_count}
• ناموفق: {failed_count}

چه کاری می\u200cخواهید انجام دهید؟
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "⏳ سفارش\u200cهای در انتظار",
                        "callback_data": "admin_pending_orders",
                    }
                ],
                [
                    {
                        "text": "✅ سفارش\u200cهای تکمیل شده",
                        "callback_data": "admin_completed_orders",
                    }
                ],
                [
                    {
                        "text": "❌ سفارش\u200cهای ناموفق",
                        "callback_data": "admin_failed_orders",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_tickets_management(self, callback: types.CallbackQuery):
        """Show support tickets management"""
        user, _ = await self.get_or_create_user(callback.from_user)
        has_access = await self.check_admin_access(user)

        if not has_access:
            await callback.answer("❌ دسترسی ندارید")
            return

        try:
            open_count = await SupportTicket.objects.filter(
                brand=self.brand, status="open"
            ).acount()
            in_progress_count = await SupportTicket.objects.filter(
                brand=self.brand, status="in_progress"
            ).acount()
            resolved_count = await SupportTicket.objects.filter(
                brand=self.brand, status="resolved"
            ).acount()

        except Exception as e:
            logger.error(f"Error fetching tickets: {e}")
            await callback.answer("❌ خطا در بارگذاری تیکت\u200cها")
            return

        text = f"""
🎫 مدیریت تیکت\u200cهای پشتیبانی

📊 وضعیت تیکت\u200cها:
• باز: {open_count}
• در حال انجام: {in_progress_count}
• حل شده: {resolved_count}
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🔴 تیکت\u200cهای باز",
                        "callback_data": "admin_open_tickets",
                    }
                ],
                [
                    {
                        "text": "🟡 تیکت\u200cهای در حال انجام",
                        "callback_data": "admin_in_progress_tickets",
                    }
                ],
                [
                    {
                        "text": "🟢 تیکت\u200cهای حل شده",
                        "callback_data": "admin_resolved_tickets",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_broadcast_menu(self, callback: types.CallbackQuery):
        """Show broadcast menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        role = await self.check_admin_role(user)

        if role not in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            await callback.answer("❌ این بخش فقط برای ادمین\u200cها است")
            return

        text = (
            "📢 ارسال پیام جمعی\n\nکدام گروه کاربران را می\u200cخواهید پیام ارسال کنید؟"
        )

        keyboard = self.create_keyboard(
            [
                [{"text": "👥 تمام کاربران", "callback_data": "admin_broadcast_all"}],
                [
                    {
                        "text": "🟢 کاربران فعال",
                        "callback_data": "admin_broadcast_active",
                    }
                ],
                [
                    {
                        "text": "🔴 کاربران غیرفعال",
                        "callback_data": "admin_broadcast_inactive",
                    }
                ],
                [
                    {
                        "text": "💰 کاربران با اشتراک",
                        "callback_data": "admin_broadcast_premium",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_settings(self, callback: types.CallbackQuery):
        """Show settings menu"""
        user, _ = await self.get_or_create_user(callback.from_user)
        role = await self.check_admin_role(user)

        if role not in [AdminRole.SUPER_ADMIN, AdminRole.ADMIN]:
            await callback.answer("❌ این بخش فقط برای ادمین\u200cها است")
            return

        text = f"""
⚙️ تنظیمات

🏢 برند: {self.brand.name}
🔑 شناسه: {self.brand.slug}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📝 تنظیمات برند", "callback_data": "admin_edit_brand_info"}],
                [
                    {
                        "text": "💰 تنظیمات پرداخت",
                        "callback_data": "admin_payment_settings",
                    }
                ],
                [
                    {
                        "text": "🌐 تنظیمات پنل VPN",
                        "callback_data": "admin_panel_settings",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def request_search_username(self, callback: types.CallbackQuery):
        """Request admin to enter username for search"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user, BotState.StateType.MAIN_MENU, {"admin_action": "search_user"}
        )

        text = "🔍 جستجوی کاربر\n\nلطفاً نام کاربری یا شماره تلگرام کاربر را وارد کنید:"

        keyboard = self.get_back_keyboard("admin")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def start_search_panel_admin(self, callback: types.CallbackQuery):
        """Start searching for a panel admin"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "search_panel_admin"},
        )

        text = """
🔍 جستجوی ادمین پنل

لطفاً نام یا UUID ادمین را وارد کنید:
        """

        keyboard = self.get_back_keyboard("admin_panel_admins")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def sync_panel_admins(self, callback: types.CallbackQuery):
        """Sync panel admins with database"""
        user, _ = await self.get_or_create_user(callback.from_user)

        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            admins = await provider.get_all_admins()
            text = f"""
✅ همگام‌سازی انجام شد

تعداد ادمین‌های پنل: {len(admins) if admins else 0}
            """
        except Exception as e:
            text = f"❌ خطا در همگام‌سازی: {e}"

        finally:
            await provider.close()

        keyboard = self.get_back_keyboard("admin_panel_admins")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def view_panel_admin_details(
        self, callback: types.CallbackQuery, admin_uuid: str
    ):
        """View details of a specific panel admin"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            admin = await provider.get_admin_by_uuid(admin_uuid)

            if not admin:
                text = "❌ ادمین یافت نشد"
                keyboard = self.get_back_keyboard("admin_list_panel_admins")
            else:
                text = f"""
👤 جزئیات ادمین

📌 نام: {admin.name}
🔑 UUID: {admin.uuid}
📋 نوع: {admin.get_mode_display()}
📱 تلگرام: {admin.telegram_id or "تعریف نشده"}
✅ فعال: {"بله" if admin.enable else "خیر"}
🔐 می‌تواند ادمین اضافه کند: {"بله" if admin.can_add_admin else "خیر"}
                """
                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✏️ ویرایش",
                                "callback_data": f"admin_edit_panel_admin_{admin_uuid}",
                            }
                        ],
                        [
                            {
                                "text": "🗑️ حذف",
                                "callback_data": f"admin_delete_panel_admin_{admin_uuid}",
                            }
                        ],
                        [
                            {
                                "text": "🔙 بازگشت",
                                "callback_data": "admin_list_panel_admins",
                            }
                        ],
                    ]
                )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_list_panel_admins")

        finally:
            await provider.close()

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_edit_panel_admin(
        self, callback: types.CallbackQuery, admin_uuid: str
    ):
        """Start editing a panel admin"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "edit_panel_admin", "admin_uuid": admin_uuid, "step": "mode"},
        )

        text = """
✏️ ویرایش ادمین پنل

نوع ادمین را انتخاب کنید:
        """
        keyboard = self.create_keyboard(
            [
                [{"text": "🔴 سوپر ادمین", "callback_data": "admin_mode_super_admin"}],
                [{"text": "🟠 ادمین", "callback_data": "admin_mode_admin"}],
                [{"text": "🟡 نماینده", "callback_data": "admin_mode_agent"}],
                [{"text": "❌ انصراف", "callback_data": "admin_panel_admins"}],
            ]
        )
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def delete_panel_admin(self, callback: types.CallbackQuery, admin_uuid: str):
        """Delete a panel admin"""
        provider = await self.get_hiddify_provider()
        if not provider:
            await callback.answer("❌ پنل Hiddify تنظیم نشده", show_alert=True)
            return

        try:
            result = await provider.delete_admin(admin_uuid)

            if result:
                text = "✅ ادمین با موفقیت حذف شد"
            else:
                text = "❌ خطا در حذف ادمین"

        except Exception as e:
            text = f"❌ خطا: {e}"

        finally:
            await provider.close()

        keyboard = self.get_back_keyboard("admin_list_panel_admins")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_pending_orders(self, callback: types.CallbackQuery):
        """Show pending orders"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            orders = await Order.objects.filter(
                brand=self.brand, status="pending"
            ).order_by("-created_at")[:10]

            if not orders:
                text = "❌ هیچ سفارش در انتظاری وجود ندارد"
            else:
                text = f"⏳ سفارش‌های در انتظار ({len(orders)}):\n\n"

                for i, order in enumerate(orders, 1):
                    text += f"{i}. سفارش #{order.id}\n"
                    text += f"   کاربر: {order.user.username}\n"
                    text += f"   مبلغ: {order.amount:,.0f} تومان\n"
                    text += f"   تاریخ: {order.created_at.strftime('%Y-%m-%d')}\n\n"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_pending_orders",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_orders"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_orders")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_completed_orders(self, callback: types.CallbackQuery):
        """Show completed orders"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            orders = await Order.objects.filter(
                brand=self.brand, status="completed"
            ).order_by("-created_at")[:10]

            if not orders:
                text = "❌ هیچ سفارش تکمیل شده‌ای وجود ندارد"
            else:
                text = f"✅ سفارش‌های تکمیل شده ({len(orders)}):\n\n"

                for i, order in enumerate(orders, 1):
                    text += f"{i}. سفارش #{order.id}\n"
                    text += f"   کاربر: {order.user.username}\n"
                    text += f"   مبلغ: {order.amount:,.0f} تومان\n"
                    text += f"   تاریخ: {order.created_at.strftime('%Y-%m-%d')}\n\n"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_completed_orders",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_orders"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_orders")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_failed_orders(self, callback: types.CallbackQuery):
        """Show failed orders"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            orders = await Order.objects.filter(
                brand=self.brand, status="failed"
            ).order_by("-created_at")[:10]

            if not orders:
                text = "❌ هیچ سفارش ناموفق‌ی وجود ندارد"
            else:
                text = f"❌ سفارش‌های ناموفق ({len(orders)}):\n\n"

                for i, order in enumerate(orders, 1):
                    text += f"{i}. سفارش #{order.id}\n"
                    text += f"   کاربر: {order.user.username}\n"
                    text += f"   مبلغ: {order.amount:,.0f} تومان\n"
                    text += f"   تاریخ: {order.created_at.strftime('%Y-%m-%d')}\n\n"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_failed_orders",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_orders"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_orders")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def view_order_details(self, callback: types.CallbackQuery, order_id: int):
        """View details of a specific order"""
        try:
            order = await Order.objects.filter(brand=self.brand, id=order_id).afirst()

            if not order:
                text = "❌ سفارش یافت نشد"
                keyboard = self.get_back_keyboard("admin_orders")
            else:
                status_emoji = {
                    "pending": "⏳",
                    "completed": "✅",
                    "failed": "❌",
                }.get(order.status, "❓")

                text = f"""
{status_emoji} جزئیات سفارش #{order.id}

👤 کاربر: {order.user.username}
📧 ایمیل: {order.user.email}
💰 مبلغ: {order.amount:,.0f} تومان
📊 وضعیت: {order.get_status_display()}
📅 تاریخ: {order.created_at.strftime("%Y-%m-%d %H:%M")}
🏷️ توضیحات: {order.description or "ندارد"}
                """

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✅ تایید سفارش",
                                "callback_data": f"admin_confirm_order_{order_id}",
                            }
                        ],
                        [
                            {
                                "text": "❌ لغو سفارش",
                                "callback_data": f"admin_cancel_order_{order_id}",
                            }
                        ],
                        [
                            {
                                "text": "🔙 بازگشت",
                                "callback_data": f"admin_{order.status}_orders",
                            }
                        ],
                    ]
                )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_orders")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def confirm_order(self, callback: types.CallbackQuery, order_id: int):
        """Confirm a pending order"""
        try:
            order = await Order.objects.filter(brand=self.brand, id=order_id).afirst()

            if not order:
                text = "❌ سفارش یافت نشد"
            else:
                order.status = "completed"
                await order.asave()
                text = "✅ سفارش با موفقیت تایید شد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard("admin_orders")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def cancel_order(self, callback: types.CallbackQuery, order_id: int):
        """Cancel an order"""
        try:
            order = await Order.objects.filter(brand=self.brand, id=order_id).afirst()

            if not order:
                text = "❌ سفارش یافت نشد"
            else:
                order.status = "failed"
                await order.asave()
                text = "✅ سفارش با موفقیت لغو شد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard("admin_orders")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_open_tickets(self, callback: types.CallbackQuery):
        """Show open support tickets"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            tickets = await SupportTicket.objects.filter(
                brand=self.brand, status="open"
            ).order_by("-created_at")[:10]

            if not tickets:
                text = "❌ هیچ تیکت باز وجود ندارد"
            else:
                text = f"🔴 تیکت‌های باز ({len(tickets)}):\n\n"

                for i, ticket in enumerate(tickets, 1):
                    text += f"{i}. تیکت #{ticket.id}\n"
                    text += f"   کاربر: {ticket.user.username}\n"
                    text += f"   موضوع: {ticket.subject[:30]}\n"
                    text += f"   تاریخ: {ticket.created_at.strftime('%Y-%m-%d')}\n\n"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_open_tickets",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_tickets"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_tickets")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_in_progress_tickets(self, callback: types.CallbackQuery):
        """Show in-progress support tickets"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            tickets = await SupportTicket.objects.filter(
                brand=self.brand, status="in_progress"
            ).order_by("-created_at")[:10]

            if not tickets:
                text = "❌ هیچ تیکت در حال انجام وجود ندارد"
            else:
                text = f"🟡 تیکت‌های در حال انجام ({len(tickets)}):\n\n"

                for i, ticket in enumerate(tickets, 1):
                    text += f"{i}. تیکت #{ticket.id}\n"
                    text += f"   کاربر: {ticket.user.username}\n"
                    text += f"   موضوع: {ticket.subject[:30]}\n"
                    text += f"   تاریخ: {ticket.created_at.strftime('%Y-%m-%d')}\n\n"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_in_progress_tickets",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_tickets"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_tickets")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_resolved_tickets(self, callback: types.CallbackQuery):
        """Show resolved support tickets"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            tickets = await SupportTicket.objects.filter(
                brand=self.brand, status="resolved"
            ).order_by("-created_at")[:10]

            if not tickets:
                text = "❌ هیچ تیکت حل شده‌ای وجود ندارد"
            else:
                text = f"🟢 تیکت‌های حل شده ({len(tickets)}):\n\n"

                for i, ticket in enumerate(tickets, 1):
                    text += f"{i}. تیکت #{ticket.id}\n"
                    text += f"   کاربر: {ticket.user.username}\n"
                    text += f"   موضوع: {ticket.subject[:30]}\n"
                    text += f"   تاریخ: {ticket.created_at.strftime('%Y-%m-%d')}\n\n"

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_resolved_tickets",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_tickets"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_tickets")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def view_ticket_details(self, callback: types.CallbackQuery, ticket_id: int):
        """View details of a specific ticket"""
        try:
            ticket = await SupportTicket.objects.filter(
                brand=self.brand, id=ticket_id
            ).afirst()

            if not ticket:
                text = "❌ تیکت یافت نشد"
                keyboard = self.get_back_keyboard("admin_tickets")
            else:
                status_emoji = {
                    "open": "🔴",
                    "in_progress": "🟡",
                    "resolved": "🟢",
                }.get(ticket.status, "❓")

                text = f"""
{status_emoji} جزئیات تیکت #{ticket.id}

👤 کاربر: {ticket.user.username}
📧 ایمیل: {ticket.user.email}
📋 موضوع: {ticket.subject}
📝 توضیحات: {ticket.description[:200]}...
📊 وضعیت: {ticket.get_status_display()}
📅 تاریخ: {ticket.created_at.strftime("%Y-%m-%d %H:%M")}
                """

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "👤 تخصیص به خود",
                                "callback_data": f"admin_assign_ticket_{ticket_id}",
                            }
                        ],
                        [
                            {
                                "text": "✅ بسته شود",
                                "callback_data": f"admin_close_ticket_{ticket_id}",
                            }
                        ],
                        [
                            {
                                "text": "🔄 دوباره باز کن",
                                "callback_data": f"admin_reopen_ticket_{ticket_id}",
                            }
                        ],
                        [
                            {
                                "text": "🔙 بازگشت",
                                "callback_data": "admin_tickets",
                            }
                        ],
                    ]
                )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_tickets")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def assign_ticket(self, callback: types.CallbackQuery, ticket_id: int):
        """Assign a ticket to the current admin"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            ticket = await SupportTicket.objects.filter(
                brand=self.brand, id=ticket_id
            ).afirst()

            if not ticket:
                text = "❌ تیکت یافت نشد"
            else:
                ticket.status = "in_progress"
                ticket.assigned_to = user
                await ticket.asave()
                text = "✅ تیکت به شما تخصیص یافت"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard("admin_tickets")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def close_ticket(self, callback: types.CallbackQuery, ticket_id: int):
        """Close a support ticket"""
        try:
            ticket = await SupportTicket.objects.filter(
                brand=self.brand, id=ticket_id
            ).afirst()

            if not ticket:
                text = "❌ تیکت یافت نشد"
            else:
                ticket.status = "resolved"
                await ticket.asave()
                text = "✅ تیکت بسته شد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard("admin_tickets")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def reopen_ticket(self, callback: types.CallbackQuery, ticket_id: int):
        """Reopen a support ticket"""
        try:
            ticket = await SupportTicket.objects.filter(
                brand=self.brand, id=ticket_id
            ).afirst()

            if not ticket:
                text = "❌ تیکت یافت نشد"
            else:
                ticket.status = "open"
                await ticket.asave()
                text = "✅ تیکت دوباره باز شد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard("admin_tickets")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_broadcast(self, callback: types.CallbackQuery, broadcast_type: str):
        """Start a broadcast message"""
        user, _ = await self.get_or_create_user(callback.from_user)

        type_names = {
            "all": "تمام کاربران",
            "active": "کاربران فعال",
            "inactive": "کاربران غیرفعال",
            "premium": "کاربران با اشتراک",
        }

        # Get count of target users
        try:
            if broadcast_type == "all":
                count = await User.objects.filter(brand=self.brand).acount()
            elif broadcast_type == "active":
                count = (
                    await User.objects.filter(
                        brand=self.brand, subscriptions__status="active"
                    )
                    .adistinct()
                    .acount()
                )
            elif broadcast_type == "inactive":
                active_ids = await User.objects.filter(
                    brand=self.brand, subscriptions__status="active"
                ).avalues_list("id", flat=True)
                count = (
                    await User.objects.filter(brand=self.brand)
                    .exclude(id__in=active_ids)
                    .acount()
                )
            elif broadcast_type == "premium":
                count = (
                    await User.objects.filter(
                        brand=self.brand, subscriptions__status="active"
                    )
                    .adistinct()
                    .acount()
                )
            else:
                count = 0
        except Exception as e:
            logger.error(f"Error counting broadcast users: {e}")
            count = 0

        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "broadcast", "type": broadcast_type, "step": "message"},
        )

        text = f"""
📢 ارسال پیام به {type_names.get(broadcast_type, "کاربران")}

👥 تعداد گیرندگان: {count} نفر

لطفاً متن پیام را وارد کنید:

💡 نکته: می‌توانید از فرمت Markdown استفاده کنید.
        """

        keyboard = self.get_back_keyboard("admin_broadcast")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def confirm_broadcast(self, callback: types.CallbackQuery):
        """Confirm and send broadcast message"""
        from aiogram.exceptions import TelegramAPIError

        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)

        broadcast_type = state.state_data.get("type")
        message_text = state.state_data.get("message")

        if not message_text:
            await callback.answer("❌ پیامی برای ارسال وجود ندارد")
            return

        # Show sending status
        text = "⏳ در حال ارسال پیام..."
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, None
        )

        try:
            # Get target users query
            if broadcast_type == "all":
                users_qs = User.objects.filter(
                    brand=self.brand, telegram_id__isnull=False
                )
            elif broadcast_type == "active":
                users_qs = User.objects.filter(
                    brand=self.brand,
                    telegram_id__isnull=False,
                    subscriptions__status="active",
                ).distinct()
            elif broadcast_type == "inactive":
                active_ids = User.objects.filter(
                    brand=self.brand, subscriptions__status="active"
                ).values_list("id", flat=True)
                users_qs = User.objects.filter(
                    brand=self.brand, telegram_id__isnull=False
                ).exclude(id__in=active_ids)
            elif broadcast_type == "premium":
                users_qs = User.objects.filter(
                    brand=self.brand,
                    telegram_id__isnull=False,
                    subscriptions__status="active",
                ).distinct()
            else:
                users_qs = User.objects.none()

            # Send messages
            success_count = 0
            fail_count = 0
            total_count = await users_qs.acount()

            async for target_user in users_qs:
                try:
                    await self.bot.send_message(
                        target_user.telegram_id, message_text, parse_mode="Markdown"
                    )
                    success_count += 1
                except TelegramAPIError as e:
                    logger.error(f"Failed to send to {target_user.id}: {e}")
                    fail_count += 1
                except Exception as e:
                    logger.error(f"Unexpected error for {target_user.id}: {e}")
                    fail_count += 1

            text = f"""
✅ ارسال پیام انجام شد

📊 آمار ارسال:
• موفق: {success_count} نفر
• ناموفق: {fail_count} نفر
• کل: {total_count} نفر

📝 متن پیام:
{message_text[:200]}{"..." if len(message_text) > 200 else ""}
            """

        except Exception as e:
            text = f"❌ خطا در ارسال پیام: {e}"

        keyboard = self.get_back_keyboard("admin_broadcast")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        await callback.answer()

    async def cancel_broadcast(self, callback: types.CallbackQuery):
        """Cancel broadcast"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

        text = "❌ ارسال پیام لغو شد"
        keyboard = self.get_back_keyboard("admin")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_brand_settings(self, callback: types.CallbackQuery):
        """Show brand settings"""
        text = f"""
📝 تنظیمات برند

🏢 نام: {self.brand.name}
🔑 شناسه: {self.brand.slug}
📝 توضیحات: {self.brand.description or "ندارد"}
✅ وضعیت: {"فعال" if self.brand.status == self.brand.BrandStatus.ACTIVE else "غیرفعال"}
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "✏️ ویرایش نام", "callback_data": "admin_edit_brand_name"}],
                [
                    {
                        "text": "✏️ ویرایش توضیحات",
                        "callback_data": "admin_edit_brand_description",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin_settings"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_payment_settings(self, callback: types.CallbackQuery):
        """Show payment settings"""
        text = """
💰 تنظیمات پرداخت

اینجا می‌توانید درگاه‌های پرداخت را مدیریت کنید.

درگاه‌های فعال:
• تحویل‌کار (درآمد)
• تراکنش (درآمد)
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🏦 درگاه‌های پرداخت",
                        "callback_data": "admin_payment_gateways",
                    }
                ],
                [
                    {
                        "text": "💵 تاریخچه تراکنش‌ها",
                        "callback_data": "admin_payment_history",
                    }
                ],
                [{"text": "📊 گزارش درآمد", "callback_data": "admin_revenue_report"}],
                [{"text": "🔙 بازگشت", "callback_data": "admin_settings"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_panel_settings(self, callback: types.CallbackQuery):
        """Show VPN panel settings"""
        provider = await self.get_hiddify_provider()

        if not provider:
            text = "❌ پنل Hiddify برای این برند تنظیم نشده است."
            keyboard = self.get_back_keyboard("admin_settings")
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, text, keyboard
            )
            await callback.answer()
            return

        try:
            panel_info = await provider.get_panel_info()

            text = f"""
🌐 تنظیمات پنل VPN

📌 پنل: Hiddify
🔗 آدرس: {provider.base_url}
📊 ورژن: {panel_info.get("version", "نامشخص") if panel_info else "نامشخص"}
✅ وضعیت: متصل
            """

        except Exception as e:
            text = f"⚠️ خطا در دریافت اطلاعات: {e}"

        finally:
            await provider.close()

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🔄 همگام‌سازی کاربران",
                        "callback_data": "admin_sync_users",
                    }
                ],
                [
                    {
                        "text": "📊 آمار پنل",
                        "callback_data": "admin_panel_stats",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin_settings"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_bot_settings(self, callback: types.CallbackQuery):
        """Show bot settings"""
        text = f"""
🤖 تنظیمات ربات

🎯 ربات برای برند: {self.brand.name}
🔑 توکن: {"●" * 10}...
✅ وضعیت: فعال
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "📝 پیام خوشامد",
                        "callback_data": "admin_edit_welcome_message",
                    }
                ],
                [{"text": "📋 منوی اصلی", "callback_data": "admin_edit_main_menu"}],
                [
                    {
                        "text": "📞 اطلاعات تماس",
                        "callback_data": "admin_edit_contact_info",
                    }
                ],
                [{"text": "🔙 بازگشت", "callback_data": "admin_settings"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    # Additional Payment Management Methods

    async def show_payment_gateways(self, callback: types.CallbackQuery):
        from apps.orders.models import CryptoCurrency, PaymentCard, PaymentGateway

        try:
            gateways = await sync_to_async(list)(
                PaymentGateway.objects.filter(brand=self.brand)
            )

            cards = await sync_to_async(list)(
                PaymentCard.objects.filter(brand=self.brand, is_active=True)
            )

            cryptos = await sync_to_async(list)(
                CryptoCurrency.objects.filter(brand=self.brand, is_active=True)
            )

            text = "🏦 درگاه‌های پرداخت\n\n"

            if gateways:
                text += "📡 درگاه‌های آنلاین:\n"
                for gw in gateways:
                    status = "✅" if gw.is_active else "❌"
                    sandbox = " (تستی)" if gw.is_sandbox else ""
                    text += f"  {status} {gw.name} - {gw.gateway_type}{sandbox}\n"
                text += "\n"

            if cards:
                text += f"💳 کارت‌های بانکی: {len(cards)} فعال\n\n"

            if cryptos:
                text += f"🪙 ارزهای دیجیتال: {len(cryptos)} فعال\n"

            if not gateways and not cards and not cryptos:
                text += "❌ هیچ درگاه پرداختی تنظیم نشده است."

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "➕ افزودن درگاه آنلاین",
                            "callback_data": "admin_add_gateway",
                        }
                    ],
                    [
                        {
                            "text": "💳 مدیریت کارت‌ها",
                            "callback_data": "admin_manage_cards",
                        }
                    ],
                    [
                        {
                            "text": "🪙 مدیریت کریپتو",
                            "callback_data": "admin_manage_crypto",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_payment_settings"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_payment_settings")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_payment_history(self, callback: types.CallbackQuery):
        """Show payment transaction history"""
        from apps.orders.models import Payment

        try:
            payments = await Payment.objects.filter(brand=self.brand).order_by(
                "-created_at"
            )[:20]

            if not payments:
                text = "❌ هیچ تراکنشی ثبت نشده است"
            else:
                text = f"💵 تاریخچه تراکنش‌ها ({len(payments)} آخر):\n\n"

                async for i, payment in enumerate(payments, 1):
                    status_emoji = {
                        "pending": "⏳",
                        "awaiting_confirmation": "🔍",
                        "confirmed": "✅",
                        "failed": "❌",
                        "cancelled": "🚫",
                        "refunded": "↩️",
                    }.get(payment.status, "❓")

                    text += (
                        f"{i}. {status_emoji} {payment.get_payment_method_display()}\n"
                    )
                    text += f"   مبلغ: {payment.amount:,.0f} {payment.currency}\n"
                    text += f"   کاربر: {payment.user.username}\n"
                    text += (
                        f"   تاریخ: {payment.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                    )

            keyboard = self.create_keyboard(
                [
                    [{"text": "🔍 جستجو", "callback_data": "admin_search_payment"}],
                    [
                        {
                            "text": "📊 فیلتر وضعیت",
                            "callback_data": "admin_filter_payments",
                        }
                    ],
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": "admin_payment_history",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_payment_settings"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_payment_settings")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_revenue_report(self, callback: types.CallbackQuery):
        """Show revenue report"""
        from datetime import datetime, timedelta

        from django.db.models import Sum

        from apps.orders.models import Payment

        try:
            today = datetime.now().date()
            week_ago = today - timedelta(days=7)
            month_ago = today - timedelta(days=30)

            # Today's revenue
            today_revenue = await Payment.objects.filter(
                brand=self.brand, status="confirmed", created_at__date=today
            ).aaggregate(total=Sum("amount"))

            # This week revenue
            week_revenue = await Payment.objects.filter(
                brand=self.brand, status="confirmed", created_at__date__gte=week_ago
            ).aaggregate(total=Sum("amount"))

            # This month revenue
            month_revenue = await Payment.objects.filter(
                brand=self.brand, status="confirmed", created_at__date__gte=month_ago
            ).aaggregate(total=Sum("amount"))

            # Total revenue
            total_revenue = await Payment.objects.filter(
                brand=self.brand, status="confirmed"
            ).aaggregate(total=Sum("amount"))

            # Count payments
            today_count = await Payment.objects.filter(
                brand=self.brand, status="confirmed", created_at__date=today
            ).acount()

            week_count = await Payment.objects.filter(
                brand=self.brand, status="confirmed", created_at__date__gte=week_ago
            ).acount()

            month_count = await Payment.objects.filter(
                brand=self.brand, status="confirmed", created_at__date__gte=month_ago
            ).acount()

            text = f"""
📊 گزارش درآمد

📅 امروز:
   مبلغ: {today_revenue.get("total") or 0:,.0f} تومان
   تعداد: {today_count} تراکنش

📆 این هفته:
   مبلغ: {week_revenue.get("total") or 0:,.0f} تومان
   تعداد: {week_count} تراکنش

📅 این ماه:
   مبلغ: {month_revenue.get("total") or 0:,.0f} تومان
   تعداد: {month_count} تراکنش

💰 کل درآمد:
   مبلغ: {total_revenue.get("total") or 0:,.0f} تومان
            """

            keyboard = self.create_keyboard(
                [
                    [
                        {
                            "text": "📈 نمودار درآمد",
                            "callback_data": "admin_revenue_chart",
                        }
                    ],
                    [
                        {
                            "text": "📤 خروجی Excel",
                            "callback_data": "admin_export_revenue",
                        }
                    ],
                    [{"text": "🔄 بروزرسانی", "callback_data": "admin_revenue_report"}],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_payment_settings"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_payment_settings")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    # Brand Settings Management Methods

    async def edit_brand_name(self, callback: types.CallbackQuery):
        """Start editing brand name"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "edit_brand_name"},
        )

        text = f"""
✏️ ویرایش نام برند

نام فعلی: {self.brand.name}

لطفاً نام جدید را وارد کنید:
        """

        keyboard = self.get_back_keyboard("admin_settings")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def edit_brand_description(self, callback: types.CallbackQuery):
        """Start editing brand description"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "edit_brand_description"},
        )

        text = f"""
✏️ ویرایش توضیحات برند

توضیحات فعلی: {self.brand.description or "ندارد"}

لطفاً توضیحات جدید را وارد کنید:
        """

        keyboard = self.get_back_keyboard("admin_settings")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    # Enhanced Ticket Management with Messages

    async def show_ticket_messages(self, callback: types.CallbackQuery, ticket_id: int):
        """Show messages for a specific ticket"""
        from apps.support.models import SupportMessage, SupportTicket

        try:
            ticket = await SupportTicket.objects.filter(
                brand=self.brand, id=ticket_id
            ).afirst()

            if not ticket:
                text = "❌ تیکت یافت نشد"
                keyboard = self.get_back_keyboard("admin_tickets")
            else:
                messages = await SupportMessage.objects.filter(ticket=ticket).aorder_by(
                    "created_at"
                )[:20]

                text = f"💬 پیام‌های تیکت #{ticket_id}\n\n"

                async for msg in messages:
                    sender = "👤 کاربر" if msg.is_from_customer else "👨‍💼 پشتیبان"
                    time_str = msg.created_at.strftime("%H:%M")
                    text += f"[{time_str}] {sender}:\n{msg.content[:100]}\n\n"

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✉️ پاسخ",
                                "callback_data": f"admin_reply_ticket_{ticket_id}",
                            }
                        ],
                        [
                            {
                                "text": "🔙 بازگشت",
                                "callback_data": f"admin_view_ticket_{ticket_id}",
                            }
                        ],
                    ]
                )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_tickets")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_ticket_reply(self, callback: types.CallbackQuery, ticket_id: int):
        """Start replying to a ticket"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "ticket_reply", "ticket_id": ticket_id},
        )

        text = """
✉️ پاسخ به تیکت

لطفاً متن پاسخ خود را وارد کنید:
        """

        keyboard = self.get_back_keyboard("admin_tickets")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def send_ticket_reply(self, message: types.Message, ticket_id: int):
        """Send reply to ticket"""
        from apps.support.models import SupportMessage, SupportTicket

        try:
            ticket = await SupportTicket.objects.filter(
                brand=self.brand, id=ticket_id
            ).afirst()

            if not ticket:
                await message.reply("❌ تیکت یافت نشد")
                return

            # Create support message
            await SupportMessage.objects.acreate(
                ticket=ticket,
                sender=message.from_user.id,
                content=message.text,
                is_from_customer=False,
            )

            # Update ticket status if needed
            if ticket.status == "open":
                ticket.status = "in_progress"
                await ticket.asave()

            # Notify customer via bot
            if ticket.customer.telegram_id:
                try:
                    notify_text = f"""
📬 پاسخ جدید به تیکت #{ticket.ticket_number}

{message.text[:500]}

برای مشاهده کامل پاسخ به بخش تیکت‌ها مراجعه کنید.
                    """
                    await self.bot.send_message(
                        ticket.customer.telegram_id, notify_text
                    )
                except Exception as e:
                    logger.error(f"Failed to notify customer: {e}")

            await message.reply("✅ پاسخ شما ارسال شد")

        except Exception as e:
            await message.reply(f"❌ خطا: {e}")

    # Additional callback handlers for new features

    async def handle_payment_gateways(self, callback: types.CallbackQuery):
        """Handle payment gateways callback"""
        await self.show_payment_gateways(callback)

    async def handle_payment_history(self, callback: types.CallbackQuery):
        """Handle payment history callback"""
        await self.show_payment_history(callback)

    async def handle_revenue_report(self, callback: types.CallbackQuery):
        """Handle revenue report callback"""
        await self.show_revenue_report(callback)

    async def handle_edit_brand_name(self, callback: types.CallbackQuery):
        """Handle edit brand name callback"""
        await self.edit_brand_name(callback)

    async def handle_edit_brand_description(self, callback: types.CallbackQuery):
        """Handle edit brand description callback"""
        await self.edit_brand_description(callback)

    async def handle_ticket_messages(
        self, callback: types.CallbackQuery, ticket_id: int
    ):
        """Handle show ticket messages callback"""
        await self.show_ticket_messages(callback, ticket_id)

    async def handle_reply_ticket(self, callback: types.CallbackQuery, ticket_id: int):
        """Handle reply to ticket callback"""
        await self.start_ticket_reply(callback, ticket_id)

    # Payment Gateway Management Methods

    async def add_payment_gateway(self, callback: types.CallbackQuery):
        """Start adding a new payment gateway"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "add_gateway", "step": "gateway_type"},
        )

        text = """
➕ افزودن درگاه پرداخت جدید

نوع درگاه را انتخاب کنید:
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "Stripe", "callback_data": "gw_type_stripe"}],
                [{"text": "PayPal", "callback_data": "gw_type_paypal"}],
                [{"text": "ZarinPal", "callback_data": "gw_type_zarinpal"}],
                [{"text": "IDPay", "callback_data": "gw_type_idpay"}],
                [{"text": "Razorpay", "callback_data": "gw_type_razorpay"}],
                [{"text": "Custom", "callback_data": "gw_type_custom"}],
                [{"text": "❌ انصراف", "callback_data": "admin_payment_gateways"}],
            ]
        )

        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_gateway_type_selection(
        self, callback: types.CallbackQuery, gw_type: str
    ):
        """Handle gateway type selection"""
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)

        state.state_data["gateway_type"] = gw_type
        state.state_data["step"] = "name"
        await state.asave()

        text = """
نام درگاه را وارد کنید (مثلاً: درگاه اصلی):
        """

        keyboard = self.get_back_keyboard("admin_payment_gateways")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def manage_cards(self, callback: types.CallbackQuery):
        """Manage payment cards"""
        from apps.orders.models import PaymentCard

        try:
            cards = await PaymentCard.objects.filter(brand=self.brand).aorder_by(
                "display_order"
            )

            if not cards:
                text = "❌ هیچ کارت بانکی ثبت نشده است"
            else:
                text = f"💳 کارت‌های بانکی ({len(cards)}):\n\n"
                async for i, card in enumerate(cards, 1):
                    status = "✅" if card.is_active else "❌"
                    masked = (
                        f"{card.card_number[:4]}****{card.card_number[-4:]}"
                        if len(card.card_number) >= 8
                        else card.card_number
                    )
                    text += f"{i}. {status} {card.bank_name}\n"
                    text += f"   شماره: {masked}\n"
                    text += f"   صاحب: {card.cardholder_name}\n\n"

            keyboard = self.create_keyboard(
                [
                    [{"text": "➕ افزودن کارت", "callback_data": "admin_add_card"}],
                    [
                        {
                            "text": "✏️ ویرایش کارت‌ها",
                            "callback_data": "admin_edit_cards",
                        }
                    ],
                    [{"text": "🗑️ حذف کارت", "callback_data": "admin_delete_card"}],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_payment_gateways"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_payment_gateways")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def manage_crypto(self, callback: types.CallbackQuery):
        """Manage cryptocurrency payments"""
        from apps.orders.models import CryptoCurrency

        try:
            cryptos = await CryptoCurrency.objects.filter(brand=self.brand).aorder_by(
                "display_order"
            )

            if not cryptos:
                text = "❌ هیچ ارز دیجیتالی تنظیم نشده است"
            else:
                text = f"🪙 ارزهای دیجیتال ({len(cryptos)}):\n\n"
                async for i, crypto in enumerate(cryptos, 1):
                    status = "✅" if crypto.is_active else "❌"
                    text += f"{i}. {status} {crypto.name} ({crypto.symbol})\n"
                    text += f"   شبکه: {crypto.get_network_display()}\n"
                    text += f"   آدرس: {crypto.wallet_address[:20]}...\n\n"

            keyboard = self.create_keyboard(
                [
                    [{"text": "➕ افزودن ارز", "callback_data": "admin_add_crypto"}],
                    [
                        {
                            "text": "✏️ ویرایش ارزها",
                            "callback_data": "admin_edit_cryptos",
                        }
                    ],
                    [{"text": "🗑️ حذف ارز", "callback_data": "admin_delete_crypto"}],
                    [
                        {
                            "text": "🔄 بروزرسانی نرخ",
                            "callback_data": "admin_update_crypto_rates",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_payment_gateways"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_payment_gateways")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    # Enhanced Payment History with Pagination

    async def show_payment_history_page(
        self, callback: types.CallbackQuery, page: int = 1
    ):
        """Show payment history with pagination"""
        from apps.orders.models import Payment

        per_page = 10
        offset = (page - 1) * per_page

        try:
            total_count = await Payment.objects.filter(brand=self.brand).acount()
            total_pages = (total_count + per_page - 1) // per_page

            payments = await Payment.objects.filter(brand=self.brand).aorder_by(
                "-created_at"
            )[offset : offset + per_page]

            if not payments:
                text = "❌ هیچ تراکنشی ثبت نشده است"
            else:
                text = f"💵 تاریخچه تراکنش‌ها (صفحه {page}/{total_pages}):\n\n"

                async for i, payment in enumerate(payments, 1):
                    status_emoji = {
                        "pending": "⏳",
                        "awaiting_confirmation": "🔍",
                        "confirmed": "✅",
                        "failed": "❌",
                        "cancelled": "🚫",
                        "refunded": "↩️",
                    }.get(payment.status, "❓")

                    text += f"{offset + i}. {status_emoji} {payment.get_payment_method_display()}\n"
                    text += f"   مبلغ: {payment.amount:,.0f} {payment.currency}\n"
                    text += f"   کاربر: {payment.user.username}\n"
                    text += (
                        f"   تاریخ: {payment.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                    )

            # Build pagination keyboard
            buttons = []

            # Navigation row
            nav_row = []
            if page > 1:
                nav_row.append(
                    {
                        "text": "◀️ قبلی",
                        "callback_data": f"admin_payment_history_{page - 1}",
                    }
                )
            if page < total_pages:
                nav_row.append(
                    {
                        "text": "▶️ بعدی",
                        "callback_data": f"admin_payment_history_{page + 1}",
                    }
                )
            if nav_row:
                buttons.append(nav_row)

            # Action buttons
            buttons.extend(
                [
                    [{"text": "🔍 جستجو", "callback_data": "admin_search_payment"}],
                    [
                        {
                            "text": "📊 فیلتر وضعیت",
                            "callback_data": "admin_filter_payments",
                        }
                    ],
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": f"admin_payment_history_{page}",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_payment_settings"}],
                ]
            )

            keyboard = self.create_keyboard(buttons)

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_payment_settings")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def search_payment(self, callback: types.CallbackQuery):
        """Start payment search"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "search_payment"},
        )

        text = """
🔍 جستجوی تراکنش

لطفاً شماره تراکنش، نام کاربری، یا مبلغ را وارد کنید:
        """

        keyboard = self.get_back_keyboard("admin_payment_history_1")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def filter_payments_by_status(self, callback: types.CallbackQuery):
        """Show payment filter options"""
        text = "📊 فیلتر تراکنش‌ها بر اساس وضعیت:"

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "⏳ در انتظار",
                        "callback_data": "admin_payments_status_pending",
                    }
                ],
                [
                    {
                        "text": "🔍 در انتظار تایید",
                        "callback_data": "admin_payments_status_awaiting_confirmation",
                    }
                ],
                [
                    {
                        "text": "✅ تایید شده",
                        "callback_data": "admin_payments_status_confirmed",
                    }
                ],
                [
                    {
                        "text": "❌ ناموفق",
                        "callback_data": "admin_payments_status_failed",
                    }
                ],
                [
                    {
                        "text": "🚫 لغو شده",
                        "callback_data": "admin_payments_status_cancelled",
                    }
                ],
                [
                    {
                        "text": "↩️ بازگشتی",
                        "callback_data": "admin_payments_status_refunded",
                    }
                ],
                [{"text": "📋 همه", "callback_data": "admin_payment_history_1"}],
                [{"text": "🔙 بازگشت", "callback_data": "admin_payment_history_1"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_payments_by_status(
        self, callback: types.CallbackQuery, status: str, page: int = 1
    ):
        """Show payments filtered by status"""
        from apps.orders.models import Payment

        per_page = 10
        offset = (page - 1) * per_page

        try:
            status_names = {
                "pending": "در انتظار",
                "awaiting_confirmation": "در انتظار تایید",
                "confirmed": "تایید شده",
                "failed": "ناموفق",
                "cancelled": "لغو شده",
                "refunded": "بازگشتی",
            }

            total_count = await Payment.objects.filter(
                brand=self.brand, status=status
            ).acount()
            total_pages = (total_count + per_page - 1) // per_page

            payments = await Payment.objects.filter(
                brand=self.brand, status=status
            ).aorder_by("-created_at")[offset : offset + per_page]

            if not payments:
                text = f"❌ هیچ تراکنش {status_names.get(status, '')}‌ای وجود ندارد"
            else:
                text = f"📊 تراکنش‌های {status_names.get(status, '')} (صفحه {page}/{total_pages}):\n\n"

                async for i, payment in enumerate(payments, 1):
                    text += f"{offset + i}. {payment.get_payment_method_display()}\n"
                    text += f"   مبلغ: {payment.amount:,.0f} {payment.currency}\n"
                    text += f"   کاربر: {payment.user.username}\n"
                    text += (
                        f"   تاریخ: {payment.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                    )

            # Build pagination keyboard
            buttons = []

            nav_row = []
            if page > 1:
                nav_row.append(
                    {
                        "text": "◀️ قبلی",
                        "callback_data": f"admin_payments_status_{status}_{page - 1}",
                    }
                )
            if page < total_pages:
                nav_row.append(
                    {
                        "text": "▶️ بعدی",
                        "callback_data": f"admin_payments_status_{status}_{page + 1}",
                    }
                )
            if nav_row:
                buttons.append(nav_row)

            buttons.extend(
                [
                    [
                        {
                            "text": "🔄 بروزرسانی",
                            "callback_data": f"admin_payments_status_{status}_{page}",
                        }
                    ],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_filter_payments"}],
                ]
            )

            keyboard = self.create_keyboard(buttons)

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_payment_history_1")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    # Revenue Chart and Export Methods

    async def show_revenue_chart(self, callback: types.CallbackQuery):
        """Show revenue chart"""
        from datetime import datetime, timedelta

        from django.db.models import Sum

        from apps.orders.models import Payment

        try:
            today = datetime.now().date()
            days = []
            revenues = []

            for i in range(7):
                date = today - timedelta(days=6 - i)
                revenue = await Payment.objects.filter(
                    brand=self.brand, status="confirmed", created_at__date=date
                ).aaggregate(total=Sum("amount"))

                days.append(date.strftime("%m/%d"))
                revenues.append(float(revenue.get("total") or 0))

            # Simple text-based chart
            max_revenue = max(revenues) if revenues else 1
            chart_lines = []

            for day, rev in zip(days, revenues):
                bar_len = int((rev / max_revenue) * 20) if max_revenue > 0 else 0
                bar = "█" * bar_len
                chart_lines.append(f"{day}: {bar} {rev:,.0f}")

            text = f"""
📈 نمودار درآمد ۷ روز اخیر

{chr(10).join(chart_lines)}

💰 مجموع: {sum(revenues):,.0f} تومان
            """

            keyboard = self.create_keyboard(
                [
                    [{"text": "📅 ۳۰ روز", "callback_data": "admin_revenue_chart_30"}],
                    [{"text": "📅 ۹۰ روز", "callback_data": "admin_revenue_chart_90"}],
                    [{"text": "🔄 بروزرسانی", "callback_data": "admin_revenue_chart"}],
                    [{"text": "🔙 بازگشت", "callback_data": "admin_revenue_report"}],
                ]
            )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_revenue_report")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def export_revenue_excel(self, callback: types.CallbackQuery):
        """Export revenue report to Excel"""
        from datetime import datetime, timedelta

        from apps.orders.models import Payment

        try:
            # Show processing message
            await callback.answer("⏳ در حال تهیه فایل...", show_alert=False)

            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=30)

            payments = (
                await Payment.objects.filter(
                    brand=self.brand,
                    status="confirmed",
                    created_at__date__gte=start_date,
                    created_at__date__lte=end_date,
                )
                .aselect_related("user")
                .aorder_by("-created_at")
            )

            # Create CSV content (simple text format for Telegram)
            csv_content = "تاریخ,کاربر,روش پرداخت,مبلغ,وضعیت\n"

            async for payment in payments:
                date_str = payment.created_at.strftime("%Y-%m-%d %H:%M")
                csv_content += f"{date_str},{payment.user.username},{payment.get_payment_method_display()},{payment.amount},{payment.get_status_display()}\n"

            # Send as text file
            from aiogram.types import BufferedInputFile

            file_content = csv_content.encode("utf-8-sig")  # BOM for Excel
            file = BufferedInputFile(
                file_content, filename=f"revenue_report_{end_date}.csv"
            )

            await self.bot.send_document(
                callback.message.chat.id,
                file,
                caption=f"📊 گزارش درآمد از {start_date} تا {end_date}\n\nتعداد تراکنش‌ها: {len(payments)}",
            )

            text = "✅ فایل گزارش ارسال شد"

        except Exception as e:
            text = f"❌ خطا در تهیه گزارش: {e}"

        keyboard = self.get_back_keyboard("admin_revenue_report")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    # Card Management Methods

    async def add_payment_card(self, callback: types.CallbackQuery):
        """Start adding a new payment card"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "add_card", "step": "bank_name"},
        )

        text = """
➕ افزودن کارت بانکی جدید

لطفاً نام بانک را وارد کنید:
        """

        keyboard = self.get_back_keyboard("admin_manage_cards")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def edit_cards_list(self, callback: types.CallbackQuery):
        """Show list of cards to edit"""
        from apps.orders.models import PaymentCard

        try:
            cards = await PaymentCard.objects.filter(brand=self.brand).aorder_by(
                "display_order"
            )

            if not cards:
                text = "❌ هیچ کارت بانکی ثبت نشده است"
                keyboard = self.get_back_keyboard("admin_manage_cards")
            else:
                text = "✏️ انتخاب کارت برای ویرایش:\n"
                buttons = []

                async for card in cards:
                    masked = (
                        f"{card.card_number[:4]}****{card.card_number[-4:]}"
                        if len(card.card_number) >= 8
                        else card.card_number
                    )
                    buttons.append(
                        [
                            {
                                "text": f"{'✅' if card.is_active else '❌'} {card.bank_name} - {masked}",
                                "callback_data": f"admin_edit_card_{card.id}",
                            }
                        ]
                    )

                buttons.append(
                    [{"text": "🔙 بازگشت", "callback_data": "admin_manage_cards"}]
                )
                keyboard = self.create_keyboard(buttons)

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_manage_cards")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def edit_card_details(self, callback: types.CallbackQuery, card_id: int):
        """Edit specific card details"""
        from apps.orders.models import PaymentCard

        try:
            card = await PaymentCard.objects.filter(
                brand=self.brand, id=card_id
            ).afirst()

            if not card:
                text = "❌ کارت یافت نشد"
                keyboard = self.get_back_keyboard("admin_manage_cards")
            else:
                masked = (
                    f"{card.card_number[:4]}****{card.card_number[-4:]}"
                    if len(card.card_number) >= 8
                    else card.card_number
                )
                text = f"""
✏️ ویرایش کارت

🏦 بانک: {card.bank_name}
💳 شماره: {masked}
👤 صاحب: {card.cardholder_name}
✅ وضعیت: {"فعال" if card.is_active else "غیرفعال"}
                """

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✏️ تغییر نام بانک",
                                "callback_data": f"admin_card_field_{card_id}_bank_name",
                            }
                        ],
                        [
                            {
                                "text": "✏️ تغییر شماره کارت",
                                "callback_data": f"admin_card_field_{card_id}_card_number",
                            }
                        ],
                        [
                            {
                                "text": "✏️ تغییر صاحب کارت",
                                "callback_data": f"admin_card_field_{card_id}_cardholder",
                            }
                        ],
                        [
                            {
                                "text": "🔄 تغییر وضعیت",
                                "callback_data": f"admin_card_toggle_{card_id}",
                            }
                        ],
                        [{"text": "🔙 بازگشت", "callback_data": "admin_edit_cards"}],
                    ]
                )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_manage_cards")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_edit_card_field(
        self, callback: types.CallbackQuery, card_id: int, field: str
    ):
        """Start editing a card field"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "edit_card", "card_id": card_id, "field": field},
        )

        field_names = {
            "bank_name": "نام بانک",
            "card_number": "شماره کارت",
            "cardholder": "نام صاحب کارت",
        }

        text = f"""
✏️ ویرایش {field_names.get(field, field)}

لطفاً مقدار جدید را وارد کنید:
        """

        keyboard = self.get_back_keyboard(f"admin_edit_card_{card_id}")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def toggle_card_status(self, callback: types.CallbackQuery, card_id: int):
        """Toggle card active status"""
        from apps.orders.models import PaymentCard

        try:
            card = await PaymentCard.objects.filter(
                brand=self.brand, id=card_id
            ).afirst()

            if card:
                card.is_active = not card.is_active
                await card.asave()
                status = "فعال" if card.is_active else "غیرفعال"
                text = f"✅ کارت {status} شد"
            else:
                text = "❌ کارت یافت نشد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard(f"admin_edit_card_{card_id}")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def delete_card_list(self, callback: types.CallbackQuery):
        """Show list of cards to delete"""
        from apps.orders.models import PaymentCard

        try:
            cards = await PaymentCard.objects.filter(brand=self.brand).aorder_by(
                "display_order"
            )

            if not cards:
                text = "❌ هیچ کارت بانکی ثبت نشده است"
                keyboard = self.get_back_keyboard("admin_manage_cards")
            else:
                text = "🗑️ انتخاب کارت برای حذف:\n"
                buttons = []

                async for card in cards:
                    masked = (
                        f"{card.card_number[:4]}****{card.card_number[-4:]}"
                        if len(card.card_number) >= 8
                        else card.card_number
                    )
                    buttons.append(
                        [
                            {
                                "text": f"🗑️ {card.bank_name} - {masked}",
                                "callback_data": f"admin_confirm_delete_card_{card.id}",
                            }
                        ]
                    )

                buttons.append(
                    [{"text": "🔙 بازگشت", "callback_data": "admin_manage_cards"}]
                )
                keyboard = self.create_keyboard(buttons)

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_manage_cards")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def confirm_delete_card(self, callback: types.CallbackQuery, card_id: int):
        """Confirm card deletion"""
        from apps.orders.models import PaymentCard

        try:
            card = await PaymentCard.objects.filter(
                brand=self.brand, id=card_id
            ).afirst()

            if not card:
                text = "❌ کارت یافت نشد"
                keyboard = self.get_back_keyboard("admin_manage_cards")
            else:
                masked = (
                    f"{card.card_number[:4]}****{card.card_number[-4:]}"
                    if len(card.card_number) >= 8
                    else card.card_number
                )
                text = f"""
⚠️ آیا از حذف این کارت مطمئن هستید؟

🏦 {card.bank_name}
💳 {masked}
👤 {card.cardholder_name}
                """

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✅ بله، حذف شود",
                                "callback_data": f"admin_do_delete_card_{card_id}",
                            }
                        ],
                        [{"text": "❌ خیر", "callback_data": "admin_delete_card"}],
                    ]
                )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_manage_cards")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def do_delete_card(self, callback: types.CallbackQuery, card_id: int):
        """Actually delete the card"""
        from apps.orders.models import PaymentCard

        try:
            card = await PaymentCard.objects.filter(
                brand=self.brand, id=card_id
            ).afirst()

            if card:
                await card.adelete()
                text = "✅ کارت با موفقیت حذف شد"
            else:
                text = "❌ کارت یافت نشد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard("admin_manage_cards")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    # Crypto Currency Management Methods

    async def add_crypto_currency(self, callback: types.CallbackQuery):
        """Start adding a new cryptocurrency"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "add_crypto", "step": "name"},
        )

        text = """
➕ افزودن ارز دیجیتال جدید

لطفاً نام ارز را وارد کنید (مثلاً: Bitcoin):
        """

        keyboard = self.get_back_keyboard("admin_manage_crypto")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def edit_cryptos_list(self, callback: types.CallbackQuery):
        """Show list of cryptos to edit"""
        from apps.orders.models import CryptoCurrency

        try:
            cryptos = await CryptoCurrency.objects.filter(brand=self.brand).aorder_by(
                "display_order"
            )

            if not cryptos:
                text = "❌ هیچ ارز دیجیتالی ثبت نشده است"
                keyboard = self.get_back_keyboard("admin_manage_crypto")
            else:
                text = "✏️ انتخاب ارز برای ویرایش:\n"
                buttons = []

                async for crypto in cryptos:
                    status = "✅" if crypto.is_active else "❌"
                    buttons.append(
                        [
                            {
                                "text": f"{status} {crypto.name} ({crypto.symbol})",
                                "callback_data": f"admin_edit_crypto_{crypto.id}",
                            }
                        ]
                    )

                buttons.append(
                    [{"text": "🔙 بازگشت", "callback_data": "admin_manage_crypto"}]
                )
                keyboard = self.create_keyboard(buttons)

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_manage_crypto")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def edit_crypto_details(self, callback: types.CallbackQuery, crypto_id: int):
        """Edit specific crypto details"""
        from apps.orders.models import CryptoCurrency

        try:
            crypto = await CryptoCurrency.objects.filter(
                brand=self.brand, id=crypto_id
            ).afirst()

            if not crypto:
                text = "❌ ارز یافت نشد"
                keyboard = self.get_back_keyboard("admin_manage_crypto")
            else:
                text = f"""
✏️ ویرایش ارز دیجیتال

🪙 نام: {crypto.name}
💱 نماد: {crypto.symbol}
🌐 شبکه: {crypto.get_network_display()}
📍 آدرس: {crypto.wallet_address[:30]}...
✅ وضعیت: {"فعال" if crypto.is_active else "غیرفعال"}
                """

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✏️ تغییر نام",
                                "callback_data": f"admin_crypto_field_{crypto_id}_name",
                            }
                        ],
                        [
                            {
                                "text": "✏️ تغییر نماد",
                                "callback_data": f"admin_crypto_field_{crypto_id}_symbol",
                            }
                        ],
                        [
                            {
                                "text": "✏️ تغییر آدرس",
                                "callback_data": f"admin_crypto_field_{crypto_id}_address",
                            }
                        ],
                        [
                            {
                                "text": "🔄 تغییر وضعیت",
                                "callback_data": f"admin_crypto_toggle_{crypto_id}",
                            }
                        ],
                        [{"text": "🔙 بازگشت", "callback_data": "admin_edit_cryptos"}],
                    ]
                )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_manage_crypto")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def start_edit_crypto_field(
        self, callback: types.CallbackQuery, crypto_id: int, field: str
    ):
        """Start editing a crypto field"""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "edit_crypto", "crypto_id": crypto_id, "field": field},
        )

        field_names = {
            "name": "نام ارز",
            "symbol": "نماد",
            "address": "آدرس کیف پول",
        }

        text = f"""
✏️ ویرایش {field_names.get(field, field)}

لطفاً مقدار جدید را وارد کنید:
        """

        keyboard = self.get_back_keyboard(f"admin_edit_crypto_{crypto_id}")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def toggle_crypto_status(self, callback: types.CallbackQuery, crypto_id: int):
        """Toggle crypto active status"""
        from apps.orders.models import CryptoCurrency

        try:
            crypto = await CryptoCurrency.objects.filter(
                brand=self.brand, id=crypto_id
            ).afirst()

            if crypto:
                crypto.is_active = not crypto.is_active
                await crypto.asave()
                status = "فعال" if crypto.is_active else "غیرفعال"
                text = f"✅ ارز {status} شد"
            else:
                text = "❌ ارز یافت نشد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard(f"admin_edit_crypto_{crypto_id}")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def delete_crypto_list(self, callback: types.CallbackQuery):
        """Show list of cryptos to delete"""
        from apps.orders.models import CryptoCurrency

        try:
            cryptos = await CryptoCurrency.objects.filter(brand=self.brand).aorder_by(
                "display_order"
            )

            if not cryptos:
                text = "❌ هیچ ارز دیجیتالی ثبت نشده است"
                keyboard = self.get_back_keyboard("admin_manage_crypto")
            else:
                text = "🗑️ انتخاب ارز برای حذف:\n"
                buttons = []

                async for crypto in cryptos:
                    buttons.append(
                        [
                            {
                                "text": f"🗑️ {crypto.name} ({crypto.symbol})",
                                "callback_data": f"admin_confirm_delete_crypto_{crypto.id}",
                            }
                        ]
                    )

                buttons.append(
                    [{"text": "🔙 بازگشت", "callback_data": "admin_manage_crypto"}]
                )
                keyboard = self.create_keyboard(buttons)

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_manage_crypto")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def confirm_delete_crypto(
        self, callback: types.CallbackQuery, crypto_id: int
    ):
        """Confirm crypto deletion"""
        from apps.orders.models import CryptoCurrency

        try:
            crypto = await CryptoCurrency.objects.filter(
                brand=self.brand, id=crypto_id
            ).afirst()

            if not crypto:
                text = "❌ ارز یافت نشد"
                keyboard = self.get_back_keyboard("admin_manage_crypto")
            else:
                text = f"""
⚠️ آیا از حذف این ارز مطمئن هستید؟

🪙 {crypto.name} ({crypto.symbol})
🌐 {crypto.get_network_display()}
                """

                keyboard = self.create_keyboard(
                    [
                        [
                            {
                                "text": "✅ بله، حذف شود",
                                "callback_data": f"admin_do_delete_crypto_{crypto_id}",
                            }
                        ],
                        [{"text": "❌ خیر", "callback_data": "admin_delete_crypto"}],
                    ]
                )

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_manage_crypto")

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def do_delete_crypto(self, callback: types.CallbackQuery, crypto_id: int):
        """Actually delete the crypto"""
        from apps.orders.models import CryptoCurrency

        try:
            crypto = await CryptoCurrency.objects.filter(
                brand=self.brand, id=crypto_id
            ).afirst()

            if crypto:
                await crypto.adelete()
                text = "✅ ارز با موفقیت حذف شد"
            else:
                text = "❌ ارز یافت نشد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard("admin_manage_crypto")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def update_crypto_rates(self, callback: types.CallbackQuery):
        """Update cryptocurrency exchange rates"""
        from apps.orders.models import CryptoCurrency

        try:
            cryptos = await CryptoCurrency.objects.filter(
                brand=self.brand, is_active=True
            ).aall()

            updated = 0
            async for crypto in cryptos:
                # In a real implementation, fetch from API
                # For now, just update the timestamp
                crypto.last_rate_update = timezone.now()
                await crypto.asave()
                updated += 1

            text = f"✅ نرخ {updated} ارز بروزرسانی شد"

        except Exception as e:
            text = f"❌ خطا: {e}"

        keyboard = self.get_back_keyboard("admin_manage_crypto")
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    # Handle admin message for card, crypto, gateway and other admin actions

    async def handle_card_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle card-related text messages"""
        from apps.orders.models import PaymentCard

        step = state.state_data.get("step")
        card_data = state.state_data.get("card_data", {})

        if step == "bank_name":
            card_data["bank_name"] = message.text.strip()
            state.state_data["card_data"] = card_data
            state.state_data["step"] = "card_number"
            await state.asave()

            text = "لطفاً شماره کارت را وارد کنید:"
            keyboard = self.get_back_keyboard("admin_manage_cards")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "card_number":
            card_number = message.text.strip().replace(" ", "")
            if not card_number.isdigit() or len(card_number) < 16:
                await message.reply("❌ لطفاً یک شماره کارت معتبر وارد کنید.")
                return

            card_data["card_number"] = card_number
            state.state_data["card_data"] = card_data
            state.state_data["step"] = "cardholder_name"
            await state.asave()

            text = "لطفاً نام صاحب کارت را وارد کنید:"
            keyboard = self.get_back_keyboard("admin_manage_cards")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "cardholder_name":
            card_data["cardholder_name"] = message.text.strip()

            # Create the card
            try:
                await PaymentCard.objects.acreate(
                    brand=self.brand,
                    bank_name=card_data["bank_name"],
                    card_number=card_data["card_number"],
                    cardholder_name=card_data["cardholder_name"],
                    is_active=True,
                )

                text = f"""
✅ کارت با موفقیت اضافه شد

🏦 بانک: {card_data["bank_name"]}
💳 شماره: {card_data["card_number"][:4]}****{card_data["card_number"][-4:]}
👤 صاحب: {card_data["cardholder_name"]}
                """

            except Exception as e:
                text = f"❌ خطا در ایجاد کارت: {e}"

            keyboard = self.get_back_keyboard("admin_manage_cards")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    async def handle_crypto_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle crypto-related text messages"""
        from apps.orders.models import CryptoCurrency

        step = state.state_data.get("step")
        crypto_data = state.state_data.get("crypto_data", {})

        if step == "name":
            crypto_data["name"] = message.text.strip()
            state.state_data["crypto_data"] = crypto_data
            state.state_data["step"] = "symbol"
            await state.asave()

            text = "لطفاً نماد ارز را وارد کنید (مثلاً: BTC):"
            keyboard = self.get_back_keyboard("admin_manage_crypto")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "symbol":
            crypto_data["symbol"] = message.text.strip().upper()
            state.state_data["crypto_data"] = crypto_data
            state.state_data["step"] = "network"
            await state.asave()

            text = "شبکه را انتخاب کنید:"
            keyboard = self.create_keyboard(
                [
                    [{"text": "Bitcoin", "callback_data": "crypto_network_bitcoin"}],
                    [{"text": "Ethereum", "callback_data": "crypto_network_ethereum"}],
                    [{"text": "Tron (TRC20)", "callback_data": "crypto_network_tron"}],
                    [{"text": "BSC (BEP20)", "callback_data": "crypto_network_bsc"}],
                    [{"text": "Polygon", "callback_data": "crypto_network_polygon"}],
                    [{"text": "❌ انصراف", "callback_data": "admin_manage_crypto"}],
                ]
            )
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "wallet_address":
            crypto_data["wallet_address"] = message.text.strip()

            # Create the crypto
            try:
                await CryptoCurrency.objects.acreate(
                    brand=self.brand,
                    name=crypto_data["name"],
                    symbol=crypto_data["symbol"],
                    network=crypto_data["network"],
                    wallet_address=crypto_data["wallet_address"],
                    is_active=True,
                )

                text = f"""
✅ ارز دیجیتال با موفقیت اضافه شد

🪙 نام: {crypto_data["name"]}
💱 نماد: {crypto_data["symbol"]}
🌐 شبکه: {crypto_data["network"]}
📍 آدرس: {crypto_data["wallet_address"][:30]}...
                """

            except Exception as e:
                text = f"❌ خطا در ایجاد ارز: {e}"

            keyboard = self.get_back_keyboard("admin_manage_crypto")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    async def handle_crypto_network_selection(
        self, callback: types.CallbackQuery, network: str
    ):
        """Handle crypto network selection"""
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)

        state.state_data.setdefault("crypto_data", {})["network"] = network
        state.state_data["step"] = "wallet_address"
        await state.asave()

        text = "لطفاً آدرس کیف پول را وارد کنید:"
        keyboard = self.get_back_keyboard("admin_manage_crypto")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()

    async def handle_gateway_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle gateway-related text messages"""
        from apps.orders.models import PaymentGateway

        step = state.state_data.get("step")
        gateway_data = state.state_data.get("gateway_data", {})

        if step == "name":
            gateway_data["name"] = message.text.strip()
            state.state_data["gateway_data"] = gateway_data
            state.state_data["step"] = "api_key"
            await state.asave()

            text = "لطفاً API Key را وارد کنید:"
            keyboard = self.get_back_keyboard("admin_payment_gateways")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "api_key":
            gateway_data["api_key"] = message.text.strip()
            state.state_data["gateway_data"] = gateway_data
            state.state_data["step"] = "secret_key"
            await state.asave()

            text = "لطفاً Secret Key را وارد کنید (یا 0 برای رد کردن):"
            keyboard = self.get_back_keyboard("admin_payment_gateways")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        elif step == "secret_key":
            secret = message.text.strip()
            gateway_data["secret_key"] = None if secret == "0" else secret

            # Create the gateway
            try:
                await PaymentGateway.objects.acreate(
                    brand=self.brand,
                    name=gateway_data["name"],
                    gateway_type=gateway_data["gateway_type"],
                    api_key=gateway_data["api_key"],
                    secret_key=gateway_data.get("secret_key"),
                    is_sandbox=True,
                    is_active=True,
                )

                text = f"""
✅ درگاه پرداخت با موفقیت اضافه شد

📌 نام: {gateway_data["name"]}
🔧 نوع: {gateway_data["gateway_type"]}
⚠️ حالت تستی: فعال
                """

            except Exception as e:
                text = f"❌ خطا در ایجاد درگاه: {e}"

            keyboard = self.get_back_keyboard("admin_payment_gateways")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    async def handle_payment_search_message(
        self, message: types.Message, user: User, state: BotState
    ):
        """Handle payment search message"""
        from apps.orders.models import Payment

        query = message.text.strip()

        try:
            # Search by payment ID, user username, or amount
            payments = (
                await Payment.objects.filter(brand=self.brand)
                .filter(
                    Q(payment_id__icontains=query)
                    | Q(user__username__icontains=query)
                    | Q(amount__icontains=query)
                )
                .aselect_related("user")
                .aorder_by("-created_at")[:10]
            )

            if not payments:
                text = f"❌ هیچ تراکنشی با عبارت «{query}» یافت نشد"
            else:
                text = f"🔍 نتایج جستجو برای «{query}»:\n\n"

                async for i, payment in enumerate(payments, 1):
                    status_emoji = {
                        "pending": "⏳",
                        "awaiting_confirmation": "🔍",
                        "confirmed": "✅",
                        "failed": "❌",
                        "cancelled": "🚫",
                        "refunded": "↩️",
                    }.get(payment.status, "❓")

                    text += (
                        f"{i}. {status_emoji} {payment.get_payment_method_display()}\n"
                    )
                    text += f"   مبلغ: {payment.amount:,.0f} {payment.currency}\n"
                    text += f"   کاربر: {payment.user.username}\n\n"

            keyboard = self.get_back_keyboard("admin_payment_history_1")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        except Exception as e:
            text = f"❌ خطا: {e}"
            keyboard = self.get_back_keyboard("admin_payment_history_1")
            await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        await self.update_user_state(user, BotState.StateType.MAIN_MENU)

    # Additional callback handlers for crypto network selection

    async def handle_crypto_network_callback(
        self, callback: types.CallbackQuery, network: str
    ):
        """Handle crypto network selection callback"""
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)

        state.state_data.setdefault("crypto_data", {})["network"] = network
        state.state_data["step"] = "wallet_address"
        await state.asave()

        text = "لطفاً آدرس کیف پول را وارد کنید:"
        keyboard = self.get_back_keyboard("admin_manage_crypto")
        await self.send_message_with_keyboard(callback.message.chat.id, text, keyboard)
        await callback.answer()
