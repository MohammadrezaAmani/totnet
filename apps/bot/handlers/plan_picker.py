"""Stateful, cache-backed service → traffic → users → duration → plans flow."""

import secrets
from html import escape

from apps.bot.models import BotState
from apps.subscriptions.catalog import get_cached_plans
from apps.subscriptions.selection import (
    duration_key,
    duration_label,
    duration_options,
    matching_plans,
    recommended_plans,
    traffic_key,
    traffic_label,
    traffic_options,
    traffic_ranges,
    volume_user_price_is_constant,
)


class PlanPickerMixin:
    OPTION_PAGE_SIZE = 9
    RESULT_PAGE_SIZE = 8
    PICKER_STAGES = ("traffic", "users", "duration", "results")

    async def show_service_picker(self, callback, category):
        user, _ = await self.get_or_create_user(callback.from_user)
        picker = {
            "token": secrets.token_hex(3),
            "category": category,
            "stage": "traffic",
            "traffic": [],
            "users": [],
            "durations": [],
            "page": 1,
        }
        await self.render_plan_picker(callback, user, picker)

    async def _picker_catalog(self, picker):
        return [
            plan
            for plan in await get_cached_plans(self.brand.pk)
            if plan.service_category == picker["category"]
        ]

    @staticmethod
    def _picker_button(picker, text, action, value="", style=None):
        return {
            "text": text,
            "callback_data": f"pf:{picker['token']}:{action}:{value}",
            "style": style,
        }

    @staticmethod
    def _picker_grid(buttons, columns=3):
        return [
            buttons[index : index + columns]
            for index in range(0, len(buttons), columns)
        ]

    def _picker_options(self, plans, picker):
        stage = picker["stage"]
        if stage == "traffic":
            return traffic_options(plans), picker["traffic"], traffic_label
        if stage == "users":
            candidates = matching_plans(plans, picker, through="traffic")
            return (
                sorted({plan.max_users for plan in candidates}),
                picker["users"],
                lambda n: f"{n} کاربر",
            )
        candidates = matching_plans(plans, picker, through="users")
        return duration_options(candidates), picker["durations"], duration_label

    async def render_plan_picker(self, callback, user, picker):
        plans = await self._picker_catalog(picker)
        stage = picker["stage"]
        title = escape(self.CATEGORY_LABELS[picker["category"]])
        lines = [f"🛒 <b>{title}</b>"]
        rows = []
        if not plans:
            lines.append("در حال حاضر پلن فعالی برای این سرویس موجود نیست.")
            rows.append(
                [{"text": "🔙 سرویس‌ها", "callback_data": "purchase_subscription"}]
            )
        elif stage == "results":
            results = matching_plans(plans, picker)
            results.sort(
                key=lambda plan: (plan.discounted_price, plan.display_order, plan.pk)
            )
            last_page = max(
                1, (len(results) + self.RESULT_PAGE_SIZE - 1) // self.RESULT_PAGE_SIZE
            )
            picker["page"] = max(1, min(picker["page"], last_page))
            start = (picker["page"] - 1) * self.RESULT_PAGE_SIZE
            lines.append(f"\n<b>پلن‌های مطابق انتخاب شما</b> · {len(results)} پلن")
            lines.extend(self._picker_summary(picker))
            if not results:
                lines.append(
                    "\nبرای این ترکیب پلنی موجود نیست؛ با بازگشت انتخاب‌ها را تغییر دهید."
                )
            for number, plan in enumerate(
                results[start : start + self.RESULT_PAGE_SIZE], start + 1
            ):
                label = (
                    f"{number}. {traffic_label(traffic_key(plan))} · {plan.max_users} کاربر · "
                    f"{duration_label(duration_key(plan))} · {self.format_price(plan.discounted_price, plan.currency)}"
                )
                rows.append(
                    [
                        {
                            "text": label,
                            "callback_data": f"select_plan_{plan.pk}",
                            "style": "success" if plan.is_featured else None,
                        }
                    ]
                )
                # Names disambiguate real plans with equal specifications or different provider families.
                lines.append(f"\n{number}. <code>{escape(plan.name[:100])}</code>")
            if last_page > 1:
                lines.append(f"\nصفحهٔ {picker['page']} از {last_page}")
            rows.extend(self._picker_pagination(picker, last_page))
            rows.append([self._picker_button(picker, "🔙 تغییر زمان", "back")])
            rows.append(
                [
                    self._picker_button(picker, "🎛 تغییر ترافیک", "stage", "traffic"),
                    self._picker_button(
                        picker, "👥 تغییر تعداد کاربر", "stage", "users"
                    ),
                ]
            )
            rows.append(
                [{"text": "🔙 سرویس‌ها", "callback_data": "purchase_subscription"}]
            )
        else:
            if stage == "traffic":
                popular = recommended_plans(plans)
                if popular:
                    heading = (
                        "پلن‌های پرطرفدار"
                        if any(getattr(p, "purchase_count", 0) for p in popular)
                        else "پلن‌های پیشنهادی"
                    )
                    lines.append(
                        f"\n⭐ <b>{heading}</b>\nخرید سریع یا انتخاب دلخواه با فیلترهای زیر:"
                    )
                    rows.extend(
                        self._picker_grid(
                            [
                                {
                                    "text": f"{traffic_label(traffic_key(plan))} · {duration_label(duration_key(plan))} · {self.format_price(plan.discounted_price, plan.currency)}",
                                    "callback_data": f"select_plan_{plan.pk}",
                                    "style": "primary",
                                }
                                for plan in popular
                            ],
                            columns=2,
                        )
                    )
                lines.append(
                    "\n<b>۱. ترافیک‌های دلخواهت را انتخاب کن</b>\nمی‌توانی چند حجم یا یک بازه را انتخاب کنی."
                )
                if volume_user_price_is_constant(plans):
                    lines.append(
                        "تعداد کاربر در هزینهٔ پلن‌های حجمیِ هم‌حجم و هم‌زمان یک سرویس تغییری ایجاد نمی‌کند."
                    )
                else:
                    lines.append(
                        "تعداد کاربر را در مرحلهٔ بعد انتخاب می‌کنی؛ قیمت دقیق هر پلن پیش از خرید نمایش داده می‌شود."
                    )
            elif stage == "users":
                lines.extend(self._picker_summary(picker, through="traffic"))
                lines.append(
                    "\n<b>۲. تعداد کاربر را انتخاب کن</b>\nمی‌توانی یک یا چند تعداد را انتخاب کنی."
                )
                candidates = matching_plans(plans, picker, through="traffic")
                if volume_user_price_is_constant(candidates) and any(
                    traffic_key(p).startswith("v") for p in candidates
                ):
                    lines.append(
                        "در پلن‌های حجمیِ هم‌حجم و هم‌زمان، تعداد کاربر روی هزینه اثر ندارد."
                    )
            else:
                lines.extend(self._picker_summary(picker, through="users"))
                lines.append(
                    "\n<b>۳. مدت زمان دلخواهت را انتخاب کن</b>\nمی‌توانی چند زمان را انتخاب کنی."
                )
            options, selected, labeler = self._picker_options(plans, picker)
            last_page = max(
                1, (len(options) + self.OPTION_PAGE_SIZE - 1) // self.OPTION_PAGE_SIZE
            )
            picker["page"] = max(1, min(picker["page"], last_page))
            start = (picker["page"] - 1) * self.OPTION_PAGE_SIZE
            if stage == "traffic":
                ranges = traffic_ranges(options)
                if len(options) > 6:
                    rows.extend(
                        self._picker_grid(
                            [
                                self._picker_button(
                                    picker,
                                    ("✅ " if set(keys) <= set(selected) else "")
                                    + label,
                                    "range",
                                    index,
                                    "success" if set(keys) <= set(selected) else None,
                                )
                                for index, label, keys in ranges
                            ]
                        )
                    )
            rows.extend(
                self._picker_grid(
                    [
                        self._picker_button(
                            picker,
                            ("✅ " if option in selected else "")
                            + (
                                "⭐ نامحدود • پیشنهاد ما"
                                if stage == "traffic" and option == "u"
                                else labeler(option)
                            ),
                            "toggle",
                            str(option),
                            "success"
                            if option in selected
                            else "primary"
                            if stage == "traffic" and option == "u"
                            else None,
                        )
                        for option in options[start : start + self.OPTION_PAGE_SIZE]
                    ],
                    columns=4 if stage == "users" else 3,
                )
            )
            if last_page > 1:
                lines.append(f"\nگزینه‌ها: صفحهٔ {picker['page']} از {last_page}")
            rows.extend(self._picker_pagination(picker, last_page))
            if selected:
                description = "، ".join(labeler(option) for option in selected)
                # Keep the message bounded for very large catalogs / range selections.
                lines.append(f"\n✅ انتخاب شما: {description[:500]}")
            rows.append(
                [
                    self._picker_button(picker, "انتخاب همه", "all"),
                    self._picker_button(
                        picker, "پاک کردن انتخاب‌ها", "clear", style="danger"
                    ),
                ]
            )
            next_label = {
                "traffic": "ادامه: تعداد کاربر ←",
                "users": "ادامه: زمان ←",
                "duration": "نمایش پلن‌های مناسب ←",
            }[stage]
            rows.append(
                [self._picker_button(picker, next_label, "next", style="success")]
            )
            rows.append(
                [
                    {"text": "🔙 سرویس‌ها", "callback_data": "purchase_subscription"}
                    if stage == "traffic"
                    else self._picker_button(picker, "🔙 مرحلهٔ قبل", "back")
                ]
            )
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {
                "step": "plan_selection" if stage == "results" else "plan_filters",
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

    def _picker_pagination(self, picker, last_page):
        buttons = [
            self._picker_button(picker, label, "page", str(page))
            for page, label in (
                (picker["page"] - 1, "◀️ قبلی"),
                (picker["page"] + 1, "بعدی ▶️"),
            )
            if 1 <= page <= last_page
        ]
        return [buttons] if buttons else []

    @staticmethod
    def _picker_summary(picker, through="duration"):
        lines = [
            "\n📊 ترافیک: "
            + "، ".join(traffic_label(value) for value in picker["traffic"])[:500]
        ]
        if through != "traffic":
            lines.append(
                "👥 تعداد کاربر: "
                + "، ".join(str(value) for value in picker["users"])[:300]
            )
        if through == "duration":
            lines.append(
                "⏰ زمان: "
                + "، ".join(duration_label(value) for value in picker["durations"])[
                    :500
                ]
            )
        return lines

    async def handle_plan_filter(self, callback):
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)
        picker = (state.state_data or {}).get("plan_picker")
        parts = (callback.data or "").split(":")
        if (
            len(parts) != 4
            or not picker
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
        stage = picker["stage"]
        selection_field = {
            "traffic": "traffic",
            "users": "users",
            "duration": "durations",
        }.get(stage)
        if action in {"toggle", "range", "all", "clear", "next"}:
            if not selection_field:
                await callback.answer("برای تغییر انتخاب‌ها به مرحلهٔ قبل برگردید.")
                return
            options, selected, _ = self._picker_options(plans, picker)
            selected = [option for option in selected if option in options]
            if action == "next":
                if not selected:
                    await callback.answer(
                        "حداقل یک گزینه انتخاب کنید.", show_alert=True
                    )
                    return
                picker[selection_field] = selected
                picker["stage"] = self.PICKER_STAGES[
                    self.PICKER_STAGES.index(stage) + 1
                ]
                picker["page"] = 1
            else:
                if action == "toggle":
                    option = (
                        int(value) if stage == "users" and value.isdigit() else value
                    )
                    if option not in options:
                        await callback.answer(
                            "این گزینه دیگر در دسترس نیست.", show_alert=True
                        )
                        return
                    selected = (
                        [item for item in selected if item != option]
                        if option in selected
                        else selected + [option]
                    )
                elif action == "range":
                    ranges = (
                        {index: keys for index, _, keys in traffic_ranges(options)}
                        if stage == "traffic"
                        else {}
                    )
                    if value not in ranges:
                        await callback.answer("بازهٔ نامعتبر است.")
                        return
                    keys = ranges[value]
                    selected = (
                        [item for item in selected if item not in keys]
                        if set(keys) <= set(selected)
                        else list(dict.fromkeys(selected + keys))
                    )
                else:
                    selected = options if action == "all" else []
                picker[selection_field] = selected
                if stage == "traffic":
                    picker["users"] = []
                if stage in {"traffic", "users"}:
                    picker["durations"] = []
        elif action in {"back", "stage"}:
            target = (
                self.PICKER_STAGES[max(0, self.PICKER_STAGES.index(stage) - 1)]
                if action == "back"
                else value
            )
            if (
                target not in self.PICKER_STAGES
                or (target != "traffic" and not picker["traffic"])
                or (target in {"duration", "results"} and not picker["users"])
                or (target == "results" and not picker["durations"])
            ):
                await callback.answer("ابتدا انتخاب مرحلهٔ قبل را کامل کنید.")
                return
            picker["stage"] = target
            picker["page"] = 1
        elif action == "page":
            if not value.isdigit() or not 1 <= int(value) <= 100000:
                await callback.answer("صفحهٔ نامعتبر است.")
                return
            picker["page"] = int(value)
        else:
            await callback.answer("گزینهٔ نامعتبر است.")
            return
        await self.render_plan_picker(callback, user, picker)
