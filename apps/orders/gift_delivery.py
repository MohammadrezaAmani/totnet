"""Deliver verified gift account details to the purchaser and recipient exactly once."""

import logging
from html import escape

from apps.orders.checkout_links import paid_gift_link
from apps.orders.models import GiftVoucher, Order
from apps.subscriptions.models import Subscription
from apps.subscriptions.presentation import compact_plan_specs
from utils.jalali import jalali_date

logger = logging.getLogger(__name__)


SERVICE_NAMES = {"normal": "نرمال", "royal": "رویال", "iran_ip": "آی‌پی ایران"}


def queue_ready_gift_details(*, order_id: int, recipient_id: int | None = None) -> bool:
    """Only send real provider data; never mistake a paid voucher for a ready VPN account.

    The purchaser receives the redeem link and credentials. After claim, the
    recipient gets the same account details without the now-consumed gift link.
    """
    order = Order.objects.select_related("user", "plan", "brand").get(pk=order_id)
    if order.order_type != Order.OrderType.GIFT:
        return False
    subscription = Subscription.objects.filter(order=order, status=Subscription.SubscriptionStatus.ACTIVE).first()
    voucher = GiftVoucher.objects.filter(order=order).first()
    if not subscription or not voucher or not subscription.subscription_url:
        return False
    from apps.referrals.tasks import _queue_once

    target = order.user if recipient_id is None else order.recipient
    if target is None or (recipient_id is not None and target.pk != recipient_id):
        return False
    is_buyer = recipient_id is None
    service = SERVICE_NAMES.get(order.plan.service_category, order.plan.get_service_category_display())
    lines = [
        "🎁 <b>اشتراک خریداری‌شده برای دیگران آماده است.</b>" if is_buyer else "🎁 <b>اطلاعات اشتراک شما</b>",
        "",
        f"🩺 نوع سرویس: {escape(service)}",
        f"📋 نوع اشتراک: {escape(compact_plan_specs(order.plan))}",
    ]
    if subscription.connectix_username:
        lines.append(f"👤 نام کاربری: <code>{escape(subscription.connectix_username)}</code>")
    if subscription.connectix_password:
        lines.append(f"🔑 رمز عبور: <code>{escape(subscription.connectix_password)}</code>")
    lines.extend(("", "🔗 لینک اشتراک:", f"<code>{escape(subscription.subscription_url)}</code>"))
    if is_buyer:
        gift_link = paid_gift_link(voucher)
        if gift_link:
            lines.extend(("", "🎁 لینک ثبت اشتراک توسط کاربر نهایی:", escape(gift_link),
                          f"⏳ مهلت ثبت: {jalali_date(voucher.expires_at, time=True)}"))
        else:
            logger.error("Gift %s ready but bot username is not valid", order.pk)

    lines.append("با این نام کاربری و رمز عبور وارد برنامه کانکتیکس شوید و به اشتراکتان وصل شوید." if subscription.connectix_username and subscription.connectix_password else "")
    buttons = [
        [{"text": "📥 دریافت اکانت / QR", "callback_data": f"get_config_{subscription.pk}"}],
        [{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}],
        [{"text": "📥 دانلود برنامه", "callback_data": "useful_category_downloads"}],
        [{"text": "📖 آکادمی", "callback_data": "useful_category_install"}],
    ]
    return _queue_once(
        user=target,
        brand_id=order.brand_id,
        notification_type="custom",
        dedupe_key=f"gift-details-ready:{voucher.pk}:{target.pk}",
        text="\n".join(line for line in lines if line),
        buttons_data=buttons,
        metadata={"gift_voucher_id": voucher.pk, "event": "gift_handover"},
    )
