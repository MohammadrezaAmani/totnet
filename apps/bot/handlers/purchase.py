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


class PurchaseStep:
    PLAN_SELECTION = "plan_selection"
    PLAN_DETAILS = "plan_details"
    PAYMENT_METHOD = "payment_method"
    CARD_TRANSFER = "card_transfer"
    WAITING_RECEIPT = "waiting_receipt"
    UNDER_REVIEW = "under_review"


class PurchaseHandler(BaseHandler):
    """Handle subscription purchase flow"""

    async def show_subscription_plans(self, callback: types.CallbackQuery):
        """Show available subscription plans"""
        user, _ = await self.get_or_create_user(callback.from_user)
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
        user, _ = await self.get_or_create_user(callback.from_user)

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
        user, _ = await self.get_or_create_user(callback.from_user)

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
            status=Order.OrderStatus.PENDING,
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
        user, _ = await self.get_or_create_user(callback.from_user)
        await self.update_user_state(
            user,
            BotState.StateType.PURCHASE_FLOW,
            {"step": PurchaseStep.PAYMENT_METHOD, "order_id": str(order.order_id)},
        )
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

        user, _ = await self.get_or_create_user(callback.from_user)
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
        user, _ = await self.get_or_create_user(callback.from_user)

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

        await self.update_user_state(
            user,
            BotState.StateType.PAYMENT_PROCESS,
            {"step": PurchaseStep.CARD_TRANSFER, "order_id": order_id},
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
            callback.message.chat.id, callback.message.message_id, text, keyboard
        )
        await callback.answer()

    async def create_subscription(self, order: Order):
        """Create VPN subscription after successful payment with Hiddify integration"""

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
            status=Subscription.SubscriptionStatus.PENDING,
        )

        if vpn_provider.provider_type == VPNProvider.ProviderType.HIDDIFY:
            try:
                from apps.vpn_providers.services.hiddify import (
                    HiddifyLanguage,
                    HiddifyProvider,
                    HiddifyUser,
                    HiddifyUserMode,
                )

                hiddify = HiddifyProvider(
                    base_url=vpn_provider.base_url,
                    api_key=vpn_provider.api_key,
                    proxy_path=vpn_provider.proxy_path or "",
                    public_api_key=vpn_provider.public_api_key,
                )

                hiddify_user = HiddifyUser(
                    name=f"user_{order.user.telegram_id}_{subscription.id}",
                    telegram_id=order.user.telegram_id,
                    usage_limit_GB=order.plan.traffic_limit_gb,
                    package_days=(end_date - start_date).days if end_date else None,
                    start_date=start_date.date(),
                    mode=HiddifyUserMode.NO_RESET,
                    enable=True,
                    is_active=True,
                    lang=HiddifyLanguage.FA,
                    comment=f"Subscription {subscription.subscription_id}",
                )

                created_user = await hiddify.create_hiddify_user(hiddify_user)

                if created_user and created_user.uuid:
                    subscription.connection_configs = {
                        "secret_uuid": str(created_user.uuid),
                        "hiddify_uuid": str(created_user.uuid),
                        "created_at": timezone.now().isoformat(),
                    }
                    subscription.vpn_user_email = (
                        f"{created_user.uuid}@{self.brand.slug}.vpn"
                    )
                    subscription.status = Subscription.SubscriptionStatus.ACTIVE
                    await subscription.asave()

                    vpn_provider.current_users += 1
                    vpn_provider.total_subscriptions += 1
                    await vpn_provider.asave()

                    logger.info(
                        f"Created Hiddify user: {created_user.uuid} for subscription {subscription.subscription_id}"
                    )
                else:
                    logger.error(
                        f"Failed to create Hiddify user for subscription {subscription.subscription_id}"
                    )
                    subscription.status = Subscription.SubscriptionStatus.SUSPENDED
                    await subscription.asave()

                await hiddify.close()

            except Exception as e:
                logger.error(f"Error creating Hiddify user: {e}")
                subscription.status = Subscription.SubscriptionStatus.SUSPENDED
                await subscription.asave()
        else:
            subscription.status = Subscription.SubscriptionStatus.ACTIVE
            await subscription.asave()

        order.status = Order.OrderStatus.COMPLETED
        await order.asave()

        logger.info(f"Subscription created: {subscription.subscription_id}")
        return subscription

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

        if order.status != Order.OrderStatus.PENDING:
            await callback.answer("❌ این سفارش قابل ادامه نیست.", show_alert=True)
            return

        dup = await Payment.objects.filter(
            order=order,
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
    💰 مبلغ: {self.format_price(order.final_price, order.currency)}

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

        if order.status != Order.OrderStatus.PENDING:
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

        photo = message.photo[-1]
        file_id = photo.file_id

        payment = await Payment.objects.acreate(
            order=order,
            brand=self.brand,
            user=user,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            amount=order.final_price,
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

        await self._notify_admin_receipt(order, payment, file_id)

    async def _notify_admin_receipt(self, order: Order, payment: Payment, file_id: str):
        """ارسال رسید به ادمین برای تأیید/رد"""
        admin_text = (
            f"🧾 رسید جدید\n"
            f"سفارش: {order.order_number}\n"
            f"مبلغ: {self.format_price(order.final_price, order.currency)}\n"
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

        admin_chat_id = self.brand.admin_chat_id
        if admin_chat_id:
            try:
                await self.bot.send_photo(
                    chat_id=admin_chat_id,
                    photo=file_id,
                    caption=admin_text,
                    reply_markup=admin_kb,
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
