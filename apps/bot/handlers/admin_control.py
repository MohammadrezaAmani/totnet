"""Provider-independent, brand-scoped bot administration and lifecycle configuration."""

import csv
import io
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram.types import BufferedInputFile
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.analytics import gamification_report, referral_link_metrics
from apps.bot.models import BehaviorEvent, BotState
from apps.brands.models import Brand, BrandConfiguration
from apps.orders.models import Order, Wallet, WalletTransaction
from apps.orders.services import (
    WalletOperationError,
    cancel_unpaid_order,
    credit_wallet,
)
from apps.referrals.models import (
    ChallengeProgram,
    ChallengeTier,
    NotificationRule,
    NotificationTrigger,
    ReferralLink,
    UserChallenge,
)
from apps.referrals.scheduling import audience_users
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.support.models import SupportTicket


def button(text, action):
    return {"text": text, "callback_data": "ac:" + action}


@transaction.atomic
def change_order_safely(brand_id, identifier, status):
    order = (
        Order.objects.select_for_update()
        .filter(brand_id=brand_id)
        .filter(
            Q(order_number=identifier)
            | Q(pk=int(identifier) if str(identifier).isdigit() else -1)
        )
        .first()
    )
    if not order:
        raise ValidationError("سفارش پیدا نشد.")
    paid = order.payments.filter(status="confirmed").aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal(0)
    if status == "cancelled":
        cancel_unpaid_order(order_id=order.pk, user_id=order.user_id, brand_id=brand_id)
        return order
    if status not in {"paid", "processing", "completed", "failed"}:
        raise ValidationError("تغییر این وضعیت به عملیات مالی جداگانه نیاز دارد.")
    if order.status in {"cancelled", "refunded", "completed"}:
        raise ValidationError("سفارش نهایی‌شده قابل تغییر نیست.")
    if status in {"paid", "processing", "completed"}:
        if order.final_price > paid:
            raise ValidationError(
                "پرداخت کامل سفارش هنوز تأیید نشده است. ابتدا رسید را بررسی کنید."
            )
        if (
            status == "completed"
            and order.order_type in {"new_subscription", "renewal", "upgrade", "gift"}
            and not Subscription.objects.filter(order=order).exists()
        ):
            raise ValidationError(
                "اشتراک هنوز ساخته نشده است؛ وضعیت «پرداخت‌شده» را برای پردازش انتخاب کنید."
            )
    elif paid:
        raise ValidationError(
            "سفارش دارای پرداخت تأییدشده است؛ ابتدا بازپرداخت را بررسی کنید."
        )
    order.status = status
    order.save(update_fields=["status", "updated_at"])
    if status == "paid":
        from apps.subscriptions.tasks import provision_paid_order

        transaction.on_commit(lambda: provision_paid_order.delay(order.pk))
    return order


