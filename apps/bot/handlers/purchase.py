"""
Purchase Handler for Multi-Tenant VPN Bot
Handles subscription purchases, plan selection, and payment processing
"""

import logging
import re
from html import escape

from aiogram import types
from asgiref.sync import sync_to_async
from django.db.models import Q, Sum
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.brands.models import BrandConfiguration
from apps.orders.models import Order, Payment, PaymentCard
from apps.orders.services import WalletCheckoutError, cancel_unpaid_order, pay_order_with_wallet
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.support.models import UsefulContent
from apps.subscriptions.catalog import get_cached_plans, is_plan_visible_in_bot
from apps.subscriptions.presentation import compact_plan_specs, connectix_plan_name
from apps.subscriptions.policy import TEMPORARILY_UNAVAILABLE_TEXT, temporary_duration_blocked
from apps.subscriptions.tasks import provision_paid_order
from apps.vpn_providers.models import VPNProvider

from utils.jalali import jalali_date

from .base import BaseHandler
from .plan_picker import PlanPickerMixin
from .useful_content import UsefulContentHandler

logger = logging.getLogger(__name__)


def _telegram_profile_link(telegram_id, label) -> str:
    """Link to a Telegram profile from an admin receipt, if the ID is valid."""
    display = escape(str(label))
    user_id = str(telegram_id or "")
    if not user_id.isascii() or not user_id.isdecimal() or int(user_id) <= 0:
        return display
    return f'<a href="tg://user?id={user_id}">{display}</a>'


class PurchaseStep:
    PLAN_SELECTION = "plan_selection"
    PLAN_DETAILS = "plan_details"
    PAYMENT_METHOD = "payment_method"
    CARD_TRANSFER = "card_transfer"
    WAITING_RECEIPT = "waiting_receipt"
    UNDER_REVIEW = "under_review"


