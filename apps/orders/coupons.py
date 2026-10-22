"""Atomic, order-scoped discount codes for checkout.

Wallet-top-up coupons use a separate endpoint. Never mutate a bank quote after
one was issued; a user must apply a checkout code first.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.orders.models import Coupon, CouponUsage, Order, Payment
from apps.subscriptions.policy import temporary_duration_blocked


class PurchaseCouponError(ValueError):
    pass


@transaction.atomic
def apply_purchase_coupon(*, brand_id: int, user_id: int, order_id: str, code: str):
    if not code or len(code.strip()) > 50:
        raise PurchaseCouponError("کد معتبر نیست.")
    try:
        order = Order.objects.select_for_update().select_related("plan").get(
            brand_id=brand_id, user_id=user_id, order_id=order_id,
        )
    except Order.DoesNotExist as exc:
        raise PurchaseCouponError("سفارش یافت نشد.") from exc
    if order.status not in {Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT}:
        raise PurchaseCouponError("این سفارش دیگر قابل تخفیف نیست.")
    if temporary_duration_blocked(order.plan):
        raise PurchaseCouponError("این مدت اشتراک موقتاً غیرفعال است.")
    if order.coupon_code or CouponUsage.objects.filter(order=order).exists():
        raise PurchaseCouponError("برای این سفارش قبلاً کد تخفیف ثبت شده است.")
    # Quotes encode a fixed payment amount. A discount after reservation would
    # make the SMS matcher ambiguous, so forbid it even before a receipt arrives.
    if hasattr(order, "card_quote") and order.card_quote is not None:
        raise PurchaseCouponError("برای این سفارش مبلغ کارت صادر شده؛ ابتدا سفارش تازه ثبت کنید.")
    try:
        coupon = Coupon.objects.select_for_update().get(
            brand_id=brand_id, code__iexact=code.strip(), is_active=True
        )
    except Coupon.DoesNotExist as exc:
        raise PurchaseCouponError("کد تخفیف پیدا نشد یا غیرفعال است.") from exc
    now = timezone.now()
    if coupon.valid_from > now or coupon.valid_until < now:
        raise PurchaseCouponError("مهلت استفاده از این کد تمام شده است.")
    if coupon.coupon_type not in {Coupon.CouponType.FIXED_AMOUNT, Coupon.CouponType.PERCENTAGE}:
        raise PurchaseCouponError("این کد برای این نوع سفارش تعریف نشده است.")
    if coupon.minimum_order_amount and order.final_price < coupon.minimum_order_amount:
        raise PurchaseCouponError("مبلغ سفارش کمتر از حداقل کد تخفیف است.")
    if coupon.applicable_plans.exists() and not coupon.applicable_plans.filter(pk=order.plan_id).exists():
        raise PurchaseCouponError("کد برای این اشتراک معتبر نیست.")
    if coupon.new_users_only and Order.objects.filter(
        user_id=user_id, brand_id=brand_id,
        status__in=[Order.OrderStatus.PAID, Order.OrderStatus.PROCESSING, Order.OrderStatus.COMPLETED],
    ).exclude(pk=order.pk).exists():
        raise PurchaseCouponError("این کد فقط برای اولین خرید است.")
    used = CouponUsage.objects.filter(coupon=coupon).count()
    if coupon.max_uses is not None and used >= coupon.max_uses:
        raise PurchaseCouponError("ظرفیت این کد تمام شده است.")
    if CouponUsage.objects.filter(coupon=coupon, user_id=user_id).count() >= coupon.max_uses_per_user:
        raise PurchaseCouponError("سقف استفاده از این کد برای شما تکمیل شده است.")
    if coupon.coupon_type == Coupon.CouponType.PERCENTAGE:
        if not 0 < coupon.discount_value <= 100:
            raise PurchaseCouponError("تنظیم درصد این کد نامعتبر است.")
        discount = order.final_price * coupon.discount_value / Decimal(100)
    else:
        discount = coupon.discount_value
    if coupon.max_discount_amount:
        discount = min(discount, coupon.max_discount_amount)
    precision = Decimal("1") if order.currency in {"T", "IRT", "IRR"} else Decimal("0.01")
    discount = min(order.final_price, discount).quantize(precision, rounding=ROUND_HALF_UP)
    if discount <= 0:
        raise PurchaseCouponError("این کد مبلغ قابل استفاده ندارد.")
    confirmed_total = order.payments.filter(status=Payment.PaymentStatus.CONFIRMED).aggregate(
        total=Sum("amount")
    )["total"] or Decimal(0)
    if order.final_price - discount < max(confirmed_total, precision):
        raise PurchaseCouponError("این تخفیف مبلغ را کامل پوشش می‌دهد یا از پرداخت قبلی کمتر می‌کند؛ برای بررسی با پشتیبانی تماس بگیرید.")
    order.coupon_code = coupon.code
    order.coupon_discount = discount
    order.discount_amount += discount
    order.final_price -= discount
    order.save(update_fields=["coupon_code", "coupon_discount", "discount_amount", "final_price", "updated_at"])
    CouponUsage.objects.create(coupon=coupon, user_id=user_id, order=order, discount_amount=discount)
    coupon.current_uses = F("current_uses") + 1
    coupon.save(update_fields=["current_uses", "updated_at"])
    return order, discount