class AdminControlMixin:
    async def control_screen(self, callback, text, rows, back="admin"):
        rows = list(rows) + [[{"text": "🔙 بازگشت", "callback_data": back}]]
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            self.create_keyboard(rows),
        )
        await callback.answer()

    async def control_prompt(self, callback, user, action, text, **data):
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        await self.update_user_state(
            user,
            BotState.StateType.ADMIN_ACTION,
            {"action": "control", "control_action": action, **data},
        )
        await self.control_screen(callback, text, [], back=data.get("back", "admin"))

    async def handle_control_callback(self, callback, user):
        data = callback.data or ""
        if not data.startswith("ac:"):
            return False
        if not await self.has_admin_access(user):
            await callback.answer("❌ دسترسی ندارید", show_alert=True)
            return True
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        parts = data[3:].split(":")
        section = parts[0]
        try:
            if section == "tickets":
                await self.control_tickets(
                    callback, parts[1], int(parts[2]) if len(parts) > 2 else 1
                )
            elif section == "links":
                await self.control_links(
                    callback, int(parts[1]) if len(parts) > 1 else 1
                )
            elif section == "link":
                await self.control_link(callback, int(parts[1]))
            elif section == "linknew":
                await self.control_prompt(
                    callback,
                    user,
                    "linknew",
                    "نام این لینک معرفی را وارد کنید (مثلاً کمپین اینستاگرام):",
                    back="ac:links",
                )
            elif section == "linktoggle":
                link = await ReferralLink.objects.aget(
                    pk=int(parts[1]), brand=self.brand
                )
                link.is_active = not link.is_active
                await link.asave(update_fields=["is_active", "updated_at"])
                await self.control_link(callback, link.pk)
            elif section == "linkedit":
                await ReferralLink.objects.aget(pk=int(parts[1]), brand=self.brand)
                await self.control_prompt(
                    callback,
                    user,
                    "linkedit",
                    "نام جدید لینک را وارد کنید:",
                    object_id=int(parts[1]),
                    back=f"ac:link:{parts[1]}",
                )
            elif section == "plans":
                await self.control_plans(
                    callback, int(parts[1]) if len(parts) > 1 else 1
                )
            elif section == "plan":
                await self.control_plan(callback, int(parts[1]))
            elif section == "plantoggle":
                plan = await SubscriptionPlan.objects.aget(
                    pk=int(parts[1]), brand=self.brand
                )
                field = parts[2]
                if field not in {
                    "is_visible",
                    "is_featured",
                    "sync_price",
                    "sync_bot_settings",
                }:
                    raise ValidationError("گزینه نامعتبر است.")
                setattr(plan, field, not getattr(plan, field))
                if field == "is_visible":
                    plan.sync_bot_settings = False
                await plan.asave()
                await self.control_plan(callback, plan.pk)
            elif section == "planedit":
                await SubscriptionPlan.objects.aget(pk=int(parts[1]), brand=self.brand)
                if parts[2] not in {
                    "name",
                    "price",
                    "display_order",
                    "discount_percentage",
                }:
                    raise ValidationError("فیلد نامعتبر است.")
                await self.control_prompt(
                    callback,
                    user,
                    "planedit",
                    "مقدار جدید را وارد کنید:",
                    object_id=int(parts[1]),
                    field=parts[2],
                    back=f"ac:plan:{parts[1]}",
                )
            elif section == "free":
                brand = await Brand.objects.aget(pk=self.brand.pk)
                brand.show_free_plans = not brand.show_free_plans
                await brand.asave(update_fields=["show_free_plans", "updated_at"])
                await self.control_plans(callback)
            elif section == "plansync":
                from apps.vpn_providers.models import VPNProvider
                from apps.vpn_providers.tasks import sync_connectix_plans

                async for provider in VPNProvider.objects.filter(
                    brand=self.brand, provider_type="connectix", status="active"
                ):
                    await sync_to_async(sync_connectix_plans.delay)(provider.pk)
                await self.control_screen(
                    callback,
                    "همگام‌سازی پلن‌ها در صف قرار گرفت؛ نام، قیمت و ترتیب دستی حفظ می‌شوند.",
                    [],
                    "ac:plans",
                )
            elif section == "challenge":
                await self.control_challenge(callback)
            elif section == "chtoggle":
                program = await ChallengeProgram.objects.aget(brand=self.brand)
                if parts[1] not in {
                    "is_active",
                    "require_referral_join_during_challenge",
                }:
                    raise ValidationError("گزینه نامعتبر است.")
                setattr(program, parts[1], not getattr(program, parts[1]))
                await program.asave()
                await self.control_challenge(callback)
            elif section == "chtrigger":
                if len(parts) == 1:
                    await self.control_screen(
                        callback,
                        "مبدأ زمان ارسال پیشنهاد چالش:",
                        [
                            [button(label, f"chtrigger:{key}")]
                            for key, label in NotificationTrigger.choices
                        ],
                        "ac:challenge",
                    )
                else:
                    if parts[1] not in NotificationTrigger.values:
                        raise ValidationError("مبدأ نامعتبر است.")
                    await ChallengeProgram.objects.filter(brand=self.brand).aupdate(
                        offer_trigger=parts[1]
                    )
                    await self.control_challenge(callback)
            elif section == "chedit":
                if parts[1] not in {
                    "name",
                    "offer_delay_minutes",
                    "duration_days",
                    "reward_percent",
                    "intro_text",
                }:
                    raise ValidationError("گزینه نامعتبر است.")
                await self.control_prompt(
                    callback,
                    user,
                    "chedit",
                    "مقدار جدید را وارد کنید (تأخیر به دقیقه، مدت چالش به روز):",
                    field=parts[1],
                    back="ac:challenge",
                )
            elif section == "tiernew":
                await self.control_prompt(
                    callback,
                    user,
                    "tiernew",
                    "تعداد معرفی موفق، مبلغ ورودی و ترتیب نمایش را با فاصله بفرستید؛ مثال: 3 150000 2",
                    back="ac:challenge",
                )
            elif section == "tier":
                tier = await ChallengeTier.objects.select_related("program").aget(
                    pk=int(parts[1]), program__brand=self.brand
                )
                await self.control_screen(
                    callback,
                    f"تارگت: {tier.target_referrals}\nورودی: {tier.entry_fee:g}\nترتیب: {tier.display_order}\nفعال: {tier.is_active}",
                    [
                        [button("ویرایش", f"tieredit:{tier.pk}")],
                        [button("تغییر وضعیت", f"tiertoggle:{tier.pk}")],
                    ],
                    "ac:challenge",
                )
            elif section == "tieredit":
                await self.control_prompt(
                    callback,
                    user,
                    "tieredit",
                    "تعداد معرفی، مبلغ ورودی و ترتیب نمایش را با فاصله بفرستید:",
                    object_id=int(parts[1]),
                    back="ac:challenge",
                )
            elif section == "tiertoggle":
                tier = await ChallengeTier.objects.aget(
                    pk=int(parts[1]), program__brand=self.brand
                )
                tier.is_active = not tier.is_active
                await tier.asave()
                await self.control_challenge(callback)
            elif section == "rules":
                await self.control_rules(
                    callback, int(parts[1]) if len(parts) > 1 else 1
                )
            elif section == "rule":
                await self.control_rule(callback, int(parts[1]))
            elif section == "rulenew":
                await self.control_prompt(
                    callback,
                    user,
                    "rulenew",
                    "نام نوتیف جدید را وارد کنید:",
                    back="ac:rules",
                )
            elif section == "ruletoggle":
                rule = await NotificationRule.objects.aget(
                    pk=int(parts[1]), brand=self.brand
                )
                rule.is_active = not rule.is_active
                await rule.asave(update_fields=["is_active", "updated_at"])
                await self.control_rule(callback, rule.pk)
            elif section == "ruleedit":
                rule = await NotificationRule.objects.aget(
                    pk=int(parts[1]), brand=self.brand
                )
                field = parts[2]
                choices = {
                    "trigger": NotificationTrigger.choices,
                    "kind": NotificationRule.Kind.choices,
                    "audience": NotificationRule.Audience.choices,
                    "button_action": NotificationRule._meta.get_field(
                        "button_action"
                    ).choices,
                }.get(field)
                if choices:
                    await self.control_screen(
                        callback,
                        "گزینه را انتخاب کنید:",
                        [
                            [button(label, f"ruleset:{rule.pk}:{field}:{key}")]
                            for key, label in choices
                        ],
                        f"ac:rule:{rule.pk}",
                    )
                elif field in {"name", "delay_minutes", "message", "button_text"}:
                    await self.control_prompt(
                        callback,
                        user,
                        "ruleedit",
                        "مقدار جدید را وارد کنید (برای پاک کردن دکمه - بفرستید):",
                        object_id=rule.pk,
                        field=field,
                        back=f"ac:rule:{rule.pk}",
                    )
                else:
                    raise ValidationError("فیلد نامعتبر است.")
            elif section == "ruleset":
                rule = await NotificationRule.objects.aget(
                    pk=int(parts[1]), brand=self.brand
                )
                field, value = parts[2:4]
                if field not in {"trigger", "kind", "audience", "button_action"}:
                    raise ValidationError("فیلد نامعتبر است.")
                setattr(rule, field, value)
                if field == "button_action" and not rule.button_text:
                    rule.button_text = "مشاهده"
                await sync_to_async(rule.full_clean)()
                await rule.asave()
                await self.control_rule(callback, rule.pk)
            elif section == "rulepreview":
                rule = await NotificationRule.objects.aget(
                    pk=int(parts[1]), brand=self.brand
                )
                count = await sync_to_async(
                    lambda: audience_users(self.brand.pk, rule.audience).count()
                )()
                await self.control_screen(
                    callback,
                    f"پیش‌نمایش: {escape(rule.name)}\n\n{escape(rule.message)}\n\nگروه هدف فعلی: {count} نفر\nمبدأ: {rule.get_trigger_display()}، تأخیر: {rule.delay_minutes} دقیقه\nپس از رسیدن به این زمان، هر رخداد فقط یک بار ارسال می‌شود.",
                    [],
                    f"ac:rule:{rule.pk}",
                )
            elif section == "report":
                await self.control_report(callback)
            elif section == "export":
                await self.control_export(callback)
            elif section == "users":
                await self.control_users(
                    callback, int(parts[1]) if len(parts) > 1 else 1
                )
            elif section == "user":
                await self.control_user(callback, int(parts[1]))
            elif section == "usersearch":
                await self.control_prompt(
                    callback,
                    user,
                    "usersearch",
                    "نام کاربری یا شناسه تلگرام را وارد کنید:",
                    back="ac:users",
                )
            elif section == "usertoggle":
                target = await User.objects.aget(pk=int(parts[1]), brand=self.brand)
                if (
                    target.is_superuser
                    or target.is_staff
                    or target.user_type != "customer"
                ):
                    raise ValidationError("حساب مدیریتی از این بخش مسدود نمی‌شود.")
                target.is_active = not target.is_active
                await target.asave(update_fields=["is_active", "updated_at"])
                await self.control_user(callback, target.pk)
            elif section == "usercredit":
                await User.objects.aget(pk=int(parts[1]), brand=self.brand)
                await self.control_prompt(
                    callback,
                    user,
                    "usercredit",
                    "مبلغ اعتبار را در واحد پول برند وارد کنید:",
                    object_id=int(parts[1]),
                    request_key=callback.id,
                    back=f"ac:user:{parts[1]}",
                )
            else:
                await callback.answer(
                    "این گزینه دیگر معتبر نیست؛ منوی مدیریت را باز کنید.",
                    show_alert=True,
                )
        except (
            ValidationError,
            ValueError,
            InvalidOperation,
            WalletOperationError,
        ) as exc:
            await callback.answer(
                "؛ ".join(exc.messages)[:190]
                if isinstance(exc, ValidationError)
                else "داده نامعتبر است؛ عملیات ذخیره نشد.",
                show_alert=True,
            )
        except (
            ReferralLink.DoesNotExist,
            SubscriptionPlan.DoesNotExist,
            NotificationRule.DoesNotExist,
            ChallengeProgram.DoesNotExist,
            ChallengeTier.DoesNotExist,
            User.DoesNotExist,
        ):
            await callback.answer("مورد انتخاب‌شده پیدا نشد.", show_alert=True)
        return True

    async def control_links(self, callback, page=1):
        qs = ReferralLink.objects.filter(brand=self.brand).order_by("-pk")
        count = await qs.acount()
        page = max(1, min(page, max(1, (count + 7) // 8)))
        rows = [
            [
                button(
                    f"{'🟢' if link.is_active else '🔴'} {link.campaign_name or link.code}",
                    f"link:{link.pk}",
                )
            ]
            async for link in qs[(page - 1) * 8 : page * 8]
        ]
        rows += [[button("➕ لینک جدید", "linknew")]]
        if page > 1:
            rows += [[button("قبلی", f"links:{page - 1}")]]
        if page * 8 < count:
            rows += [[button("بعدی", f"links:{page + 1}")]]
        await self.control_screen(
            callback,
            "🔗 لینک‌های معرفی نام‌گذاری‌شده\nلینک بدون صاحب برای آمار کمپین است؛ لینک دارای صاحب پاداش معرفی هم ثبت می‌کند.",
            rows,
        )

    async def control_link(self, callback, pk):
        link = await ReferralLink.objects.select_related("user").aget(
            pk=pk, brand=self.brand
        )
        metrics = await sync_to_async(referral_link_metrics)(link)
        username = self.brand.bot_username or (await self.bot.get_me()).username
        url = f"https://t.me/{username.lstrip('@')}?start={link.code}"
        text = f"🔗 {escape(link.campaign_name or link.code)}\n<code>{escape(url)}</code>\nمنبع: {escape(link.source or '—')}\nصاحب پاداش: {escape(link.user.username if link.user else 'بدون صاحب')}\nورود یکتا: {metrics['visits']}\nعضویت جدید: {metrics['registrations']}\nمعرفی منتسب: {metrics['referrals']}\nخریدار: {metrics['buyers']}\nخرید موفق: {metrics['purchases']}\n"
        text += "\n".join(
            f"ارزش خرید: {self.format_price(r['total'], r['currency'])}"
            for r in metrics["revenue"]
        )
        await self.control_screen(
            callback,
            text,
            [
                [button("ویرایش نام", f"linkedit:{pk}")],
                [button("فعال / غیرفعال", f"linktoggle:{pk}")],
            ],
            "ac:links",
        )

    async def control_plans(self, callback, page=1):
        qs = SubscriptionPlan.objects.filter(brand=self.brand).order_by(
            "-is_active", "display_order", "price", "pk"
        )
        count = await qs.acount()
        page = max(1, min(page, max(1, (count + 7) // 8)))
        rows = [
            [
                button(
                    f"{'🟢' if plan.is_active and plan.is_visible else '🔴'} {plan.name[:45]}",
                    f"plan:{plan.pk}",
                )
            ]
            async for plan in qs[(page - 1) * 8 : page * 8]
        ]
        brand = await Brand.objects.aget(pk=self.brand.pk)
        rows += [
            [
                button(
                    f"پلن رایگان: {'نمایش' if brand.show_free_plans else 'مخفی'}",
                    "free",
                )
            ],
            [button("همگام‌سازی پنل", "plansync")],
        ]
        if page > 1:
            rows += [[button("قبلی", f"plans:{page - 1}")]]
        if page * 8 < count:
            rows += [[button("بعدی", f"plans:{page + 1}")]]
        await self.control_screen(
            callback,
            f"📋 مدیریت تمام پلن‌ها — {count} پلن\nنام، قیمت و ترتیب دستی با همگام‌سازی تغییر نمی‌کنند.\nپلن غیرفعال پنل، حتی با نمایش روشن، قابل فروش نیست.",
            rows,
        )

    async def control_plan(self, callback, pk):
        p = await SubscriptionPlan.objects.aget(pk=pk, brand=self.brand)
        from apps.subscriptions.presentation import compact_plan_specs

        text = f"{escape(p.name)}\n{compact_plan_specs(p)}\nنوع: {p.get_plan_type_display()}\nسرویس: {p.get_service_category_display()}\nقیمت: {self.format_price(p.price, p.currency)}\nتخفیف: {p.discount_percentage:g}٪\nترتیب: {p.display_order}\nفعال در پنل: {p.is_active}\nنمایش: {p.is_visible}\nویژه: {p.is_featured}\nدریافت قیمت پنل: {p.sync_price}\nدریافت نام/ترتیب/نمایش پنل: {p.sync_bot_settings}"
        rows = [
            [button(label, f"planedit:{pk}:{field}")]
            for field, label in [
                ("name", "نام"),
                ("price", "قیمت"),
                ("display_order", "ترتیب"),
                ("discount_percentage", "تخفیف"),
            ]
        ]
        rows += [
            [button(label, f"plantoggle:{pk}:{field}")]
            for field, label in [
                ("is_visible", "نمایش / مخفی"),
                ("is_featured", "ویژه / عادی"),
                ("sync_price", "دریافت قیمت از پنل"),
                ("sync_bot_settings", "دریافت تنظیمات از پنل"),
            ]
        ]
        await self.control_screen(callback, text, rows, "ac:plans")

    async def control_challenge(self, callback):
        p, _ = await ChallengeProgram.objects.aget_or_create(
            brand=self.brand, defaults={"is_active": False}
        )
        text = f"🏆 {escape(p.name)}\nفعال: {p.is_active}\nمبدأ پیشنهاد: {p.get_offer_trigger_display()}\nتأخیر پیشنهاد: {p.offer_delay_minutes} دقیقه\nمدت اجرای چالش: {p.duration_days} روز\nدرصد: {p.reward_percent:g}٪\nورود معرفی در بازه چالش: {p.require_referral_join_during_challenge}\n\nتنظیمات جدید روی چالش‌های شروع‌شده اثر نمی‌گذارد. اصل وجه ورودی در پایان آزاد می‌شود."
        rows = [
            [button(label, f"chedit:{field}")]
            for field, label in [
                ("name", "نام"),
                ("offer_delay_minutes", "تأخیر ارسال (دقیقه)"),
                ("duration_days", "مدت چالش (روز)"),
                ("reward_percent", "درصد پاداش"),
                ("intro_text", "متن شرایط"),
            ]
        ]
        rows += [
            [button("مبدأ ارسال", "chtrigger")],
            [button("فعال / غیرفعال", "chtoggle:is_active")],
            [
                button(
                    "شرط زمان عضویت معرف‌شده",
                    "chtoggle:require_referral_join_during_challenge",
                )
            ],
        ]
        rows += [
            [
                button(
                    f"🎯 {tier.target_referrals} نفر — {tier.entry_fee:g}",
                    f"tier:{tier.pk}",
                )
            ]
            async for tier in ChallengeTier.objects.filter(program=p)
        ]
        rows += [
            [button("➕ تارگت جدید", "tiernew")],
            [button("📊 آمار چالش و رفتار", "report")],
        ]
        await self.control_screen(callback, text, rows)

    async def control_rules(self, callback, page=1):
        qs = NotificationRule.objects.filter(brand=self.brand).order_by("pk")
        count = await qs.acount()
        page = max(1, min(page, max(1, (count + 7) // 8)))
        rows = [
            [button(f"{'🟢' if r.is_active else '🔴'} {r.name}", f"rule:{r.pk}")]
            async for r in qs[(page - 1) * 8 : page * 8]
        ]
        rows += [[button("➕ ساخت نوتیف", "rulenew")]]
        if page > 1:
            rows += [[button("قبلی", f"rules:{page - 1}")]]
        if page * 8 < count:
            rows += [[button("بعدی", f"rules:{page + 1}")]]
        await self.control_screen(
            callback,
            "🔔 زمان‌بندی نوتیف‌ها\nبر اساس عمر حضور کاربر در ربات یا خرید کاملاً پرداخت‌شده.\nنوتیف جدید ابتدا غیرفعال ساخته می‌شود؛ تنظیمات و پیش‌نمایش را بررسی کنید سپس فعال کنید.",
            rows,
        )

    async def control_rule(self, callback, pk):
        r = await NotificationRule.objects.aget(pk=pk, brand=self.brand)
        sent = await r.deliveries.filter(sent_at__isnull=False).acount()
        total = await r.deliveries.acount()
        text = f"🔔 {escape(r.name)}\nفعال: {r.is_active}\nنوع: {r.get_kind_display()}\nمبدأ: {r.get_trigger_display()}\nتأخیر: {r.delay_minutes} دقیقه\nمخاطبان: {r.get_audience_display()}\nتحویل: {sent}/{total}\n\n{escape(r.message[:1800])}"
        rows = [
            [button(label, f"ruleedit:{pk}:{field}")]
            for field, label in [
                ("name", "نام"),
                ("kind", "نوع"),
                ("trigger", "مبدأ"),
                ("delay_minutes", "تأخیر به دقیقه"),
                ("audience", "مخاطبان"),
                ("message", "متن"),
                ("button_action", "مقصد دکمه"),
                ("button_text", "عنوان دکمه"),
            ]
        ]
        rows += [
            [button("پیش‌نمایش و تعداد مخاطب", f"rulepreview:{pk}")],
            [button("فعال / غیرفعال", f"ruletoggle:{pk}")],
        ]
        await self.control_screen(callback, text, rows, "ac:rules")

    async def control_report(self, callback):
        r = await sync_to_async(gamification_report)(self.brand.pk)
        statuses = r["statuses"]
        text = "📊 عملکرد گیمیفیکیشن\n" + "\n".join(
            f"{label}: {statuses.get(key, 0)}"
            for key, label in UserChallenge.Status.choices
        )
        rate = (
            f"{r['success_rate']}٪"
            if r["success_rate"] is not None
            else "هنوز چالش تمام‌شده‌ای نیست"
        )
        text += f"\nشرکت‌کننده پرداخت‌شده: {r['participants']}\nنرخ موفقیت چالش‌های پایان‌یافته: {rate}\nوجه فیریز: {self.format_price(r['frozen'], self.brand.currency)}\nپاداش چالش: {self.format_price(r['challenge_rewards'], self.brand.currency)}\n\nانتخاب تارگت و عملکرد:\n"
        text += "\n".join(
            f"{t['target_referrals']} نفر: انتخاب {t['selected']}، موفق {t['successes']}، ناموفق {t['failures']}، میانگین معرفی {t['average_progress'] or 0:.1f}"
            for t in r["tiers"]
        )
        s = r["surveys"]
        text += f"\n\nنظرسنجی تکمیل‌شده: {s['completed']}/{s['total']}\nکیفیت: {s['quality'] or 0:.1f}، برنامه: {s['app'] or 0:.1f}، پشتیبانی: {s['support'] or 0:.1f}\n\nانتخاب‌های کاربران:\n"
        text += "\n".join(
            f"{escape(c['event'])} / {escape(c['value'])}: {c['count']} بار، {c['users']} نفر"
            for c in r["choices"][:12]
        )
        text += "\n\nتحویل نوتیف‌ها:\n" + "\n".join(
            f"{n['notification_type']} (#{n['rule_id'] or '—'}): {n['sent']}/{n['queued']}"
            for n in r["notifications"][:12]
        )
        await self.control_screen(
            callback,
            text[:3800],
            [[button("خروجی CSV", "export")], [button("بروزرسانی", "report")]],
        )

    async def control_export(self, callback):
        def export():
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(
                ["time", "user_id", "event", "value", "outcome", "metadata"]
            )
            import json

            for e in BehaviorEvent.objects.filter(brand=self.brand).order_by(
                "-created_at"
            )[:10000]:
                writer.writerow(
                    [
                        e.created_at.isoformat(),
                        e.user_id,
                        e.event,
                        e.value,
                        e.outcome,
                        json.dumps(e.metadata, ensure_ascii=False),
                    ]
                )
            reports = io.StringIO()
            w = csv.writer(reports)
            w.writerow(
                [
                    "user_id",
                    "challenge_id",
                    "status",
                    "target",
                    "progress",
                    "entry",
                    "reward_percent",
                    "reward",
                    "started",
                    "ended",
                ]
            )
            for c in UserChallenge.objects.filter(brand=self.brand).order_by("pk"):
                w.writerow(
                    [
                        c.user_id,
                        c.pk,
                        c.status,
                        c.target_referrals,
                        c.successful_referrals,
                        c.entry_amount,
                        c.reward_percent,
                        c.reward_amount,
                        c.starts_at,
                        c.ends_at,
                    ]
                )
            return out.getvalue().encode("utf-8-sig"), reports.getvalue().encode(
                "utf-8-sig"
            )

        events, challenges = await sync_to_async(export)()
        await self.bot.send_document(
            callback.message.chat.id,
            BufferedInputFile(events, filename="behavior-events.csv"),
            caption="آخرین ۱۰٬۰۰۰ رخداد رفتار",
        )
        await self.bot.send_document(
            callback.message.chat.id,
            BufferedInputFile(challenges, filename="challenge-performance.csv"),
            caption="عملکرد چالش‌ها",
        )
        await callback.answer()

    async def control_users(self, callback, page=1):
        qs = User.objects.filter(brand=self.brand).order_by("-pk")
        count = await qs.acount()
        page = max(1, min(page, max(1, (count + 7) // 8)))
        rows = [
            [
                button(
                    f"{'🟢' if u.is_active else '🔴'} {u.username[:40]}", f"user:{u.pk}"
                )
            ]
            async for u in qs[(page - 1) * 8 : page * 8]
        ]
        rows += [[button("🔍 جستجوی کاربر", "usersearch")]]
        if page > 1:
            rows += [[button("قبلی", f"users:{page - 1}")]]
        if page * 8 < count:
            rows += [[button("بعدی", f"users:{page + 1}")]]
        await self.control_screen(callback, f"👥 کاربران ربات: {count}", rows)

    async def control_user(self, callback, pk):
        u = await User.objects.aget(pk=pk, brand=self.brand)
        wallet = await Wallet.objects.filter(
            user=u, brand=self.brand, currency=self.brand.currency
        ).afirst()
        subs = (
            await Subscription.objects.filter(
                brand=self.brand, owner=u, status="active"
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .acount()
        )
        await self.control_screen(
            callback,
            f"👤 {escape(u.username)}\nتلگرام: {u.telegram_id}\nفعال: {u.is_active}\nاشتراک فعال: {subs}\nموجودی: {self.format_price(wallet.balance if wallet else 0, self.brand.currency)}\nفیریز چالش: {self.format_price(wallet.challenge_frozen_balance if wallet else 0, self.brand.currency)}",
            [
                [button("فعال / مسدود", f"usertoggle:{pk}")],
                [button("افزودن اعتبار با ثبت دفتر مالی", f"usercredit:{pk}")],
            ],
            "ac:users",
        )

    async def handle_control_message(self, message, user, state):
        if not await self.has_admin_access(user):
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
            await message.reply("❌ دسترسی ندارید")
            return
        d = state.state_data
        action = d.get("control_action")
        value = (message.text or "").strip()
        try:
            if action == "linknew":
                if not value or len(value) > 100:
                    raise ValidationError("نام باید حداکثر ۱۰۰ کاراکتر باشد.")
                d["link_name"] = value
                d["control_action"] = "linkowner"
                await state.asave()
                await message.reply(
                    "نام کاربری صاحب پاداش را بفرستید؛ برای لینک آماری بدون صاحب، - بفرستید."
                )
                return
            if action == "linkowner":
                owner = None
                if value != "-":
                    owner = await User.objects.filter(
                        brand=self.brand, username__iexact=value.lstrip("@")
                    ).afirst()
                    if not owner:
                        raise ValidationError("صاحب لینک باید عضو همین برند باشد.")
                link = ReferralLink(
                    brand=self.brand,
                    user=owner,
                    campaign_name=d["link_name"],
                    source=d["link_name"],
                )
                await sync_to_async(link.full_clean)()
                await link.asave()
                back = f"ac:link:{link.pk}"
            elif action == "linkedit":
                link = await ReferralLink.objects.aget(
                    pk=d["object_id"], brand=self.brand
                )
                link.campaign_name = value
                await sync_to_async(link.full_clean)()
                await link.asave()
                back = f"ac:link:{link.pk}"
            elif action == "rulenew":
                if not value or len(value) > 100:
                    raise ValidationError("نام باید حداکثر ۱۰۰ کاراکتر باشد.")
                d["rule_name"] = value
                d["control_action"] = "ruletext"
                await state.asave()
                await message.reply("متن نوتیف را بفرستید (حداکثر ۳۵۰۰ کاراکتر):")
                return
            elif action == "ruletext":
                rule = NotificationRule(
                    brand=self.brand, name=d["rule_name"], message=value
                )
                await sync_to_async(rule.full_clean)()
                await rule.asave()
                back = f"ac:rule:{rule.pk}"
            elif action == "ruleedit":
                rule = await NotificationRule.objects.aget(
                    pk=d["object_id"], brand=self.brand
                )
                field = d["field"]
                if field not in {"name", "message", "delay_minutes", "button_text"}:
                    raise ValidationError("فیلد نامعتبر است.")
                setattr(
                    rule,
                    field,
                    int(value)
                    if field == "delay_minutes"
                    else ("" if field == "button_text" and value == "-" else value),
                )
                if field == "button_text" and not rule.button_text:
                    rule.button_action = ""
                await sync_to_async(rule.full_clean)()
                await rule.asave()
                back = f"ac:rule:{rule.pk}"
            elif action == "planedit":
                plan = await SubscriptionPlan.objects.aget(
                    pk=d["object_id"], brand=self.brand
                )
                field = d["field"]
                if field not in {
                    "name",
                    "price",
                    "display_order",
                    "discount_percentage",
                }:
                    raise ValidationError("فیلد نامعتبر است.")
                setattr(
                    plan,
                    field,
                    value
                    if field == "name"
                    else int(value)
                    if field == "display_order"
                    else Decimal(value),
                )
                if field == "price":
                    plan.sync_price = False
                if field in {"name", "display_order"}:
                    plan.sync_bot_settings = False
                await sync_to_async(plan.full_clean)()
                await plan.asave()
                back = f"ac:plan:{plan.pk}"
            elif action == "chedit":
                program = await ChallengeProgram.objects.aget(brand=self.brand)
                field = d["field"]
                if field not in {
                    "name",
                    "intro_text",
                    "offer_delay_minutes",
                    "duration_days",
                    "reward_percent",
                }:
                    raise ValidationError("فیلد نامعتبر است.")
                setattr(
                    program,
                    field,
                    value
                    if field in {"name", "intro_text"}
                    else Decimal(value)
                    if field == "reward_percent"
                    else int(value),
                )
                await sync_to_async(program.full_clean)()
                await program.asave()
                back = "ac:challenge"
            elif action in {"tiernew", "tieredit"}:
                target, fee, order = value.split()
                program = await ChallengeProgram.objects.aget(brand=self.brand)
                tier = (
                    await ChallengeTier.objects.aget(pk=d["object_id"], program=program)
                    if action == "tieredit"
                    else ChallengeTier(program=program)
                )
                tier.target_referrals = int(target)
                tier.entry_fee = Decimal(fee)
                tier.display_order = int(order)
                await sync_to_async(tier.full_clean)()
                await tier.asave()
                back = "ac:challenge"
            elif action == "usersearch":
                target = (
                    await User.objects.filter(brand=self.brand)
                    .filter(
                        Q(username__iexact=value.lstrip("@"))
                        | Q(telegram_id=int(value) if value.isdigit() else -1)
                    )
                    .afirst()
                )
                if not target:
                    raise ValidationError("کاربر یافت نشد.")
                back = f"ac:user:{target.pk}"
            elif action == "brandtext":
                if d["field"] == "welcome_message":
                    if len(value) > 3500:
                        raise ValidationError("متن بسیار طولانی است.")
                    config, _ = await BrandConfiguration.objects.aget_or_create(
                        brand=self.brand
                    )
                    config.welcome_message = value
                    await sync_to_async(config.full_clean)()
                    await config.asave()
                elif d["field"] == "contact_email":
                    brand = await Brand.objects.aget(pk=self.brand.pk)
                    brand.contact_email = value
                    await sync_to_async(brand.full_clean)()
                    await brand.asave()
                back = "admin_bot_settings"
            elif action == "usercredit":
                amount = Decimal(value)
                if (
                    not amount.is_finite()
                    or amount <= 0
                    or amount.quantize(Decimal(".01")) != amount
                ):
                    raise ValidationError("مبلغ مثبت با حداکثر دو رقم اعشار وارد کنید.")

                def add_credit():
                    with transaction.atomic():
                        target = User.objects.get(pk=d["object_id"], brand=self.brand)
                        wallet, _ = Wallet.objects.get_or_create(
                            user=target, brand=self.brand, currency=self.brand.currency
                        )
                        credit_wallet(
                            wallet_id=wallet.pk,
                            amount=amount,
                            transaction_type=WalletTransaction.TransactionType.ADMIN_ADJUSTMENT,
                            description=f"Admin credit by {user.pk}",
                            idempotency_key=f"admin-credit:{self.brand.pk}:{d['request_key']}",
                            metadata={"admin_id": user.pk},
                        )

                await sync_to_async(add_credit)()
                back = f"ac:user:{d['object_id']}"
            else:
                raise ValidationError("عملیات نامعتبر است.")
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
            await message.reply(
                "✅ ذخیره شد.",
                reply_markup=self.create_keyboard(
                    [
                        [{"text": "مشاهده", "callback_data": back}],
                        [{"text": "مدیریت", "callback_data": "admin"}],
                    ]
                ),
            )
        except (
            ValidationError,
            ValueError,
            InvalidOperation,
            WalletOperationError,
        ) as exc:
            await message.reply(
                "؛ ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "مقدار نامعتبر است؛ دوباره وارد کنید."
            )

    async def control_main_menu(self, callback):
        config, _ = await BrandConfiguration.objects.aget_or_create(brand=self.brand)
        rows = [
            [
                {
                    "text": f"{label}: {'روشن' if getattr(config, field) else 'خاموش'}",
                    "callback_data": f"admin_menu_toggle_{field}",
                }
            ]
            for field, label in [
                ("referral_system_enabled", "معرفی"),
                ("wallet_system_enabled", "کیف پول"),
                ("support_system_enabled", "پشتیبانی"),
            ]
        ]
        await self.control_screen(
            callback, "تنظیمات منوی اصلی", rows, "admin_bot_settings"
        )

    async def toggle_main_menu(self, callback, field):
        if field not in {
            "referral_system_enabled",
            "wallet_system_enabled",
            "support_system_enabled",
        }:
            await callback.answer("گزینه نامعتبر است.")
            return
        config, _ = await BrandConfiguration.objects.aget_or_create(brand=self.brand)
        setattr(config, field, not getattr(config, field))
        await config.asave()
        await self.control_main_menu(callback)

    async def control_tickets(self, callback, status, page=1):
        if status not in SupportTicket.TicketStatus.values:
            await callback.answer("وضعیت نامعتبر است.", show_alert=True)
            return
        qs = (
            SupportTicket.objects.filter(brand=self.brand, status=status)
            .select_related("customer")
            .order_by("-created_at")
        )
        count = await qs.acount()
        page = max(1, min(page, max(1, (count + 7) // 8)))
        rows = [
            [
                {
                    "text": f"#{t.ticket_number} — {t.subject[:45]}",
                    "callback_data": f"admin_view_ticket_{t.pk}",
                }
            ]
            async for t in qs[(page - 1) * 8 : page * 8]
        ]
        if page > 1:
            rows += [[button("قبلی", f"tickets:{status}:{page - 1}")]]
        if page * 8 < count:
            rows += [[button("بعدی", f"tickets:{status}:{page + 1}")]]
        await self.control_screen(
            callback,
            f"🎫 تیکت‌ها — {dict(SupportTicket.TicketStatus.choices)[status]}\nتعداد: {count}\nصفحه: {page}",
            rows,
            "admin_tickets",
        )