class PurchaseHandler(PlanPickerMixin, BaseHandler):
    """Handle subscription purchase flow"""

    CATEGORY_LABELS = {
        SubscriptionPlan.ServiceCategory.NORMAL: "سرویس نرمال",
        SubscriptionPlan.ServiceCategory.ROYAL: "سرویس رویال",
        SubscriptionPlan.ServiceCategory.IRAN_IP: "سرویس آی‌پی ایران",
    }

    async def get_plans(
        self,
        user,
        *,
        category: str | None = None,
        special_only: bool = False,
        back_callback: str = "purchase_subscription",
        page: int = 1,
    ):
        """Build a plan list for one XMind service group or the inline picker."""
        catalog = await get_cached_plans(self.brand.pk)
        if category:
            catalog = [plan for plan in catalog if plan.service_category == category]
        if special_only:
            now = timezone.now()
            catalog = [plan for plan in catalog if plan.is_featured and (
                plan.offer_expires_at is None or plan.offer_expires_at > now
            )]

        total = len(catalog)
        page_size = 10
        last_page = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, last_page))
        start = (page - 1) * page_size
        plans = catalog[start:start + page_size]

        if not plans:
            text = "❌ در حال حاضر پلن فعالی در این بخش موجود نیست."
            return text, self.get_back_keyboard(back_callback)

        if special_only:
            title = "💊 تجویز ویژه دکتر"
        elif category:
            title = f"🛒 {self.CATEGORY_LABELS.get(category, 'پلن‌های اشتراک')}"
        else:
            title = f"🛒 پلن‌های اشتراک {escape(self.brand.name)}"

        text = f"{title}\n\nلطفاً یکی از پلن‌های زیر را انتخاب کنید:"
        if last_page > 1:
            text += f"\nصفحهٔ {page} از {last_page}"
        keyboard_buttons = []
        credits = {
            currency: await self.purchase_credit(user, currency)
            for currency in {p.currency for p in plans}
        }
        text += "\n💰 قیمت‌های فهرست پس از کسر اعتبار نقد قابل‌استفاده شما نمایش داده می‌شوند."
        for plan in plans:
            plan_text = compact_plan_specs(plan)
            plan_text += f" 💰{self.format_price(self.payable_plan_price(plan, credits[plan.currency]), plan.currency)}"
            if plan.discount_percentage > 0 and (
                plan.offer_expires_at is None or plan.offer_expires_at > timezone.now()
            ):
                plan_text += f" • {plan.discount_percentage:g}% تخفیف"

            blocked = temporary_duration_blocked(plan)
            keyboard_buttons.append(
                [{"text": ("🔴 " if blocked else "") + plan_text,
                  "callback_data": f"select_plan_{plan.id}",
                  "style": "danger" if blocked else None}]
            )

        navigation = []
        for target_page, label in ((page - 1, "◀️ قبلی"), (page + 1, "بعدی ▶️")):
            if 1 <= target_page <= last_page:
                navigation.append({
                    "text": label,
                    "callback_data": f"plans_page_{category or 'all'}_{int(special_only)}_{target_page}",
                })
        if navigation:
            keyboard_buttons.append(navigation)
        keyboard_buttons.append(
            [{"text": "🔙 بازگشت", "callback_data": back_callback}]
        )
        return text, self.create_keyboard(keyboard_buttons)

    async def show_plans_page(self, callback, category, special_only, page):
        category = None if category == "all" else category
        if category is not None and category not in self.CATEGORY_LABELS:
            await callback.answer("❌ دستهٔ سرویس نامعتبر است.", show_alert=True)
            return
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(user, BotState.StateType.PURCHASE_FLOW, {
            "step": "plan_selection", "service_category": category, "special_offers": special_only,
            "plan_picker": None,
        })
        text, keyboard = await self.get_plans(user, category=category, special_only=special_only, page=page)
        await self.edit_message_with_keyboard(callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    async def show_subscription_plans(self, callback: types.CallbackQuery):
        """Show the XMind purchase landing page."""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user, BotState.StateType.PURCHASE_FLOW, {
                "step": "service_selection", "plan_picker": None, "special_offers": False,
            }
        )

        text = f"""
🛒 خرید اشتراک {escape(self.brand.name)}

نوع سرویس را انتخاب کنید. برای مقایسهٔ سرویس‌ها می‌توانید ابتدا راهنمای انتخاب سرویس را ببینید.
        """
        text = await self.section_intro_once(user, "purchase", "ابتدا نوع سرویس را انتخاب کنید؛ قبل از پرداخت، مشخصات و مبلغ نهایی را مرور کنید.") + text
        services = [{"text": label, "callback_data": f"purchase_category_{category}", }
                    for category, label in self.CATEGORY_LABELS.items()]
        services.append({"text": "💊 تجویز ویژه دکتر", "callback_data": "purchase_special", "style": "primary"})
        buttons = [services[index:index + 2] for index in range(0, len(services), 2)]
        buttons.extend([
            [{"text": "📖 راهنمای انتخاب سرویس", "callback_data": "service_guide"}],
            [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
        ])
        pending = await Order.objects.filter(
            brand=self.brand, user=user,
            status__in=[Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT],
            payments__status=Payment.PaymentStatus.CONFIRMED,
            payments__payment_method=Payment.PaymentMethod.WALLET,
        ).order_by("-created_at").afirst()
        if pending:
            buttons.insert(0, [{
                "text": "💳 ادامه سفارش نیمه‌پرداخت‌شده",
                "callback_data": f"payment_methods_{pending.order_id}",
            }])
        keyboard = self.create_keyboard(buttons)
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_plans_by_category(
        self, callback: types.CallbackQuery, category: str
    ):
        """Show active plans in exactly one configured service category."""
        if category not in self.CATEGORY_LABELS:
            await callback.answer("❌ دستهٔ سرویس نامعتبر است.", show_alert=True)
            return
        await self.show_service_picker(callback, category)

    async def show_special_offers(self, callback: types.CallbackQuery):
        """Show non-expired plans explicitly marked as special offers."""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": "plan_selection", "special_offers": True, "service_category": None, "plan_picker": None},
        )
        text, keyboard = await self.get_plans(
            user, special_only=True, back_callback="purchase_subscription"
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_service_guide(self, callback: types.CallbackQuery):
        """Show configurable service descriptions and direct links to each group."""
        try:
            config = await BrandConfiguration.objects.aget(brand=self.brand)
            custom_fields = config.custom_fields or {}
        except BrandConfiguration.DoesNotExist:
            custom_fields = {}
        guides = custom_fields.get("service_guides", {})
        if not isinstance(guides, dict):
            guides = {}

        lines = ["📖 <b>راهنمای انتخاب سرویس</b>"]
        for category, label in self.CATEGORY_LABELS.items():
            description = str(guides.get(category, "")).strip()
            if not description:
                description = (
                    "توضیح اختصاصی این سرویس هنوز تنظیم نشده است؛ "
                    "مشخصات کامل هر پلن در صفحهٔ خرید نمایش داده می‌شود."
                )
            # Telegram text messages are capped at 4096 characters. Keep each
            # configurable section bounded and escape admin-provided HTML.
            description = escape(description[:900])
            lines.append(f"\n<b>{escape(label)}</b>\n{description}")

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🌐 رفتن به سرویس نرمال",
                        "callback_data": f"purchase_category_{SubscriptionPlan.ServiceCategory.NORMAL}",
                    }
                ],
                [
                    {
                        "text": "👑 رفتن به سرویس رویال",
                        "callback_data": f"purchase_category_{SubscriptionPlan.ServiceCategory.ROYAL}",
                    }
                ],
                [
                    {
                        "text": "🇮🇷 رفتن به سرویس آی‌پی ایران",
                        "callback_data": f"purchase_category_{SubscriptionPlan.ServiceCategory.IRAN_IP}",
                    }
                ],
                [{"text": "💊 تجویز ویژه دکتر", "callback_data": "purchase_special", "style": "primary"}],
                [{"text": "🔙 بازگشت", "callback_data": "purchase_subscription"}],
            ]
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            "\n".join(lines),
            keyboard,
        )
        await callback.answer()

    async def show_plan_details(self, callback: types.CallbackQuery, plan_id: int):
        """Show detailed plan information"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            plan = await SubscriptionPlan.objects.aget(
                id=plan_id, brand=self.brand, is_active=True, is_visible=True
            )
        except SubscriptionPlan.DoesNotExist:
            await callback.answer("❌ پلن یافت نشد.", show_alert=True)
            return

        if not await is_plan_visible_in_bot(plan, self.brand.pk):
            await callback.answer("❌ این پلن در دسترس نیست.", show_alert=True)
            return
        if temporary_duration_blocked(plan):
            await callback.answer(TEMPORARILY_UNAVAILABLE_TEXT, show_alert=True)
            return

        state = await self.get_user_state(user)
        state_data = state.state_data or {}
        if state_data.get("special_offers"):
            back_callback = "purchase_special"
        elif state_data.get("plan_picker"):
            picker = state_data["plan_picker"]
            back_callback = f"pf:{picker['token']}:stage:{picker['stage']}"
        else:
            category = state_data.get("service_category") or plan.service_category
            back_callback = f"purchase_category_{category}"

        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {
                "step": "plan_details",
                "plan_id": plan_id,
                "details_back_callback": back_callback,
            },
        )

        credit = min(await self.purchase_credit(user, plan.currency), max(plan.discounted_price, 0))
        text = (
            f"🛒 <b>{escape(self.CATEGORY_LABELS.get(plan.service_category, plan.get_service_category_display()))}</b>\n"
            f"📋 {compact_plan_specs(plan)}\n\n"
            f"💳 <b>مبلغ پرداختی شما:</b> {self.format_price(self.payable_plan_price(plan, credit), plan.currency)}\n"
            f"💰 قیمت بدون احتساب امتیازات نقد: {self.format_price(plan.price, plan.currency)}"
        )
        if plan.discounted_price != plan.price:
            text += f"\n🔥 قیمت با تخفیف عمومی: {self.format_price(plan.discounted_price, plan.currency)}"

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "✅ ادامه و رفتن به خرید",
                        "callback_data": f"purchase_plan_{plan_id}",
                        "style": "success",
                    }
                ],
                [
                    {
                        "text": "👤 خرید برای دیگری",
                        "callback_data": f"buy_for_other_{plan_id}",
                    },
                ],
                [
                    {
                        "text": "🔙 بازگشت به پلن‌ها",
                        "callback_data": back_callback,
                    }
                ],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def initiate_purchase(
        self, callback: types.CallbackQuery, plan_id: int, purchase_type: str = "self"
    ):
        """Initiate purchase process"""
        user, _ = await self.get_or_create_user(callback.from_user)

        if purchase_type == "gift":
            await self.show_plan_details(callback, plan_id)
            return
        if purchase_type == "other" and not (self.brand.bot_username or '').strip():
            await callback.answer('لینک هدیه فعال نیست؛ نام کاربری ربات در پنل ثبت نشده است.', show_alert=True)
            return

        try:
            plan = await SubscriptionPlan.objects.aget(
                id=plan_id, brand=self.brand, is_active=True, is_visible=True
            )
        except SubscriptionPlan.DoesNotExist:
            await callback.answer("❌ پلن یافت نشد.", show_alert=True)
            return

        if not await is_plan_visible_in_bot(plan, self.brand.pk):
            await callback.answer("❌ این پلن در دسترس نیست.", show_alert=True)
            return
        if temporary_duration_blocked(plan):
            await callback.answer(TEMPORARILY_UNAVAILABLE_TEXT, show_alert=True)
            return

        order = await Order.objects.acreate(
            brand=self.brand,
            user=user,
            plan=plan,
            order_type=(
                Order.OrderType.GIFT
                if purchase_type in ("gift", "other")
                else Order.OrderType.NEW_SUBSCRIPTION
            ),
            original_price=plan.price,
            discount_amount=plan.price - plan.discounted_price,
            final_price=plan.discounted_price,
            currency=plan.currency,
            status=Order.OrderStatus.PENDING,
        )

        # First-purchase gifts are applied on the server before any payment
        # quote is issued. A failed or expired campaign never blocks checkout.
        from apps.orders.coupons import apply_first_purchase_referral_coupon
        await sync_to_async(apply_first_purchase_referral_coupon)(
            brand_id=self.brand.pk, user_id=user.pk, order_id=str(order.order_id),
        )
        # Reload the updated totals and relations together. arefresh_from_db()
        # invalidates foreign-key caches, and touching order.plan later from an
        # aiogram callback otherwise runs a synchronous query in the async loop.
        order = await Order.objects.select_related(
            "brand", "user", "plan", "recipient"
        ).aget(pk=order.pk)

        if order.final_price <= 0 and order.order_type == Order.OrderType.GIFT:
            from apps.orders.checkout_links import issue_paid_gift, paid_gift_link
            order.status = Order.OrderStatus.PAID
            await order.asave(update_fields=['status', 'updated_at'])
            voucher = await sync_to_async(issue_paid_gift)(order_id=order.pk)
            link = paid_gift_link(voucher) if voucher else None
            msg = (
                '🎁 اشتراک هدیه آماده است.\n'
                'لینک اختصاصی هدیه و معرفی مرتبط با این خرید:\n'
                f'{link}\n\n'
                'لینک را برای دریافت‌کننده بفرستید. نخستین دریافت‌کننده تا مهلت درج‌شده در سیستم '
                'می‌تواند اشتراک را تحویل بگیرد و بعد از بخش «اشتراک‌های من» نام‌گذاری کند.\n'
                'مثال نام: آیفون خودم\n'
                'معرفی فقط برای دریافت‌کننده‌ای که تازه عضو ربات شده ثبت می‌شود.'
            ) if link else '🎁 هدیه ثبت شد؛ نام کاربری ربات باید در پنل تکمیل شود.'
            await self.edit_message_with_keyboard(
                callback.message.chat.id, callback.message.message_id, msg,
                self.get_back_keyboard('purchase_subscription'),
            )
            await callback.answer()
            return

        if order.final_price <= 0:
            queued, message = await self._queue_free_order(order)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
            keyboard = self.create_keyboard(
                [[{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}]]
            )
            await self.edit_message_with_keyboard(
                callback.message.chat.id,
                callback.message.message_id,
                message,
                keyboard,
            )
            await callback.answer(
                "✅ درخواست ثبت شد" if queued else "❌ فعال‌سازی انجام نشد",
                show_alert=not queued,
            )
            return

        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {
                "step": "payment_method",
                "order_id": str(order.order_id),
                "purchase_type": purchase_type,
            },
        )

        await self.show_payment_methods(callback, order)

    async def repurchase_subscription(
        self, callback: types.CallbackQuery, subscription_id: int
    ):
        """Buy the current plan again for the same subscription owner."""
        await self.purchase_plan_for_subscription(
            callback, subscription_id=subscription_id, plan_id=None
        )

    async def purchase_plan_for_subscription(
        self,
        callback: types.CallbackQuery,
        subscription_id: int,
        plan_id: int | None,
    ):
        """Create a new order for the selected renewal plan and preserve the source owner."""
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            subscription = (
                await Subscription.objects.select_related("plan", "owner", "user")
                .filter(Q(user=user) | Q(owner=user), brand=self.brand)
                .aget(pk=subscription_id)
            )
        except Subscription.DoesNotExist:
            await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
            return

        if plan_id is None:
            plan = subscription.plan
        else:
            try:
                plan = await SubscriptionPlan.objects.aget(
                    pk=plan_id,
                    brand=self.brand,
                    is_active=True,
                    is_visible=True,
                )
            except SubscriptionPlan.DoesNotExist:
                await callback.answer("❌ پلن انتخاب‌شده در دسترس نیست.", show_alert=True)
                return

        if (
            not plan.is_active
            or not plan.is_visible
            or not await is_plan_visible_in_bot(plan, self.brand.pk)
        ):
            await callback.answer(
                "❌ این پلن دیگر برای خرید فعال نیست.", show_alert=True
            )
            return

        if temporary_duration_blocked(plan):
            await callback.answer(TEMPORARILY_UNAVAILABLE_TEXT, show_alert=True)
            return

        recipient = (
            subscription.owner
            if subscription.user_id == user.pk and subscription.owner_id != user.pk
            else None
        )
        order = await Order.objects.acreate(
            brand=self.brand,
            user=user,
            recipient=recipient,
            plan=plan,
            order_type=(
                Order.OrderType.GIFT if recipient else Order.OrderType.NEW_SUBSCRIPTION
            ),
            original_price=plan.price,
            discount_amount=plan.price - plan.discounted_price,
            final_price=plan.discounted_price,
            currency=plan.currency,
            status=Order.OrderStatus.PENDING,
            notes=(
                f"Renewal/re-purchase requested from subscription {subscription.pk}; "
                f"selected plan {plan.pk}."
            ),
        )

        # The first paid checkout can start from a previously claimed/received
        # subscription as well as the regular product catalog.
        from apps.orders.coupons import apply_first_purchase_referral_coupon
        await sync_to_async(apply_first_purchase_referral_coupon)(
            brand_id=self.brand.pk, user_id=user.pk, order_id=str(order.order_id),
        )
        # Reload the updated totals and relations together. arefresh_from_db()
        # invalidates foreign-key caches, and touching order.plan later from an
        # aiogram callback otherwise runs a synchronous query in the async loop.
        order = await Order.objects.select_related(
            "brand", "user", "plan", "recipient"
        ).aget(pk=order.pk)

        if order.final_price <= 0:
            queued, message = await self._queue_free_order(order)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
            await self.edit_message_with_keyboard(
                callback.message.chat.id,
                callback.message.message_id,
                message,
                self.create_keyboard(
                    [[{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}]]
                ),
            )
            await callback.answer(
                "✅ درخواست ثبت شد" if queued else "❌ فعال‌سازی انجام نشد",
                show_alert=not queued,
            )
            return

        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {
                "step": PurchaseStep.PAYMENT_METHOD,
                "order_id": str(order.order_id),
                "purchase_type": "renewal_repurchase",
                "source_subscription_id": subscription.pk,
                "selected_plan_id": plan.pk,
            },
        )
        await self.show_payment_methods(callback, order)

    async def _queue_free_order(self, order: Order) -> tuple[bool, str]:
        """Fulfill zero-price plans without routing them through payment screens."""
        provider = None
        if order.plan.vpn_provider_id:
            provider = await VPNProvider.objects.filter(
                pk=order.plan.vpn_provider_id,
                brand=order.brand,
                status=VPNProvider.ProviderStatus.ACTIVE,
            ).afirst()
        if provider is None:
            provider = await VPNProvider.objects.filter(
                brand=order.brand,
                status=VPNProvider.ProviderStatus.ACTIVE,
                is_default=True,
            ).afirst()
        if provider is None:
            order.status = Order.OrderStatus.FAILED
            order.admin_notes = "Free order could not be provisioned: no active provider."
            await order.asave(update_fields=("status", "admin_notes", "updated_at"))
            return False, "❌ برای این برند پنل فعالی تنظیم نشده است. با پشتیبانی تماس بگیرید."

        await Subscription.objects.aget_or_create(
            order=order,
            defaults={
                "brand": order.brand,
                "user": order.user,
                "plan": order.plan,
                "vpn_provider": provider,
                "owner": order.recipient or order.user,
                "status": Subscription.SubscriptionStatus.PENDING,
                "starts_at": order.created_at,
                "traffic_limit_gb": order.plan.traffic_limit_gb,
            },
        )
        order.status = Order.OrderStatus.PAID
        order.admin_notes = "Zero-price plan; no payment required."
        await order.asave(update_fields=("status", "admin_notes", "updated_at"))
        provision_paid_order.delay(order.pk)
        return True, (
            "✅ پلن رایگان ثبت شد و برای فعال‌سازی ارسال شد.\n"
            "پس از آماده‌شدن، پیام تأیید دریافت می‌کنی؛ وضعیت را از «اشتراک‌های من» ببین."
        )

    async def show_payment_methods(
        self, callback: types.CallbackQuery, order: Order, *, answer_callback: bool = True
    ):
        """Apply available cash credit and show only the remaining payment."""
        user, _ = await self.get_or_create_user(callback.from_user)
        text, keyboard = await self._order_payment_content(user, order)
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        if answer_callback:
            await callback.answer()

    @staticmethod
    def _copyable_amount(amount, currency):
        # Copy only the exact numeric amount; neither group separators nor currency
        # labels should be pasted into a bank transfer amount field.
        numeric = f"{amount:.2f}".rstrip("0").rstrip(".")
        unit = {"T": "تومان", "IRT": "تومان", "IRR": "ریال",
                "USD": "دلار"}.get(currency, currency)
        return f"<code>{numeric}</code> {escape(unit)}"

    async def _order_payment_content(self, user, order):
        if order.status not in {
            Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT,
            Order.OrderStatus.PAID, Order.OrderStatus.PROCESSING, Order.OrderStatus.COMPLETED,
        }:
            return "این سفارش دیگر برای پرداخت فعال نیست.", self.get_back_keyboard("purchase_subscription")
        if (order.status in {Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT}
                and temporary_duration_blocked(order.plan)):
            return TEMPORARILY_UNAVAILABLE_TEXT, self.get_back_keyboard("purchase_subscription")
        if order.status in {Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT}:
            if await self.purchase_credit(user, order.currency) > 0:
                try:
                    await sync_to_async(pay_order_with_wallet)(
                        order_id=str(order.order_id), user_id=user.pk,
                        brand_id=self.brand.pk, allow_partial=True,
                    )
                except WalletCheckoutError as exc:
                    logger.warning("Automatic cash credit unavailable for order %s (%s)", order.pk, exc)
        total_paid = await self._confirmed_order_payments(order)
        wallet_paid = (await order.payments.filter(
            status=Payment.PaymentStatus.CONFIRMED,
            payment_method=Payment.PaymentMethod.WALLET,
        ).aaggregate(total=Sum("amount")))["total"] or 0
        remaining_due = max(order.final_price - total_paid, 0)
        if remaining_due <= 0:
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
            return (
                f"✅ پرداخت سفارش {order.order_number} تکمیل شد.\n"
                f"💰 مبلغ کل: {self.format_price(order.final_price, order.currency)}\n\n"
                f"💊 کسر از موجودی نقد: {self.format_price(wallet_paid, order.currency)}\n"
                f"💳 پرداخت خارج از کیف پول: {self.format_price(max(order.final_price - wallet_paid, 0), order.currency)}\n\n"
                "وضعیت فعال‌سازی را از «اشتراک‌های من» ببینید.",
                self.create_keyboard([
                    [{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}],
                    [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
                ]),
            )
        plan = order.plan
        plan_code = connectix_plan_name(
            max_users=plan.max_users, plan_type=plan.plan_type,
            traffic_limit_gb=plan.traffic_limit_gb,
            duration_value=plan.duration_value, duration_unit=plan.duration_unit,
            service_category=plan.service_category,
        )
        text = (
            "💳 <b>انتخاب روش پرداخت</b>\n\n"
            f"Plan : <code>{escape(plan_code)}</code>\n"
            f"مبلغ قابل پرداخت: {self._copyable_amount(remaining_due, order.currency)}\n\n"
            "روش پرداخت را انتخاب کنید:"
        )
        if order.order_type == Order.OrderType.GIFT:
            text = '🎁 <b>خرید هدیه</b>\nلینک یک‌بارمصرف بعد از تأیید پرداخت برای شما فرستاده می‌شود.\n\n' + text
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": PurchaseStep.PAYMENT_METHOD, "order_id": str(order.order_id)},
        )
        keyboard = await self._payment_methods_keyboard(user, order)
        if not order.coupon_code:
            keyboard.inline_keyboard.insert(0, [types.InlineKeyboardButton(
                text="🎟 ثبت کد تخفیف", callback_data=f"purchase_coupon_{order.order_id}"
            )])
        if not any(
            button.callback_data and button.callback_data.startswith("payment_")
            for row in keyboard.inline_keyboard for button in row
        ):
            text = text.replace(
                "روش پرداخت را انتخاب کنید:",
                "⚠️ در حال حاضر روش پرداختی فعال نیست.\nبرای راهنمایی با پشتیبانی هماهنگ کنید.",
            )
            keyboard.inline_keyboard.insert(0, [types.InlineKeyboardButton(
                text="🛟 پشتیبانی", callback_data="support"
            )])

        return text, keyboard

    async def request_purchase_coupon(self, callback, order_id: str):
        user, _ = await self.get_or_create_user(callback.from_user)
        order = await Order.objects.filter(
            user=user, brand=self.brand, order_id=order_id,
            status__in=[Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT],
        ).afirst()
        if not order:
            await callback.answer("سفارش قابل تخفیف نیست.", show_alert=True)
            return
        await self.update_user_state(user, BotState.StateType.PAYMENT_PROCESS, {
            "step": "waiting_purchase_coupon", "action": "purchase_coupon", "order_id": order_id,
        })
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id,
            "🎟 کد تخفیف را ارسال کنید. مبلغ نهایی قبل از انتخاب کارت دوباره محاسبه می‌شود.",
            self.get_back_keyboard(f"payment_methods_{order_id}"),
        )
        await callback.answer()

    async def handle_purchase_coupon_message(self, message, user, state):
        from apps.orders.coupons import apply_purchase_coupon, PurchaseCouponError
        order_id = (state.state_data or {}).get("order_id")
        code = (message.text or "").strip()
        try:
            order, discount = await sync_to_async(apply_purchase_coupon)(
                brand_id=self.brand.pk, user_id=user.pk, order_id=order_id, code=code,
            )
        except PurchaseCouponError as exc:
            await message.reply(f"❌ {escape(str(exc))}")
            return
        await self._remove_user_input(message)
        order = await Order.objects.select_related("plan").aget(pk=order.pk)
        text, keyboard = await self._order_payment_content(user, order)
        text = f"✅ کد تخفیف اعمال شد: {self.format_price(discount, order.currency)}\n\n" + text
        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    async def show_payment_methods_for_order(
        self, callback: types.CallbackQuery, order_id: str
    ):
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            order = await Order.objects.select_related("plan").aget(
                order_id=order_id, user=user, brand=self.brand
            )
        except Order.DoesNotExist:
            await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
            return
        await self.show_payment_methods(callback, order)

    async def _payment_methods_keyboard(self, user, order):
        keyboard_buttons = []
        cards_available = await self.brand.payment_cards.filter(is_active=True).aexists()
        async for method in self.brand.payment_methods.filter(is_enabled=True).order_by(
            "display_order"
        ):
            if method.payment_type == "wallet":
                continue
            if method.payment_type != "card_transfer":
                logger.warning(
                    "Skipping unsupported purchase payment method %s for brand %s",
                    method.payment_type,
                    self.brand.slug,
                )
                continue
            if not cards_available:
                continue
            keyboard_buttons.append(
                [
                    {
                        "text": "💳 کارت‌به‌کارت",
                        "callback_data": f"payment_{method.payment_type}_{order.order_id}",
                    }
                ]
            )

        total_paid = await self._confirmed_order_payments(order)
        remaining_due = max(order.final_price - total_paid, 0)
        wallet_amount = min(await self.purchase_credit(user, order.currency), remaining_due)
        if wallet_amount > 0:
            wallet_label = (
                "💰 پرداخت کامل از کیف پول"
                if wallet_amount >= remaining_due
                else (
                    f"💰 کیف پول {self.format_price(wallet_amount, order.currency)}؛ "
                    f"مانده {self.format_price(remaining_due - wallet_amount, order.currency)}"
                )
            )
            keyboard_buttons.insert(
                0,
                [
                    {
                        "text": wallet_label,
                        "callback_data": f"payment_wallet_{order.order_id}",
                    }
                ],
            )

        keyboard_buttons.append(
            [{"text": "❌ انصراف", "callback_data": f"cancel_purchase_{order.order_id}"}]
        )
        return self.create_keyboard(keyboard_buttons)

    async def cancel_purchase(self, callback, order_id):
        user, _ = await self.get_or_create_user(callback.from_user)
        try:
            returned = await sync_to_async(cancel_unpaid_order)(
                order_id=order_id, user_id=user.pk, brand_id=self.brand.pk
            )
        except WalletCheckoutError:
            await callback.answer("این سفارش قابل انصراف خودکار نیست؛ با پشتیبانی هماهنگ کنید.", show_alert=True)
            return
        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        text = "سفارش لغو شد."
        if returned:
            currency = await Order.objects.filter(
                order_id=order_id, user=user, brand=self.brand
            ).values_list("currency", flat=True).aget()
            text += f"\n💰 {self.format_price(returned, currency)} به موجودی نقد شما برگشت."
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text,
            self.get_back_keyboard("purchase_subscription"),
        )
        await callback.answer()

    @staticmethod
    async def _confirmed_order_payments(order):
        totals = await order.payments.filter(
            status=Payment.PaymentStatus.CONFIRMED
        ).aaggregate(total=Sum("amount"))
        return totals["total"] or 0

    async def process_wallet_payment(
        self, callback: types.CallbackQuery, order_id: str
    ):
        """Process wallet payment"""
        user, _ = await self.get_or_create_user(callback.from_user)
        requested_plan = await Order.objects.filter(
            order_id=order_id, user=user, brand=self.brand
        ).values_list("plan_id", flat=True).afirst()
        if requested_plan:
            plan = await SubscriptionPlan.objects.filter(pk=requested_plan).afirst()
            if plan and temporary_duration_blocked(plan):
                await callback.answer(TEMPORARILY_UNAVAILABLE_TEXT, show_alert=True)
                return

        try:
            result = await sync_to_async(pay_order_with_wallet)(
                order_id=order_id,
                user_id=user.pk,
                brand_id=self.brand.pk,
                allow_partial=True,
            )
        except WalletCheckoutError as exc:
            messages = {
                "Order was not found": "❌ سفارش یافت نشد.",
                "Insufficient wallet balance": "❌ موجودی کیف پول کافی نیست.",
                "Wallet currency does not match order currency": "❌ ارز کیف پول با سفارش یکسان نیست.",
                "Wallet is unavailable": "❌ کیف پول در دسترس نیست.",
                "Requested duration is temporarily unavailable": TEMPORARILY_UNAVAILABLE_TEXT,
            }
            await callback.answer(
                messages.get(str(exc), "❌ پرداخت از کیف پول انجام نشد."),
                show_alert=True,
            )
            return

        order = await Order.objects.select_related(
            "brand", "user", "recipient", "plan", "plan__vpn_provider"
        ).aget(order_id=order_id, user=user, brand=self.brand)

        if result.remaining_due > 0:
            await self.show_payment_methods(callback, order, answer_callback=False)
            await callback.answer(
                f"مبلغ {self.format_price(result.amount_applied, order.currency)} از کیف پول کسر شد؛ "
                f"{self.format_price(result.remaining_due, order.currency)} باقی مانده است.",
                show_alert=True,
            )
            return

        text = f"""
