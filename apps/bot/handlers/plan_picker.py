"""Duration and four volume ranges, then users and matching real catalog plans."""

import secrets
from decimal import Decimal
from html import escape

from apps.bot.models import BotState
from apps.subscriptions.catalog import get_cached_plans
from apps.subscriptions.selection import (
    duration_key,
    duration_label,
    duration_options,
    traffic_key,
    traffic_label,
)


class PlanPickerMixin:
    RESULT_PAGE_SIZE = 8

    async def show_service_picker(self, callback, category):
        user, _ = await self.get_or_create_user(callback.from_user)
        picker = {
            "version": 2,
            "token": secrets.token_hex(3),
            "category": category,
            "stage": "bundle",
            "traffic": [],
            "users": [],
            "durations": [],
            "range": "",
            "page": 1,
        }
        await self.render_plan_picker(callback, user, picker)

    async def _picker_catalog(self, picker):
        return [
            p
            for p in await get_cached_plans(self.brand.pk)
            if p.service_category == picker["category"]
        ]

    @staticmethod
    def _picker_button(picker, text, action, value=""):
        return {"text": text, "callback_data": f"pf:{picker['token']}:{action}:{value}"}

    @staticmethod
    def _picker_grid(buttons, columns=2):
        return [buttons[i : i + columns] for i in range(0, len(buttons), columns)]

    @staticmethod
    def _volume_ranges(picker):
        normal = picker["category"] == "normal"
        return [
            (
                "low",
                "۲۰ تا ۳۰ گیگ" if normal else "۱۰ تا ۳۰ گیگ",
                Decimal(20 if normal else 10),
                Decimal(30),
            ),
            ("medium", "۳۰ تا ۵۰ گیگ", Decimal(30), Decimal(50)),
            ("high", "۶۰ تا ۱۰۰ گیگ", Decimal(60), Decimal(100)),
            ("unlimited", "نامحدود", None, None),
        ]

    def _range_keys(self, plans, picker, range_id):
        entry = next((r for r in self._volume_ranges(picker) if r[0] == range_id), None)
        if not entry:
            return []
        _, _, low, high = entry
        keys = set()
        for plan in plans:
            key = traffic_key(plan)
            if range_id == "unlimited" and key == "u":
                keys.add(key)
            elif key.startswith("v") and low is not None:
                volume = Decimal(key[1:])
                if (
                    low < volume if range_id == "medium" else low <= volume
                ) and volume <= high:
                    keys.add(key)
        return sorted(
            keys, key=lambda k: Decimal(k[1:]) if k.startswith("v") else Decimal(0)
        )

    @staticmethod
    def _candidates(plans, picker, include_users=False):
        return [
            p
            for p in plans
            if (not picker["durations"] or duration_key(p) in picker["durations"])
            and (not picker["traffic"] or traffic_key(p) in picker["traffic"])
            and (
                not include_users
                or not picker["users"]
                or p.max_users in picker["users"]
            )
        ]

    def _summary(self, picker):
        label = next(
            (r[1] for r in self._volume_ranges(picker) if r[0] == picker["range"]),
            "انتخاب نشده",
        )
        return [
            "⏰ مدت اشتراک: "
            + "، ".join(duration_label(d) for d in picker["durations"]),
            "📦 حجم: " + label,
        ]

    async def render_plan_picker(self, callback, user, picker):
        plans = await self._picker_catalog(picker)
        trial_plans = [p for p in plans if p.discounted_price <= 0]
        eligible_keys = set().union(
            *(
                self._range_keys(plans, picker, r[0])
                for r in self._volume_ranges(picker)
            )
        )
        plans = [p for p in plans if traffic_key(p) in eligible_keys]
        lines = [f"🛒 <b>{escape(self.CATEGORY_LABELS[picker['category']])}</b>"]
        rows = []
        if not plans:
            lines.append("\nدر حال حاضر پلن فعالی برای این سرویس موجود نیست.")
        elif picker["stage"] == "bundle":
            lines.append("\n<b>۱. مدت اشتراک را انتخاب کنید</b>")
            rows.extend(
                self._picker_grid(
                    [
                        self._picker_button(
                            picker,
                            ("✅ " if d in picker["durations"] else "")
                            + duration_label(d),
                            "duration",
                            d,
                        )
                        for d in duration_options(plans)
                    ],
                    columns=3,
                )
            )
            lines.append(
                "\n<b>۲. بازه حجم را انتخاب کنید</b>\nبعد از انتخاب مدت و حجم، تعداد کاربر را در صفحه بعد انتخاب می‌کنید."
            )
            duration_plans = [
                p
                for p in plans
                if not picker["durations"] or duration_key(p) in picker["durations"]
            ]
            rows.extend(
                self._picker_grid(
                    [
                        self._picker_button(
                            picker,
                            ("✅ " if picker["range"] == key else "") + label,
                            "volume",
                            key,
                        )
                        for key, label, _, _ in self._volume_ranges(picker)
                    ]
                )
            )
            if picker["durations"]:
                lines.append(
                    "\n⏰ مدت انتخاب‌شده: " + duration_label(picker["durations"][0])
                )
                unavailable = [
                    label
                    for key, label, _, _ in self._volume_ranges(picker)
                    if not self._range_keys(duration_plans, picker, key)
                ]
                if unavailable:
                    lines.append(
                        "در این مدت فعلاً موجود نیست: " + "، ".join(unavailable)
                    )
        elif picker["stage"] == "users":
            lines.extend(
                ["", *self._summary(picker), "\n<b>۳. تعداد کاربر را انتخاب کنید</b>"]
            )
            rows.extend(
                self._picker_grid(
                    [
                        self._picker_button(
                            picker, f"{count} کاربر", "users", str(count)
                        )
                        for count in sorted(
                            {p.max_users for p in self._candidates(plans, picker)}
                        )
                    ],
                    columns=3,
                )
            )
            rows.append([self._picker_button(picker, "🔙 تغییر مدت و حجم", "back")])
        else:
            results = sorted(
                self._candidates(plans, picker, include_users=True),
                key=lambda p: (p.discounted_price, p.display_order, p.pk),
            )
            last_page = max(
                1, (len(results) + self.RESULT_PAGE_SIZE - 1) // self.RESULT_PAGE_SIZE
            )
            picker["page"] = max(1, min(picker["page"], last_page))
            lines.extend(
                [
                    "",
                    *self._summary(picker),
                    f"👥 تعداد کاربر: {picker['users'][0]}",
                    "\n<b>پلن موردنظر را انتخاب کنید</b>",
                ]
            )
            if not results:
                lines.append("این ترکیب دیگر موجود نیست؛ انتخاب‌ها را تغییر دهید.")
            offset = (picker["page"] - 1) * self.RESULT_PAGE_SIZE
            for plan in results[offset : offset + self.RESULT_PAGE_SIZE]:
                rows.append(
                    [
                        {
                            "text": f"{traffic_label(traffic_key(plan))} · {duration_label(duration_key(plan))} · {self.format_price(plan.discounted_price, plan.currency)}",
                            "callback_data": f"select_plan_{plan.pk}",
                        }
                    ]
                )
            if last_page > 1:
                lines.append(f"\nصفحه {picker['page']} از {last_page}")
                rows.append(
                    [
                        self._picker_button(picker, label, "page", str(page))
                        for page, label in (
                            (picker["page"] - 1, "◀️ قبلی"),
                            (picker["page"] + 1, "بعدی ▶️"),
                        )
                        if 1 <= page <= last_page
                    ]
                )
            rows.append([self._picker_button(picker, "🔙 تغییر تعداد کاربر", "back")])
            rows.append([self._picker_button(picker, "🔙 تغییر مدت و حجم", "bundle")])
        if picker["stage"] == "bundle":
            rows.extend(
                [
                    [
                        {
                            "text": "🎁 " + p.name[:80],
                            "callback_data": f"select_plan_{p.pk}",
                        }
                    ]
                    for p in trial_plans[:3]
                ]
            )
        rows.append([{"text": "🔙 سرویس‌ها", "callback_data": "purchase_subscription"}])
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {
                "step": "plan_selection"
                if picker["stage"] == "results"
                else "plan_filters",
                "service_category": picker["category"],
                "special_offers": False,
                "plan_picker": picker,
            },
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            "\n".join(lines),
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def handle_plan_filter(self, callback):
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)
        picker = (state.state_data or {}).get("plan_picker")
        parts = (callback.data or "").split(":")
        if (
            len(parts) != 4
            or not picker
            or picker.get("version") != 2
            or parts[1] != picker.get("token")
            or picker.get("category") not in self.CATEGORY_LABELS
            or state.current_state != BotState.StateType.PURCHASE_FLOW
            or state.state_data.get("step")
            not in {"plan_filters", "plan_selection", "plan_details"}
            or state.state_data.get("special_offers")
        ):
            await callback.answer(
                "این منو قدیمی است؛ دوباره سرویس را انتخاب کنید.", show_alert=True
            )
            return
        _, _, action, value = parts
        plans = await self._picker_catalog(picker)
        if action in {"duration", "volume"} and picker["stage"] == "bundle":
            if action == "duration":
                if value not in duration_options(plans):
                    await callback.answer(
                        "این مدت دیگر در دسترس نیست.", show_alert=True
                    )
                    return
                picker.update(durations=[value], traffic=[], users=[], range="", page=1)
            else:
                if not picker["durations"]:
                    await callback.answer(
                        "ابتدا مدت اشتراک را انتخاب کنید.", show_alert=True
                    )
                    return
                duration_plans = [
                    p for p in plans if duration_key(p) in picker["durations"]
                ]
                keys = self._range_keys(duration_plans, picker, value)
                if not keys:
                    await callback.answer(
                        "برای این مدت و بازه حجم فعلاً پلنی موجود نیست.", show_alert=True
                    )
                    return
                picker.update(
                    traffic=keys, users=[], range=value, stage="users", page=1
                )
        elif action == "users" and picker["stage"] == "users":
            if not value.isdigit() or int(value) not in {
                p.max_users for p in self._candidates(plans, picker)
            }:
                await callback.answer(
                    "این تعداد کاربر دیگر در دسترس نیست.", show_alert=True
                )
                return
            picker.update(users=[int(value)], stage="results", page=1)
        elif action in {"back", "bundle"}:
            picker["stage"] = (
                "users"
                if action == "back" and picker["stage"] == "results"
                else "bundle"
            )
            picker["page"] = 1
        elif action == "page" and picker["stage"] == "results" and value.isdigit():
            picker["page"] = max(1, min(int(value), 100000))
        else:
            await callback.answer("گزینه معتبر نیست؛ از منوی فعلی استفاده کنید.")
            return
        await self.render_plan_picker(callback, user, picker)
