"""
Purchase Handler for Multi-Tenant VPN Bot
Handles subscription purchases, plan selection, and payment processing
"""

import logging
import uuid
from datetime import timedelta

from aiogram import types
from django.utils import timezone

from apps.bot.models import BotState
from apps.orders.models import Order, Payment, WalletTransaction
from apps.subscriptions.models import Subscription, SubscriptionPlan
from apps.vpn_providers.models import VPNProvider

from .base import BaseHandler

logger = logging.getLogger(__name__)


class PurchaseHandler(BaseHandler):
    """Handle subscription purchase flow"""

    async def show_subscription_plans(self, callback: types.CallbackQuery):
        """Show available subscription plans"""
        user = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user, BotState.StateType.PURCHASE_FLOW, {"step": "plan_selection"}
        )

        plans = []
        async for plan in SubscriptionPlan.objects.filter(
            brand=self.brand, is_active=True, is_visible=True
        ).order_by("display_order", "price"):
            plans.append(plan)

        if not plans:
            text = "❌ در حال حاضر پلن فعالی موجود نیست."
            keyboard = self.get_back_keyboard("main_menu")
        else:
            text = f"""
🛒 پلن‌های اشتراک {self.brand.name}

لطفاً یکی از پلن‌های زیر را انتخاب کنید:
            """

            keyboard_buttons = []
            for plan in plans:
                plan_text = f"{plan.name}"
                if plan.plan_type == SubscriptionPlan.PlanType.UNLIMITED:
                    plan_text += (
                        f" - {self.format_duration(plan.duration_value)} - نامحدود"
                    )
                elif plan.plan_type == SubscriptionPlan.PlanType.TRAFFIC_BASED:
                    plan_text += f" - {self.format_traffic(plan.traffic_limit_gb)}"
                elif plan.plan_type == SubscriptionPlan.PlanType.TIME_BASED:
                    plan_text += f" - {self.format_duration(plan.duration_value)}"
                elif plan.plan_type == SubscriptionPlan.PlanType.HYBRID:
                    plan_text += f" - {self.format_duration(plan.duration_value)} - {self.format_traffic(plan.traffic_limit_gb)}"

                plan_text += (
                    f"\n💰 {self.format_price(plan.discounted_price, plan.currency)}"
                )

                if plan.discount_percentage > 0:
                    plan_text += f" 🔥 {plan.discount_percentage}% تخفیف"

                keyboard_buttons.append(
                    [{"text": plan_text, "callback_data": f"select_plan_{plan.id}"}]
                )

            keyboard_buttons.append(
                [{"text": "🔙 بازگشت", "callback_data": "main_menu"}]
            )
            keyboard = self.create_keyboard(keyboard_buttons)

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def show_plan_details(self, callback: types.CallbackQuery, plan_id: int):
        """Show detailed plan information"""
        user = await self.get_or_create_user(callback.from_user)

        try:
            plan = await SubscriptionPlan.objects.aget(id=plan_id, brand=self.brand)
        except SubscriptionPlan.DoesNotExist:
            await callback.answer("❌ پلن یافت نشد.", show_alert=True)
            return

        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": "plan_details", "plan_id": plan_id},
        )

        text = f"""
📋 جزئیات پلن {plan.name}

💰 قیمت: {self.format_price(plan.price, plan.currency)}
"""

        if plan.discount_percentage > 0:
            text += f"🔥 تخفیف: {plan.discount_percentage}%\n"
            text += f"💵 قیمت نهایی: {self.format_price(plan.discounted_price, plan.currency)}\n"

        text += "\n📊 مشخصات:\n"

        if plan.plan_type == SubscriptionPlan.PlanType.UNLIMITED:
            text += f"⏰ مدت زمان: {self.format_duration(plan.duration_value)}\n"
            text += "📈 ترافیک: نامحدود\n"
        elif plan.plan_type == SubscriptionPlan.PlanType.TRAFFIC_BASED:
            text += f"📊 حجم ترافیک: {self.format_traffic(plan.traffic_limit_gb)}\n"
        elif plan.plan_type == SubscriptionPlan.PlanType.TIME_BASED:
            text += f"⏰ مدت زمان: {self.format_duration(plan.duration_value)}\n"
        elif plan.plan_type == SubscriptionPlan.PlanType.HYBRID:
            text += f"⏰ مدت زمان: {self.format_duration(plan.duration_value)}\n"
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
                        "callback_data": "purchase_subscription",
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
        user = await self.get_or_create_user(callback.from_user)

        try:
            plan = await SubscriptionPlan.objects.aget(id=plan_id, brand=self.brand)
        except SubscriptionPlan.DoesNotExist:
            await callback.answer("❌ پلن یافت نشد.", show_alert=True)
            return

        order = await Order.objects.acreate(
            brand=self.brand,
            user=user,
            plan=plan,
            order_type=(
                Order.OrderType.GIFT
                if purchase_type == "gift"
                else Order.OrderType.NEW_SUBSCRIPTION
            ),
            original_price=plan.price,
            discount_amount=plan.price - plan.discounted_price,
            final_price=plan.discounted_price,
            currency=plan.currency,
        )

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

    async def show_payment_methods(self, callback: types.CallbackQuery, order: Order):
        """Show available payment methods"""
        text = f"""
💳 انتخاب روش پرداخت

سفارش شما: {order.order_number}
پلن: {order.plan.name}
مبلغ قابل پرداخت: {self.format_price(order.final_price, order.currency)}

لطفاً روش پرداخت خود را انتخاب کنید:
        """

        keyboard_buttons = []

        payment_methods = []
        async for method in self.brand.payment_methods.filter(is_enabled=True).order_by(
            "display_order"
        ):
            payment_methods.append(method)

        for method in payment_methods:
            keyboard_buttons.append(
                [
                    {
                        "text": f"{method.name}",
                        "callback_data": f"payment_{method.payment_type}_{order.order_id}",
                    }
                ]
            )

        user = await self.get_or_create_user(callback.from_user)
        try:
            wallet = await WalletTransaction.objects.filter(
                wallet__user=user, wallet__brand=self.brand
            ).afirst()
            if wallet:
                wallet_obj = wallet.wallet
                if wallet_obj.balance >= order.final_price:
                    keyboard_buttons.insert(
                        0,
                        [
                            {
                                "text": f"💰 پرداخت از کیف پول ({self.format_price(wallet_obj.balance, order.currency)})",
                                "callback_data": f"payment_wallet_{order.order_id}",
                            }
                        ],
                    )
        except Exception as _:
            pass

        keyboard_buttons.append(
            [{"text": "❌ انصراف", "callback_data": "purchase_subscription"}]
        )
        keyboard = self.create_keyboard(keyboard_buttons)

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def process_wallet_payment(
        self, callback: types.CallbackQuery, order_id: str
    ):
        """Process wallet payment"""
        user = await self.get_or_create_user(callback.from_user)

        try:
            order = await Order.objects.aget(
                order_id=order_id, user=user, brand=self.brand
            )
        except Order.DoesNotExist:
            await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
            return

        from apps.orders.models import Wallet

        try:
            wallet = await Wallet.objects.aget(user=user, brand=self.brand)
            if wallet.balance < order.final_price:
                await callback.answer("❌ موجودی کیف پول کافی نیست.", show_alert=True)
                return
        except Wallet.DoesNotExist:
            await callback.answer("❌ کیف پول یافت نشد.", show_alert=True)
            return

        _ = await Payment.objects.acreate(
            order=order,
            brand=self.brand,
            user=user,
            payment_method=Payment.PaymentMethod.WALLET,
            amount=order.final_price,
            currency=order.currency,
            status=Payment.PaymentStatus.CONFIRMED,
        )

        wallet.balance -= order.final_price
        await wallet.asave()

        await WalletTransaction.objects.acreate(
            wallet=wallet,
            transaction_type=WalletTransaction.TransactionType.PAYMENT,
            amount=-order.final_price,
            balance_before=wallet.balance + order.final_price,
            balance_after=wallet.balance,
            reference_id=str(order.order_id),
            description=f"پرداخت سفارش {order.order_number}",
        )

        order.status = Order.OrderStatus.PAID
        await order.asave()

        await self.create_subscription(order)

        text = f"""
✅ پرداخت موفق!

سفارش شما با موفقیت پردازش شد.
شماره سفارش: {order.order_number}
مبلغ پرداختی: {self.format_price(order.final_price, order.currency)}

اشتراک شما به زودی فعال خواهد شد.
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
        await callback.answer("✅ پرداخت با موفقیت انجام شد!")

    async def show_card_transfer_payment(
        self, callback: types.CallbackQuery, order_id: str
    ):
        """Show card transfer payment instructions"""
        user = await self.get_or_create_user(callback.from_user)

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

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"step": "card_transfer", "order_id": order_id},
        )

        text = f"""