✅ پرداخت موفق!

سفارش شما با موفقیت پردازش شد.
شماره سفارش: {order.order_number}
مبلغ پرداختی: {self.format_price(order.final_price, order.currency)}

اشتراک شما در صف فعال‌سازی قرار گرفت.
        """

        keyboard = self.create_keyboard(
            [
                [{"text": "📱 مشاهده اشتراک‌ها", "callback_data": "my_subscriptions"}],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

        await self.update_user_state(user, BotState.StateType.MAIN_MENU)
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer(
            "✅ این سفارش قبلاً پرداخت شده است."
            if result.already_paid
            else "✅ پرداخت با موفقیت انجام شد!"
        )

    async def show_card_transfer_payment(self, callback: types.CallbackQuery, order_id: str):
        """One card/amount screen after payment method: no intermediate bank menu."""
        user, _ = await self.get_or_create_user(callback.from_user)
        order = await Order.objects.select_related('plan').filter(
            order_id=order_id, user=user, brand=self.brand,
            status__in=[Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT],
        ).afirst()
        if not order or max(order.final_price - await self._confirmed_order_payments(order), 0) <= 0:
            await callback.answer('این سفارش قابل پرداخت نیست.', show_alert=True)
            return
        if temporary_duration_blocked(order.plan):
            await callback.answer(TEMPORARILY_UNAVAILABLE_TEXT, show_alert=True)
            return
        if not await self.brand.payment_methods.filter(
            payment_type='card_transfer', is_enabled=True,
        ).aexists():
            await callback.answer('روش کارت‌به‌کارت فعال نیست.', show_alert=True)
            return
        card = await self.brand.payment_cards.filter(is_active=True).order_by('display_order', 'pk').afirst()
        if not card:
            await callback.answer('کارت بانکی فعالی ثبت نشده است.', show_alert=True)
            return
        from apps.orders.checkout_links import reserve_card_quote, QuoteUnavailable
        try:
            quote = await sync_to_async(reserve_card_quote)(
                order_id=order_id, user_id=user.pk, brand_id=self.brand.pk, card_id=card.pk,
            )
        except (QuoteUnavailable, Order.DoesNotExist, PaymentCard.DoesNotExist) as exc:
            await callback.answer(str(exc) if isinstance(exc, QuoteUnavailable) else 'اطلاعات پرداخت در دسترس نیست.', show_alert=True)
            return
        await self.update_user_state(user, BotState.StateType.PAYMENT_PROCESS, {
            'step': PurchaseStep.CARD_TRANSFER, 'order_id': order_id, 'card_id': card.pk,
            'action': None,
        })
        extra = quote.expected_amount - quote.base_amount
        text = (
            '💳 <b>کارت‌به‌کارت</b>\n\n'
            f'💰 مبلغ واریز: {self._copyable_amount(quote.expected_amount, order.currency)}\n'
            f'🏦 بانک {escape(card.bank_name)}\n'
            f'💳 شماره کارت: <code>{escape(card.card_number)}</code>\n'
            f'👤 {escape(card.cardholder_name)}\n\n'
            'روی مبلغ بزنید تا کپی شود؛ لطفاً دقیقاً همین مبلغ را واریز کنید.\n'
            'بعد از واریز، رسید متنی یا تصویری را بفرستید.'
        )
        if extra > 0:
            text += '\n\nمبلغ شامل یک اختلاف کوچک برای شناسایی آسان‌تر واریز شماست.'
        kb = self.create_keyboard([
            [{'text': '✅ واریز کردم', 'callback_data': f'payment_done_{order_id}'}],
            [{'text': '🔙 روش‌های پرداخت', 'callback_data': f'payment_methods_{order_id}'}],
        ])
        await self.edit_message_with_keyboard(callback.message.chat.id, callback.message.message_id, text, kb)
        await callback.answer()

    async def show_selected_card(self, callback: types.CallbackQuery, order_id: str, card_id: int):
        # Old Telegram keyboards may still exist. Never let them bypass the
        # currently configured single-card checkout or its reserved quote.
        await self.show_card_transfer_payment(callback, order_id)

    async def create_subscription(self, order: Order):
        """Delegate provisioning to the provider-aware subscription service."""
        from apps.subscriptions.services import provision_order_subscription

        return await provision_order_subscription(order_id=order.pk)

    async def payment_done(self, callback: types.CallbackQuery, order_id: str):
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            order = await Order.objects.select_related("plan").aget(
                order_id=order_id, user=user, brand=self.brand
            )
        except Order.DoesNotExist:
            await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
            return

        if temporary_duration_blocked(order.plan):
            await callback.answer(TEMPORARILY_UNAVAILABLE_TEXT, show_alert=True)
            return
        state = await self.get_user_state(user)
        sd = state.state_data or {}
        if (
            state.current_state != BotState.StateType.PAYMENT_PROCESS
            or sd.get("step") != PurchaseStep.CARD_TRANSFER
            or sd.get("order_id") != order_id
        ):
            await callback.answer("❌ درخواست نامعتبر است.", show_alert=True)
            return

        if order.status not in (
            Order.OrderStatus.PENDING,
            Order.OrderStatus.AWAITING_PAYMENT,
        ):
            await callback.answer("❌ این سفارش قابل ادامه نیست.", show_alert=True)
            return

        dup = await Payment.objects.filter(
            order=order,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status__in=[Payment.PaymentStatus.PENDING, Payment.PaymentStatus.CONFIRMED],
        ).aexists()
        if dup:
            await callback.answer("⏳ رسید این سفارش قبلاً ثبت شده.", show_alert=True)
            return

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"step": PurchaseStep.WAITING_RECEIPT, "order_id": order_id, "action": None,
             "card_id": sd["card_id"]},
        )

        now = jalali_date(timezone.now(), time=True)
        text = (
            "📤 <b>ارسال رسید پرداخت</b>\n\n"
            "رسید متنی یا عکس واضح تراکنش را ارسال کنید.\n"
            "تاریخ و ساعت واریز باید در رسید قابل مشاهده باشد.\n"
            f"🕐 زمان ثبت درخواست: <code>{now}</code>"
        )
        keyboard = self.create_keyboard([
            [{"text": "❌ لغو پرداخت", "callback_data": f"payment_not_done_{order_id}"}],
        ])

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer("منتظر دریافت رسید هستیم")

    async def payment_not_done(self, callback: types.CallbackQuery, order_id: str):
        """Cancel payment flow"""

        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            order = await Order.objects.aget(
                order_id=order_id, user=user, brand=self.brand
            )
        except Order.DoesNotExist:
            await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
            return

        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": "payment_method", "order_id": order_id},
        )

        text = f"""
    ❌ پرداخت لغو شد

    سفارش: {order.order_number}

    می‌توانید دوباره روش پرداخت را انتخاب کنید.
        """

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "💳 انتخاب روش پرداخت",
                        "callback_data": f"payment_methods_{order_id}",
                    }
                ],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )

        await callback.answer("❌ پرداخت لغو شد")

    async def handle_photo_message(self, message: types.Message, state: BotState):
        """Legacy entry point, accepts image or text using a shared guarded service."""
        await self.handle_payment_receipt(message, state)

    @staticmethod
    @sync_to_async
    def _record_card_receipt(*, order_id, user_id, brand_id, card_id, receipt_text, file_id):
        """Serialize concurrent submissions and store exactly one pending receipt."""
        with transaction.atomic():
            order = Order.objects.select_for_update().get(
                order_id=order_id, user_id=user_id, brand_id=brand_id,
            )
            if order.status not in (Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT):
                raise ValueError("سفارش دیگر برای پرداخت فعال نیست.")
            if Payment.objects.filter(order=order, payment_method=Payment.PaymentMethod.CARD_TRANSFER,
                                      status__in=[Payment.PaymentStatus.PENDING,
                                                  Payment.PaymentStatus.CONFIRMED]).exists():
                raise ValueError("رسید این سفارش قبلاً دریافت شده است.")
            total = Payment.objects.filter(order=order, status=Payment.PaymentStatus.CONFIRMED).aggregate(
                total=Sum("amount"))["total"] or 0
            due = max(order.final_price - total, 0)
            if due <= 0:
                raise ValueError("سفارش تسویه شده است.")
            card = PaymentCard.objects.filter(pk=card_id, brand_id=brand_id, is_active=True).first()
            if not card:
                raise ValueError("کارت مقصد دیگر فعال نیست؛ بانک دیگری انتخاب کنید.")
            from apps.orders.models import CardTransferQuote
            quote = CardTransferQuote.objects.select_for_update().filter(
                order=order, card=card, expires_at__gt=timezone.now(),
            ).first()
            if quote is None or quote.base_amount != due:
                raise ValueError('مبلغ معتبر این سفارش منقضی یا تغییر کرده است؛ دوباره وارد صفحه پرداخت شوید.')
            payment = Payment.objects.create(
                order=order, brand_id=brand_id, user_id=user_id,
                payment_method=Payment.PaymentMethod.CARD_TRANSFER,
                amount=quote.expected_amount, currency=order.currency,
                status=Payment.PaymentStatus.PENDING,
                receipt_file=file_id or "", receipt_reference=(receipt_text[:255] or None),
                card_number=card.card_number, cardholder_name=card.cardholder_name,
                notes=f"Destination bank: {card.bank_name}\nReceipt: {receipt_text}",
            )
            order.status = Order.OrderStatus.PROCESSING
            order.save(update_fields=["status", "updated_at"])
            return payment

    async def handle_payment_receipt(self, message: types.Message, state: BotState):
        user, _ = await self.get_or_create_user(message.from_user)
        sd = state.state_data or {}
        if (state.current_state != BotState.StateType.PAYMENT_PROCESS or
            sd.get("step") != PurchaseStep.WAITING_RECEIPT or not sd.get("card_id")):
            await message.answer("برای ارسال رسید ابتدا روش کارت‌به‌کارت را انتخاب کنید.")
            return
        receipt_text = (message.text or message.caption or "").strip()
        file_id = message.photo[-1].file_id if message.photo else ""
        if not file_id and (len(receipt_text) < 10 or len(receipt_text) > 2000):
            await message.answer("رسید متنی باید شامل جزئیات تراکنش (۱۰ تا ۲۰۰۰ نویسه) یا عکس باشد.")
            return
        try:
            payment = await self._record_card_receipt(
                order_id=sd.get("order_id"), user_id=user.pk, brand_id=self.brand.pk,
                card_id=sd["card_id"], receipt_text=receipt_text[:2000], file_id=file_id,
            )
        except (Order.DoesNotExist, ValueError) as exc:
            await message.answer(escape(str(exc)))
            return
        await self.update_user_state(user, BotState.StateType.PAYMENT_PROCESS, {
            "step": PurchaseStep.UNDER_REVIEW, "order_id": sd["order_id"],
            "payment_id": str(payment.pk),
        })
        at = jalali_date(payment.created_at, time=True)
        order = await Order.objects.select_related("plan", "user", "user__referred_by").aget(pk=payment.order_id)
        if order.order_type == Order.OrderType.GIFT and not order.recipient_id:
            text = (
                "🎁 رسید خرید برای دیگران ثبت شد\n\n"
                "تا زمان تایید تراکنش و ارسال اشتراک\n"
                "میتوانید لینک نصب برنامه و توضیحات کار با برنامه را به کاربر نهایی منتقل کنید"
            )
            gift_help = (
                UsefulContent.Category.DOWNLOADS,
                UsefulContent.Category.INSTALL,
                UsefulContent.Category.APP_USAGE,
            )
            keyboard = self.create_keyboard([
                [{"text": UsefulContentHandler.CATEGORY_LABELS[key],
                  "callback_data": f"useful_category_{key}"}]
                for key in gift_help
            ] + [[{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}]])
        else:
            text = (
                f"✅ رسید دریافت شد.\n🕐 تاریخ و ساعت ثبت: <code>{at}</code>\n"
                "⏳ در انتظار بررسی تراکنش توسط ادمین.\n\n"
                "تا زمان تأیید تراکنش و ارسال/تمدید اشتراک شما، "
                "می‌توانید محتواهای آکادمی را مطالعه کنید."
            )
            keyboard = self.create_keyboard([
                *[[{"text": label, "callback_data": f"useful_category_{key}",
                    "style": "success" if key in {
                        "downloads", "install", "app_usage",
                    } else None}]
                  for key, label in UsefulContentHandler.CATEGORY_LABELS.items()],
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ])
        await self.send_message_with_keyboard(message.chat.id, text, keyboard)
        notified = await self._notify_admin_receipt(order, payment, file_id)
        if not notified:
            logger.error(
                "Payment receipt %s persisted without successful Telegram admin notification; "
                "requires review in Django Admin",
                payment.pk,
            )

    async def _notify_admin_receipt(
        self, order: Order, payment: Payment, file_id: str
    ) -> bool:
        """ارسال رسید به ادمین برای تأیید/رد"""
        purchaser = order.user
        referrer = purchaser.referred_by
        payer_name = purchaser.full_name or purchaser.get_full_name() or purchaser.username
        admin_text = (
            f"🧾 رسید جدید\n"
            f"حساب مقصد: <code>{escape(payment.card_number or '')}</code>"
            f" — {escape(payment.cardholder_name or '—')}\n"
            f"مبلغ: {self.format_price(payment.amount, order.currency)}\n"
            f"پلن: {escape(order.plan.name)}\n"
            f"تاریخ: {jalali_date(payment.created_at, time=True)}\n"
            f"خریدار: {_telegram_profile_link(purchaser.telegram_id, payer_name)}\n"
            f"آیدی عددی خریدار: {_telegram_profile_link(purchaser.telegram_id, purchaser.telegram_id or '—')}\n"
            f"آیدی عددی معرف: {_telegram_profile_link(referrer.telegram_id if referrer else None, referrer.telegram_id if referrer and referrer.telegram_id else '—')}"
        )
        admin_kb = self.create_keyboard(
            [
                [
                    {
                        "text": "✅ تأیید",
                        "callback_data": f"admin_confirm_payment_{payment.id}",
                    },
                    {
                        "text": "❌ رد",
                        "callback_data": f"admin_reject_payment_{payment.id}",
                    },
                ]
            ]
        )

        from apps.orders.models import AdminReceiptMessage
        notified = 0
        async for user in self.get_brand_admin_recipients():
            try:
                if file_id:
                    sent = await self.bot.send_photo(
                        chat_id=user.telegram_id, photo=file_id,
                        caption=admin_text, reply_markup=admin_kb, parse_mode="HTML",
                    )
                else:
                    sent = await self.bot.send_message(
                        chat_id=user.telegram_id, text=admin_text,
                        reply_markup=admin_kb, parse_mode="HTML",
                    )
                notified += 1
                # Keep IDs even if the Celery broker is temporarily unavailable.
                if isinstance(getattr(sent, "message_id", None), int):
                    await AdminReceiptMessage.objects.aget_or_create(
                        payment=payment, chat_id=user.telegram_id,
                        message_id=sent.message_id,
                    )
            except Exception:
                logger.exception("Failed to send receipt notification to admin %s", user.pk)
        if notified:
            logger.info(
                "Sent payment receipt notification to %s admin(s) for brand %s, payment %s",
                notified,
                self.brand.pk,
                payment.pk,
            )
        else:
            logger.error(
                "No Telegram admin received receipt notification for brand %s, payment %s",
                self.brand.pk,
                payment.pk,
            )
        return notified > 0

    async def admin_confirm_payment(
        self,
        callback: types.CallbackQuery,
        payment_id: int,
    ):
        """Confirm payment."""

        user, _ = await self.get_or_create_user(callback.from_user, use_cache=False)
        if not await self.has_admin_access(user):
            await callback.answer("⛔ دسترسی ادمین ندارید.", show_alert=True)
            return

        try:
            payment = await Payment.objects.aget(id=int(payment_id), brand=self.brand)
        except Payment.DoesNotExist:
            await callback.answer("❌ پرداخت یافت نشد.", show_alert=True)
            return
        if payment.status != Payment.PaymentStatus.PENDING:
            await callback.answer("❌ این پرداخت قابل تأیید نیست.", show_alert=True)
            return
        payment.status = Payment.PaymentStatus.CONFIRMED
        payment.verified_at = timezone.now()
        payment.verified_by = user
        await payment.asave(update_fields=["status", "verified_at", "verified_by", "updated_at"])

        # The post-save confirmation hook queues deletion of every admin receipt
        # message, including copies sent to other administrators.
        await callback.answer("✅ پرداخت تأیید شد.")

    async def admin_reject_payment(
        self,
        callback: types.CallbackQuery,
        payment_id: int,
    ):
        """Reject payment."""

        user, _ = await self.get_or_create_user(callback.from_user, use_cache=False)
        if not await self.has_admin_access(user):
            await callback.answer("⛔ دسترسی ادمین ندارید.", show_alert=True)
            return

        try:
            payment = await Payment.objects.aget(id=int(payment_id), brand=self.brand)
        except Payment.DoesNotExist:
            await callback.answer("❌ پرداخت یافت نشد.", show_alert=True)
            return

        if payment.status != Payment.PaymentStatus.PENDING:
            await callback.answer("❌ این پرداخت قابل رد نیست.", show_alert=True)
            return

        payment.status = Payment.PaymentStatus.FAILED
        await payment.asave(update_fields=["status"])

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("❌ پرداخت رد شد.")
