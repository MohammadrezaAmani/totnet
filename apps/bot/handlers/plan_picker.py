"""Catalog-driven subscription selector.

Every callback is scoped to a random session token and every final selection points
at a real active plan. No fictitious prices or plan combinations are constructed.
"""

import secrets
from decimal import Decimal
from html import escape

from apps.bot.models import BotState
from apps.subscriptions.catalog import get_cached_plans
from apps.subscriptions.selection import duration_key, duration_label, traffic_key, traffic_label
from apps.subscriptions.policy import blocked_duration_key, temporary_duration_blocked
from apps.subscriptions.presentation import plan_gift_label


class PlanPickerMixin:
    RESULT_PAGE_SIZE = 8
    STANDARD_MONTHS = frozenset({"m1", "m2", "m3"})

    async def show_service_picker(self, callback, category):
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.render_plan_picker(callback, user, {
            "version": 3, "token": secrets.token_hex(4), "category": category,
            "stage": "bundle", "duration": "", "users": 0,
            "range": "", "page": 1,
        })

    async def _picker_catalog(self, picker):
        plans = [p for p in await get_cached_plans(self.brand.pk)
                 if p.service_category == picker["category"]]
        if not plans:
            plans = [p for p in await get_cached_plans(self.brand.pk, refresh=True)
                     if p.service_category == picker["category"]]
        return plans

    @staticmethod
    def _picker_button(picker, label, action, value=""):
        return {"text": label, "callback_data": f"pf:{picker['token']}:{action}:{value}"}

    @staticmethod
    def _grid(buttons, columns=3):
        return [buttons[i:i + columns] for i in range(0, len(buttons), columns)]

    @staticmethod
    def _volume(plan):
        return traffic_key(plan).startswith("v")

    @staticmethod
    def _duration_sort(key):
        unit = key[:1]
        try:
            return int(key[1:]) * {"d": 1, "w": 7, "m": 30, "y": 365}.get(unit, 9999)
        except ValueError:
            return 99999

    @staticmethod
    def _min_volume(category, duration):
        if category not in {"normal", "royal"}:
            return Decimal(0)
        if duration == "m1":
            return Decimal(10 if category == "royal" else 20)
        if duration == "m2":
            return Decimal(40)
        if duration == "m3":
            return Decimal(60)
        return Decimal(0)

    def _volume_ranges(self, picker, plans):
        """Disjoint, duration-aware ranges populated only by actual catalog entries."""
        minimum = self._min_volume(picker["category"], picker["duration"])
        groups = [
            ("a", minimum, Decimal(30), "حجم‌های کم"),
            ("b", max(minimum, Decimal(31)), Decimal(59), "۳۱ تا ۵۹ گیگ"),
            ("c", max(minimum, Decimal(60)), Decimal(99), "۶۰ تا ۹۹ گیگ"),
            ("d", max(minimum, Decimal(100)), Decimal("Infinity"), "۱۰۰ گیگ و بیشتر"),
        ]
        # When the minimum is 40/60, empty earlier buckets are omitted.
        result = []
        for key, low, high, label in groups:
            matching = [p for p in plans if self._volume(p)
                        and low <= Decimal(traffic_key(p)[1:]) <= high]
            if matching:
                result.append((key, f"{low} تا {high} گیگ" if high.is_finite() else label))
        return result

    def _filtered(self, plans, picker):
        return [p for p in plans if self._volume(p)
                and duration_key(p) == picker["duration"]
                and p.max_users == picker["users"]
                and Decimal(traffic_key(p)[1:]) >= self._min_volume(
                    picker["category"], picker["duration"])]

    def _range_plans(self, plans, picker):
        ranges = self._volume_ranges(picker, plans)
        if picker["range"] not in {key for key, _ in ranges}:
            return []
        # Filter using the actual range boundaries, not an index inferred from visible ranges.
        key = picker["range"]
        minimum = self._min_volume(picker["category"], picker["duration"])
        boundaries = {
            "a": (minimum, Decimal(30)),
            "b": (max(minimum, Decimal(31)), Decimal(59)),
            "c": (max(minimum, Decimal(60)), Decimal(99)),
            "d": (max(minimum, Decimal(100)), Decimal("Infinity")),
        }
        lo, hi = boundaries[key]
        return [p for p in plans if lo <= Decimal(traffic_key(p)[1:]) <= hi]

    @staticmethod
    def _unique_volume_plans(plans):
        """Catalog row per (volume, price, currency): never replace a genuine price."""
        chosen = {}
        for p in sorted(plans, key=lambda p: (
            p.max_users != 1, duration_key(p) != "m1", p.display_order, p.pk)):
            chosen.setdefault((traffic_key(p), p.discounted_price, p.currency), p)
        return sorted(chosen.values(), key=lambda p: (
            p.display_order, p.pk))

    async def render_plan_picker(self, callback, user, picker):
        catalog = await self._picker_catalog(picker)
        volume_plans = [
            p for p in catalog if self._volume(p)
            and Decimal(traffic_key(p)[1:]) >= self._min_volume(picker["category"], duration_key(p))
        ]
        unlimited_plans = [p for p in catalog if traffic_key(p) == "u"]
        time_plans = [p for p in catalog if traffic_key(p) == "n"]
        rows = []
        lines = [f"🛒 <b>{escape(self.CATEGORY_LABELS[picker['category']])}</b>"]
        stage = picker["stage"]

        if not catalog:
            lines.append("\nدر حال حاضر اشتراک فعالی برای این سرویس موجود نیست.")
        elif stage == "bundle":
            lines.extend([
                "\nابتدا مدت زمان و تعداد کاربر مورد نیاز را انتخاب کنید.",
                "\n<b>مدت زمان:</b>",
            ])
            durations = sorted({duration_key(p) for p in volume_plans}, key=self._duration_sort, reverse=True)
            rows.extend(self._grid([
                self._picker_button(picker, ("🔴 " if blocked_duration_key(d) else "✅ " if picker["duration"] == d else "") + duration_label(d), "duration", d)
                for d in durations if d in self.STANDARD_MONTHS
            ]))
            lines.append("<b>تعداد کاربر:</b>")
            available = [p for p in volume_plans if not picker["duration"] or duration_key(p) == picker["duration"]]
            rows.extend(self._grid([
                self._picker_button(picker, ("✅ " if picker["users"] == n else "") + f"{n} کاربر", "users", str(n))
                for n in sorted({p.max_users for p in available if 1 <= p.max_users <= 4})
            ], columns=4))
            if blocked_duration_key(picker["duration"]):
                lines.append("🔴 اشتراک‌های ۲ و ۳ ماهه موقتاً غیرفعال هستند.")
            if not volume_plans:
                lines.append("برای دیدن سایر اشتراک‌ها، «تمام اشتراک‌ها» را انتخاب کنید.")
        elif stage == "ranges":
            filtered = self._filtered(volume_plans, picker)
            lines.append(f"\n🛒 تا این مرحله: {picker['users']} کاربر، {duration_label(picker['duration'])}")
            lines.append("\n<b>بازه حجم را انتخاب کنید:</b>")
            ranges = self._volume_ranges(picker, filtered)
            rows.extend(self._grid([self._picker_button(picker, label, "range", key)
                                    for key, label in ranges], 2))
            if not ranges:
                lines.append("برای این ترکیب حجم فعالی موجود نیست؛ انتخاب‌ها را عوض کنید.")
            rows.append([self._picker_button(picker, "🔙 مدت و تعداد کاربر", "back")])
        elif stage == "all_type":
            lines.append("\nنوع اشتراک را انتخاب کنید:")
            if volume_plans:
                rows.append([self._picker_button(picker, "📦 اشتراک‌های حجمی", "type", "volume")])
            if unlimited_plans:
                rows.append([self._picker_button(picker, "♾ اشتراک‌های نامحدود", "type", "unlimited")])
            if time_plans:
                rows.append([self._picker_button(picker, "⏱ سرویس‌های زمانی", "type", "time")])
            rows.append([self._picker_button(picker, "🔙 بازگشت", "back")])
        elif stage == "unlimited_users":
            lines.append("\n<b>نامحدود (۱ کاربر):</b> مدت دلخواه را انتخاب کنید.")
            credit_by_currency = {c: await self.purchase_credit(user, c)
                                  for c in {p.currency for p in unlimited_plans}}
            for p in sorted((p for p in unlimited_plans if p.max_users == 1 and duration_key(p) in self.STANDARD_MONTHS), key=lambda p: (p.display_order, p.pk)):
                rows.append([{"text": f"{'🔴 ' if temporary_duration_blocked(p) else ''}{duration_label(duration_key(p))}{plan_gift_label(p)} • {self.format_price(self.payable_plan_price(p, credit_by_currency[p.currency]), p.currency)}",
                              "callback_data": f"select_plan_{p.pk}",
                              "style": "danger" if temporary_duration_blocked(p) else None}])
            lines.append("\n<b>تعداد کاربران دیگر:</b>")
            rows.extend(self._grid([self._picker_button(picker, f"{n} کاربر", "allusers", str(n))
                                    for n in sorted({p.max_users for p in unlimited_plans if 1 < p.max_users <= 4})], 3))
            rows.append([self._picker_button(picker, "🔙 نوع اشتراک", "back")])
        elif stage in {"results", "volume_list", "time_list", "unlimited_results"}:
            if stage == "results":
                results = self._range_plans(self._filtered(volume_plans, picker), picker)
                # Show the matching unlimited plan alongside high-volume subscriptions.
                # Use the *highest existing* range, not a hardcoded 100GB threshold.
                ranges = self._volume_ranges(picker, self._filtered(volume_plans, picker))
                if ranges and picker["range"] == ranges[-1][0]:
                    results.extend(p for p in unlimited_plans
                                   if p.max_users == picker["users"]
                                   and duration_key(p) == picker["duration"])
                lines.append(f"\n🛒 تا این مرحله: {picker['users']} کاربر، {duration_label(picker['duration'])}")
            elif stage == "volume_list":
                results = self._unique_volume_plans(volume_plans)
                lines.append("\nاشتراک‌های حجمی — حجم و قیمت:")
                # Warn rather than assert price invariance if admin/upstream data disagree.
                prices = {}
                for p in volume_plans:
                    prices.setdefault((traffic_key(p), p.currency), set()).add(p.discounted_price)
                if any(len(s) > 1 for s in prices.values()):
                    lines.append("⚠️ بعضی حجم‌ها قیمت‌های متفاوتی در کاتالوگ دارند؛ قیمت هر گزینه همان مبلغ واقعی آن است.")
                else:
                    lines.append("در اشتراک حجمی تعداد کاربر و زمان در قیمت تأثیری ندارد.")
            elif stage == "time_list":
                results = time_plans
                lines.append("\n⏱ سرویس‌های زمانی — مشخصات و قیمت هر اشتراک:")
            else:
                results = [p for p in unlimited_plans if p.max_users == picker["users"]
                           and duration_key(p) in self.STANDARD_MONTHS]
                lines.append(f"\n♾ اشتراک نامحدود — {picker['users']} کاربر")
            # Respect the explicit panel order, rather than replacing it with price/volume sorting.
            results = sorted(results, key=lambda p: (p.display_order, p.pk))
            if stage == "results":
                results.sort(key=lambda p: traffic_key(p) == "u")  # comparison option last
                # Keep a comparison choice on page 1 even for a long top bucket.
                if len(results) > self.RESULT_PAGE_SIZE:
                    for index, candidate in enumerate(results):
                        if traffic_key(candidate) == "u" and index >= self.RESULT_PAGE_SIZE:
                            results.insert(self.RESULT_PAGE_SIZE - 1, results.pop(index))
                            break
            page_count = max(1, (len(results) + self.RESULT_PAGE_SIZE - 1) // self.RESULT_PAGE_SIZE)
            picker["page"] = max(1, min(picker["page"], page_count))
            credits = {c: await self.purchase_credit(user, c) for c in {p.currency for p in results}}
            for p in results[(picker["page"]-1)*self.RESULT_PAGE_SIZE:picker["page"]*self.RESULT_PAGE_SIZE]:
                label = (f"{duration_label(duration_key(p))} • {p.max_users} کاربر" if stage == "time_list"
                         else duration_label(duration_key(p)) if stage == "unlimited_results"
                         else "♾ نامحدود" if traffic_key(p) == "u"
                         else traffic_label(traffic_key(p)))
                rows.append([{"text": f"{'🔴 ' if temporary_duration_blocked(p) else ''}{label}{plan_gift_label(p)} • {self.format_price(self.payable_plan_price(p, credits[p.currency]), p.currency)}",
                              "callback_data": f"select_plan_{p.pk}",
                              "style": "danger" if temporary_duration_blocked(p) else None}])
            if not results:
                lines.append("هیچ اشتراکی با این شرایط موجود نیست.")
            if page_count > 1:
                lines.append(f"صفحه {picker['page']} از {page_count}")
                rows.append([self._picker_button(picker, label, "page", str(page)) for page, label in (
                    (picker["page"]-1, "◀️ قبلی"), (picker["page"]+1, "بعدی ▶️"))
                             if 1 <= page <= page_count])
            rows.append([self._picker_button(picker, "🔙 بازگشت", "back")])

        if stage not in {"all_type", "volume_list", "time_list", "unlimited_users", "unlimited_results"}:
            rows.append([self._picker_button(picker, "📋 تمام اشتراک‌های این سرویس", "all")])
        rows.append([{"text": "🔙 سرویس‌ها", "callback_data": "purchase_subscription"}])
        await self.update_user_state(user, BotState.StateType.PURCHASE_FLOW, {
            "step": "plan_selection" if stage in {"results", "volume_list", "time_list", "unlimited_users", "unlimited_results"} else "plan_filters",
            "service_category": picker["category"], "special_offers": False,
            "plan_picker": picker,
        })
        await self.edit_message_with_keyboard(callback.message.chat.id, callback.message.message_id,
                                             "\n".join(lines), self.create_keyboard(rows))
        await callback.answer()

    async def handle_plan_filter(self, callback):
        user, _ = await self.get_or_create_user(callback.from_user)
        state = await self.get_user_state(user)
        picker = (state.state_data or {}).get("plan_picker")
        parts = (callback.data or "").split(":")
        if (len(parts) != 4 or not picker or picker.get("version") != 3 or
            parts[1] != picker.get("token") or picker.get("category") not in self.CATEGORY_LABELS or
            state.current_state != BotState.StateType.PURCHASE_FLOW or
            state.state_data.get("step") not in {"plan_filters", "plan_selection", "plan_details"}):
            await callback.answer("این منو قدیمی است؛ دوباره سرویس را انتخاب کنید.", show_alert=True)
            return
        _, _, action, value = parts
        catalog = await self._picker_catalog(picker)
        volume = [p for p in catalog if self._volume(p)
                  and Decimal(traffic_key(p)[1:]) >= self._min_volume(picker["category"], duration_key(p))]
        stage = picker["stage"]
        if action == "duration" and stage == "bundle" and blocked_duration_key(value):
            await callback.answer("زمان درخواستی موقتاً غیرفعال است", show_alert=True)
            return
        if action == "duration" and stage == "bundle":
            allowed = {duration_key(p) for p in volume} & self.STANDARD_MONTHS
            if value in allowed:
                picker["duration"] = value
                if not any(duration_key(p) == value and p.max_users == picker["users"] for p in volume):
                    picker["users"] = 0
                if picker["users"] and self._filtered(volume, picker):
                    picker.update(stage="ranges", range="", page=1)
        elif action == "users" and stage == "bundle" and value.isdecimal():
            available = [p for p in volume if not picker["duration"] or duration_key(p) == picker["duration"]]
            if int(value) in {p.max_users for p in available if 1 <= p.max_users <= 4}:
                picker["users"] = int(value)
                if picker["duration"] and self._filtered(volume, picker):
                    picker.update(stage="ranges", range="", page=1)
        elif action == "next" and stage == "bundle":
            if picker["duration"] and picker["users"] and self._filtered(volume, picker):
                picker.update(stage="ranges", range="", page=1)
        elif action == "range" and stage == "ranges":
            if value in {key for key, _ in self._volume_ranges(picker, self._filtered(volume, picker))}:
                picker.update(stage="results", range=value, page=1)
        elif action == "all":
            picker.update(stage="all_type", page=1)
        elif action == "type" and stage == "all_type":
            if value == "volume" and volume:
                picker.update(stage="volume_list", page=1)
            elif value == "time" and any(traffic_key(p) == "n" for p in catalog):
                picker.update(stage="time_list", page=1)
            elif value == "unlimited" and any(traffic_key(p) == "u" for p in catalog):
                picker.update(stage="unlimited_users", page=1)
        elif action == "allusers" and stage == "unlimited_users" and value.isdecimal():
            if int(value) in {p.max_users for p in catalog if traffic_key(p) == "u" and 1 < p.max_users <= 4}:
                picker.update(stage="unlimited_results", users=int(value), page=1)
        elif action == "page" and stage in {"results", "volume_list", "time_list", "unlimited_results"} and value.isdecimal():
            picker["page"] = min(int(value), 100000)
        elif action == "back":
            picker["stage"] = {"ranges": "bundle", "results": "ranges",
                               "all_type": "bundle", "volume_list": "all_type", "time_list": "all_type",
                               "unlimited_users": "all_type", "unlimited_results": "unlimited_users"}.get(stage, "bundle")
            picker["page"] = 1
        elif action == "stage" and value == stage:
            pass  # Return to the saved picker from plan details.
        await self.render_plan_picker(callback, user, picker)