💳 پرداخت با کارت بانکی

سفارش: {order.order_number}
مبلغ قابل پرداخت: {self.format_price(order.final_price, order.currency)}

💳 اطلاعات کارت‌های دریافت:

"""

        keyboard_buttons = []
        for i, card in enumerate(cards):
            text += f"""
🏦 {card.bank_name}
💳 شماره کارت: `{card.card_number}`
👤 نام صاحب کارت: {card.cardholder_name}

"""
            keyboard_buttons.append(
                [
                    {
                        "text": f"💳 انتخاب کارت {card.bank_name}",
                        "callback_data": f"select_card_{card.id}_{order_id}",
                    }
                ]
            )

        text += """
📤 مراحل پرداخت:
1️⃣ مبلغ را به یکی از کارت‌های بالا واریز کنید
2️⃣ عکس رسید واریز را ارسال کنید
3️⃣ منتظر تایید پرداخت باشید
        """

        keyboard_buttons.append(
            [{"text": "🔙 بازگشت", "callback_data": f"payment_methods_{order_id}"}]
        )
        keyboard = self.create_keyboard(keyboard_buttons)

        await self.edit_message_with_keyboard(
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def create_subscription(self, order: Order):
        """Create VPN subscription after successful payment"""

        vpn_provider = (
            await VPNProvider.objects.filter(
                brand=self.brand, status=VPNProvider.ProviderStatus.ACTIVE
            )
            .order_by("priority", "current_users")
            .afirst()
        )

        if not vpn_provider:
            logger.error(f"No available VPN provider for brand {self.brand.id}")
            return

        vpn_email = f"user_{order.user.id}_{uuid.uuid4().hex[:8]}@{self.brand.slug}.vpn"

        start_date = timezone.now()
        end_date = None
        if order.plan.duration_value:
            if order.plan.duration_unit == SubscriptionPlan.DurationUnit.DAYS:
                end_date = start_date + timedelta(days=order.plan.duration_value)
            elif order.plan.duration_unit == SubscriptionPlan.DurationUnit.MONTHS:
                end_date = start_date + timedelta(days=order.plan.duration_value * 30)
            elif order.plan.duration_unit == SubscriptionPlan.DurationUnit.YEARS:
                end_date = start_date + timedelta(days=order.plan.duration_value * 365)

        subscription = await Subscription.objects.acreate(
            brand=self.brand,
            user=order.user,
            plan=order.plan,
            order=order,
            vpn_provider=vpn_provider,
            vpn_user_email=vpn_email,
            owner=order.recipient or order.user,
            starts_at=start_date,
            expires_at=end_date,
            traffic_limit_gb=order.plan.traffic_limit_gb,
            status=Subscription.SubscriptionStatus.ACTIVE,
        )

        order.status = Order.OrderStatus.COMPLETED
        await order.asave()

        logger.info(f"Subscription created: {subscription.subscription_id}")
        return subscription
