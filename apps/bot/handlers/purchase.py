"""
Purchase Handler for Multi-Tenant VPN Bot
Handles subscription purchases, plan selection, and payment processing
"""

import logging
from html import escape

from aiogram import types
from asgiref.sync import sync_to_async
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.brands.models import BrandConfiguration
from apps.orders.models import Order, Payment, Wallet
from apps.orders.services import WalletCheckoutError, pay_order_with_wallet
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.subscriptions.tasks import provision_paid_order
from apps.vpn_providers.models import VPNProvider

from .base import BaseHandler

logger = logging.getLogger(__name__)


class PurchaseStep:
    PLAN_SELECTION = "plan_selection"
    PLAN_DETAILS = "plan_details"
    PAYMENT_METHOD = "payment_method"
    CARD_TRANSFER = "card_transfer"
    WAITING_RECEIPT = "waiting_receipt"
    UNDER_REVIEW = "under_review"


class PurchaseHandler(BaseHandler):
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
    ):
        """Build a plan list for one XMind service group or the inline picker."""
        queryset = SubscriptionPlan.objects.filter(
            brand=self.brand, is_active=True, is_visible=True
        )
        if category:
            queryset = queryset.filter(service_category=category)
        if special_only:
            queryset = queryset.filter(is_featured=True).filter(
                Q(offer_expires_at__isnull=True) | Q(offer_expires_at__gt=timezone.now())
            )

        plans = []
        async for plan in queryset.order_by("display_order", "price"):
            plans.append(plan)

        if not plans:
            text = "❌ در حال حاضر پلن فعالی در این بخش موجود نیست."
            return text, self.get_back_keyboard(back_callback)

        if special_only:
            title = "🔥 پیشنهادهای ویژه"
        elif category:
            title = f"🛒 {self.CATEGORY_LABELS.get(category, 'پلن‌های اشتراک')}"
        else:
            title = f"🛒 پلن‌های اشتراک {self.brand.name}"

        text = f"{title}\n\nلطفاً یکی از پلن‌های زیر را انتخاب کنید:"
        keyboard_buttons = []
        for plan in plans:
            details = []
            if plan.plan_type == SubscriptionPlan.PlanType.UNLIMITED:
                if plan.duration_value:
                    details.append(
                        self.format_duration(plan.duration_value, plan.duration_unit)
                    )
                details.append("نامحدود")
            elif plan.plan_type == SubscriptionPlan.PlanType.TRAFFIC_BASED:
                if plan.traffic_limit_gb is not None:
                    details.append(self.format_traffic(plan.traffic_limit_gb))
            elif plan.plan_type == SubscriptionPlan.PlanType.TIME_BASED:
                if plan.duration_value:
                    details.append(
                        self.format_duration(plan.duration_value, plan.duration_unit)
                    )
            elif plan.plan_type == SubscriptionPlan.PlanType.HYBRID:
                if plan.duration_value:
                    details.append(
                        self.format_duration(plan.duration_value, plan.duration_unit)
                    )
                if plan.traffic_limit_gb is not None:
                    details.append(self.format_traffic(plan.traffic_limit_gb))

            plan_text = plan.name
            if details:
                plan_text += " • " + " • ".join(details)
            plan_text += f" • {self.format_price(plan.discounted_price, plan.currency)}"
            if plan.discount_percentage > 0 and (
                plan.offer_expires_at is None or plan.offer_expires_at > timezone.now()
            ):
                plan_text += f" • {plan.discount_percentage:g}% تخفیف"

            keyboard_buttons.append(
                [{"text": plan_text, "callback_data": f"select_plan_{plan.id}"}]
            )

        keyboard_buttons.append(
            [{"text": "🔙 بازگشت", "callback_data": back_callback}]
        )
        return text, self.create_keyboard(keyboard_buttons)

    async def show_subscription_plans(self, callback: types.CallbackQuery):
        """Show the XMind purchase landing page."""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user, BotState.StateType.PURCHASE_FLOW, {"step": "service_selection"}
        )

        text = f"""
🛒 خرید اشتراک {self.brand.name}

نوع سرویس را انتخاب کنید. برای مقایسهٔ سرویس‌ها می‌توانید ابتدا راهنمای سرویس‌ها را ببینید.
        """
        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🌐 سرویس نرمال",
                        "callback_data": f"purchase_category_{SubscriptionPlan.ServiceCategory.NORMAL}",
                    },
                    {
                        "text": "👑 سرویس رویال",
                        "callback_data": f"purchase_category_{SubscriptionPlan.ServiceCategory.ROYAL}",
                    },
                ],
                [
                    {
                        "text": "🇮🇷 سرویس آی‌پی ایران",
                        "callback_data": f"purchase_category_{SubscriptionPlan.ServiceCategory.IRAN_IP}",
                    },
                    {"text": "🔥 پیشنهاد ویژه", "callback_data": "purchase_special"},
                ],
                [{"text": "📖 راهنمای سرویس‌ها", "callback_data": "service_guide"}],
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
            ]
        )
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
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": "plan_selection", "service_category": category, "special_offers": False},
        )
        text, keyboard = await self.get_plans(
            user, category=category, back_callback="purchase_subscription"
        )
        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_special_offers(self, callback: types.CallbackQuery):
        """Show non-expired plans explicitly marked as special offers."""
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": "plan_selection", "special_offers": True, "service_category": None},
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

        lines = ["📖 <b>راهنمای سرویس‌ها</b>"]
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
                [{"text": "🔥 پیشنهاد ویژه", "callback_data": "purchase_special"}],
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

        state = await self.get_user_state(user)
        state_data = state.state_data or {}
        if state_data.get("special_offers"):
            back_callback = "purchase_special"
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

        text = f"""
📋 جزئیات پلن {plan.name}

🏷️ دسته: {self.CATEGORY_LABELS.get(plan.service_category, plan.get_service_category_display())}
💰 قیمت: {self.format_price(plan.price, plan.currency)}
"""

        if plan.discounted_price < plan.price:
            text += f"🔥 تخفیف: {plan.discount_percentage:g}%\n"
            text += f"💵 قیمت نهایی: {self.format_price(plan.discounted_price, plan.currency)}\n"

        text += "\n📊 مشخصات:\n"

        if plan.plan_type == SubscriptionPlan.PlanType.UNLIMITED:
            text += f"⏰ مدت زمان: {self.format_duration(plan.duration_value, plan.duration_unit)}\n"
            text += "📈 ترافیک: نامحدود\n"
        elif plan.plan_type == SubscriptionPlan.PlanType.TRAFFIC_BASED:
            text += f"📊 حجم ترافیک: {self.format_traffic(plan.traffic_limit_gb)}\n"
        elif plan.plan_type == SubscriptionPlan.PlanType.TIME_BASED:
            text += f"⏰ مدت زمان: {self.format_duration(plan.duration_value, plan.duration_unit)}\n"
        elif plan.plan_type == SubscriptionPlan.PlanType.HYBRID:
            text += f"⏰ مدت زمان: {self.format_duration(plan.duration_value, plan.duration_unit)}\n"
            text += f"📊 حجم ترافیک: {self.format_traffic(plan.traffic_limit_gb)}\n"

        text += f"👥 تعداد کاربر: {plan.max_users}\n"

        if plan.features:
            text += "\n✨ ویژگی‌ها:\n"
            for feature in plan.features:
                text += f"• {feature}\n"

        if plan.description:
            text += f"\n📝 توضیحات:\n{plan.description}\n"

        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "🛒 خرید این پلن",
                        "callback_data": f"purchase_plan_{plan_id}",
                    }
                ],
                [
                    {"text": "🎁 خرید هدیه", "callback_data": f"gift_plan_{plan_id}"},
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

        try:
            plan = await SubscriptionPlan.objects.aget(
                id=plan_id, brand=self.brand, is_active=True, is_visible=True
            )
        except SubscriptionPlan.DoesNotExist:
            await callback.answer("❌ پلن یافت نشد.", show_alert=True)
            return

        if purchase_type in ("gift", "other"):
            await self.update_user_state(
                user,
                BotState.StateType.PURCHASE_FLOW,
                {
                    "step": "gift_recipient",
                    "plan_id": plan.pk,
                    "purchase_type": purchase_type,
                },
            )
            await self.send_message_with_keyboard(
                callback.message.chat.id,
                "برای چه کسی می‌خواهید خرید کنید؟ نام کاربری تلگرام گیرنده را با @ بفرستید.\n"
                "گیرنده باید قبلاً ربات را شروع کرده باشد.",
                self.get_back_keyboard("purchase_subscription"),
            )
            await callback.answer()
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

        if not plan.is_active or not plan.is_visible:
            await callback.answer(
                "❌ این پلن دیگر برای خرید فعال نیست.", show_alert=True
            )
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

    async def handle_gift_recipient_message(self, message: types.Message, user, state):
        """Resolve a same-brand Telegram account before creating a gift order."""
        data = state.state_data or {}
        plan_id = data.get("plan_id")
        username = (message.text or "").strip().lstrip("@").strip()
        if not username or not plan_id:
            await message.reply("نام کاربری معتبر را با @ بفرستید.")
            return
        try:
            plan = await SubscriptionPlan.objects.aget(
                pk=plan_id,
                brand=self.brand,
                is_active=True,
                is_visible=True,
            )
            recipient = await User.objects.aget(
                brand=self.brand,
                username__iexact=username,
                is_active=True,
            )
        except (SubscriptionPlan.DoesNotExist, User.DoesNotExist):
            await message.reply(
                "گیرنده پیدا نشد. او باید ابتدا همین ربات را شروع کند؛ سپس نام کاربری را دوباره بفرستید."
            )
            return
        if recipient.pk == user.pk:
            await message.reply("برای خرید اشتراک خودتان از گزینه خرید معمولی استفاده کنید.")
            return

        order = await Order.objects.acreate(
            brand=self.brand,
            user=user,
            recipient=recipient,
            plan=plan,
            order_type=Order.OrderType.GIFT,
            original_price=plan.price,
            discount_amount=plan.price - plan.discounted_price,
            final_price=plan.discounted_price,
            currency=plan.currency,
            status=Order.OrderStatus.PENDING,
        )
        if order.final_price <= 0:
            _, result_text = await self._queue_free_order(order)
            await self.update_user_state(user, BotState.StateType.MAIN_MENU)
            await self.send_message_with_keyboard(
                message.chat.id,
                result_text,
                self.create_keyboard(
                    [[{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}]]
                ),
            )
            return
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": PurchaseStep.PAYMENT_METHOD, "order_id": str(order.order_id)},
        )
        text = (
            f"🎁 گیرنده: {recipient.full_name or recipient.username}\n"
            f"پلن: {plan.name}\n"
            f"مبلغ قابل پرداخت: {self.format_price(order.final_price, order.currency)}\n\n"
            "برای ادامه، روش پرداخت را انتخاب کنید."
        )
        keyboard = await self._payment_methods_keyboard(user, order)
        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

    async def show_payment_methods(
        self, callback: types.CallbackQuery, order: Order, *, answer_callback: bool = True
    ):
        """Show available payment methods"""
        total_paid = await self._confirmed_order_payments(order)
        remaining_due = max(order.final_price - total_paid, 0)
        paid_line = (
            f"مبلغ پرداخت‌شده: {self.format_price(total_paid, order.currency)}\n"
            if total_paid
            else ""
        )
        text = f"""
💳 انتخاب روش پرداخت

سفارش شما: {order.order_number}
پلن: {order.plan.name}
مبلغ کل: {self.format_price(order.final_price, order.currency)}
{paid_line}مبلغ باقی‌مانده: {self.format_price(remaining_due, order.currency)}

لطفاً روش پرداخت خود را انتخاب کنید:
        """
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": PurchaseStep.PAYMENT_METHOD, "order_id": str(order.order_id)},
        )
        keyboard = await self._payment_methods_keyboard(user, order)

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        if answer_callback:
            await callback.answer()

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
            keyboard_buttons.append(
                [
                    {
                        "text": method.name,
                        "callback_data": f"payment_{method.payment_type}_{order.order_id}",
                    }
                ]
            )

        total_paid = await self._confirmed_order_payments(order)
        remaining_due = max(order.final_price - total_paid, 0)
        wallet = await Wallet.objects.filter(
            user=user,
            brand=self.brand,
            is_active=True,
            is_frozen=False,
            currency=order.currency,
            balance__gt=0,
        ).afirst()
        if wallet and remaining_due > 0:
            wallet_amount = min(wallet.balance, remaining_due)
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
            [{"text": "❌ انصراف", "callback_data": "purchase_subscription"}]
        )
        return self.create_keyboard(keyboard_buttons)

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

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer(
            "✅ این سفارش قبلاً پرداخت شده است."
            if result.already_paid
            else "✅ پرداخت با موفقیت انجام شد!"
        )

    async def show_card_transfer_payment(
        self, callback: types.CallbackQuery, order_id: str
    ):
        """Show card transfer payment instructions"""
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            order = await Order.objects.aget(
                order_id=order_id, user=user, brand=self.brand
            )
        except Order.DoesNotExist:
            await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
            return

        cards = []
        async for card in self.brand.payment_cards.filter(is_active=True).order_by(
            "display_order"
        ):
            cards.append(card)

        if not cards:
            await callback.answer("❌ کارت بانکی فعالی موجود نیست.", show_alert=True)
            return

        remaining_due = max(
            order.final_price - await self._confirmed_order_payments(order), 0
        )
        if remaining_due <= 0:
            await callback.answer("✅ این سفارش قبلاً تسویه شده است.", show_alert=True)
            return

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"step": PurchaseStep.CARD_TRANSFER, "order_id": order_id},
        )

        text = f"""
💳 پرداخت با کارت بانکی

سفارش: <code>{order.order_number}</code>

مبلغ قابل پرداخت: {self.format_price(remaining_due, order.currency)}

💳 اطلاعات کارت‌های دریافت:

"""

        keyboard_buttons = []
        for i, card in enumerate(cards):
            text += f"""
🏦 {card.bank_name}
💳 شماره کارت: <code>{card.card_number}</code> 
👤 نام صاحب کارت: {card.cardholder_name}

"""
        keyboard_buttons.append(
            [
                {
                    "text": "واریز کردم",
                    "callback_data": f"payment_done_{order_id}",
                },
                {
                    "text": "لغو فرآیند",
                    "callback_data": f"payment_not_done_{order_id}",
                },
            ]
        )

        text += """
📤 مراحل پرداخت:
1️⃣ مبلغ را به یکی از کارت‌های بالا واریز کنید
2️⃣ عکس رسید واریز را ارسال کنید
3️⃣ بر روی واریز کردم کلیک کرده و منتظر تایید پرداخت باشید
        """

        keyboard_buttons.append(
            [{"text": "🔙 بازگشت", "callback_data": "purchase_subscription"}]
        )
        keyboard = self.create_keyboard(keyboard_buttons)

        await self.edit_message_with_keyboard(
            callback.message.chat.id,
            callback.message.message_id,
            text,
            keyboard,
            parse_mode="html",
        )
        await callback.answer()

    async def create_subscription(self, order: Order):
        """Delegate provisioning to the provider-aware subscription service."""
        from apps.subscriptions.services import provision_order_subscription

        return await provision_order_subscription(order_id=order.pk)

    async def payment_done(self, callback: types.CallbackQuery, order_id: str):
        user, _ = await self.get_or_create_user(callback.from_user)

        try:
            order = await Order.objects.aget(
                order_id=order_id, user=user, brand=self.brand
            )
        except Order.DoesNotExist:
            await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
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
            {"step": PurchaseStep.WAITING_RECEIPT, "order_id": order_id},
        )

        text = f"""
    📤 ارسال رسید پرداخت

    💳 سفارش: {order.order_number}
    💰 مبلغ: {self.format_price(max(order.final_price - await self._confirmed_order_payments(order), 0), order.currency)}

    📸 لطفاً **عکس رسید واریز** را ارسال کنید.

    ⚠️ دقت کنید:
    • عکس واضح باشد
    • مبلغ قابل مشاهده باشد
    • تاریخ تراکنش مشخص باشد
    """
        keyboard = self.create_keyboard(
            [
                [
                    {
                        "text": "❌ لغو پرداخت",
                        "callback_data": f"payment_not_done_{order_id}",
                    }
                ],
            ]
        )

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer("📸 منتظر دریافت عکس رسید هستیم")

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

    async def handle_photo_message(
        self,
        message: types.Message,
        state: BotState,
    ):
        user, _ = await self.get_or_create_user(message.from_user)

        if state.current_state != BotState.StateType.PAYMENT_PROCESS:
            await message.answer("❌ این پیام در این مرحله قابل قبول نیست.")
            return

        sd = state.state_data or {}
        if sd.get("step") != PurchaseStep.WAITING_RECEIPT:
            await message.answer("❌ در حال حاضر منتظر رسید نیستیم.")
            return

        order_id = sd.get("order_id")
        if not order_id:
            await message.answer("❌ اطلاعات سفارش ناقص است.")
            return

        if not message.photo:
            await message.answer("❌ لطفاً فقط عکس رسید را ارسال کنید.")
            return

        try:
            order = await Order.objects.aget(
                order_id=order_id, user=user, brand=self.brand
            )
        except Order.DoesNotExist:
            await message.answer("❌ سفارش معتبر نیست.")
            return

        if order.status not in (
            Order.OrderStatus.PENDING,
            Order.OrderStatus.AWAITING_PAYMENT,
        ):
            await message.answer("❌ این سفارش دیگر قابل پرداخت نیست.")
            return

        existing = await Payment.objects.filter(
            order=order,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            status__in=[
                Payment.PaymentStatus.PENDING,
                Payment.PaymentStatus.CONFIRMED,
            ],
        ).afirst()
        if existing:
            await message.answer("⏳ رسید قبلاً دریافت شده و در حال بررسی است.")
            return

        remaining_due = max(
            order.final_price - await self._confirmed_order_payments(order), 0
        )
        if remaining_due <= 0:
            await message.answer("✅ این سفارش قبلاً تسویه شده است.")
            return

        photo = message.photo[-1]
        file_id = photo.file_id

        payment = await Payment.objects.acreate(
            order=order,
            brand=self.brand,
            user=user,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            amount=remaining_due,
            currency=order.currency,
            status=Payment.PaymentStatus.PENDING,
            receipt_file=file_id,
        )

        order.status = Order.OrderStatus.PROCESSING
        await order.asave()

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {
                "step": PurchaseStep.UNDER_REVIEW,
                "order_id": order_id,
                "payment_id": str(payment.id),
            },
        )

        text = (
            "✅ رسید دریافت شد\n"
            "⏳ پرداخت شما در صف بررسی توسط ادمین قرار گرفت.\n"
            "به محض تأیید، اشتراک شما فعال خواهد شد."
        )
        keyboard = self.create_keyboard(
            [
                [{"text": "🏠 منوی اصلی", "callback_data": "main_menu"}],
            ]
        )

        await self.send_message_with_keyboard(message.chat.id, text, keyboard)

        notified = await self._notify_admin_receipt(order, payment, file_id)
        if not notified:
            await self.send_message_with_keyboard(
                message.chat.id,
                "⚠️ رسید ذخیره شد، اما اعلان برای ادمین ارسال نشد. لطفاً با پشتیبانی تماس بگیرید.",
                keyboard,
            )

    async def _notify_admin_receipt(
        self, order: Order, payment: Payment, file_id: str
    ) -> bool:
        """ارسال رسید به ادمین برای تأیید/رد"""
        admin_text = (
            f"🧾 رسید جدید\n"
            f"سفارش: {order.order_number}\n"
            f"مبلغ: {self.format_price(payment.amount, order.currency)}\n"
            f"payment_id: {payment.id}"
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

        notified = 0
        async for user in self.get_brand_admin_recipients():
            try:
                await self.bot.send_photo(
                    chat_id=user.telegram_id,
                    photo=file_id,
                    caption=admin_text,
                    reply_markup=admin_kb,
                )
                notified += 1
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
        await payment.asave(update_fields=["status"])

        await callback.message.edit_reply_markup(reply_markup=None)
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
