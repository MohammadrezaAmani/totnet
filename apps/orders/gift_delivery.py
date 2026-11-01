"""Deliver verified gift account details to the purchaser and recipient exactly once."""

import logging
from html import escape

from apps.orders.checkout_links import paid_gift_link
from apps.orders.models import GiftVoucher, Order
from apps.subscriptions.models import Subscription
from apps.subscriptions.presentation import connectix_account_message
from utils.jalali import jalali_date

logger = logging.getLogger(__name__)


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
    if not subscription or not voucher:
        return False
    # Connectix accounts may have username/password but no subscription URL.
    # Do not withhold a successfully provisioned account from the purchaser.
    if not (subscription.subscription_url or (subscription.connectix_username and subscription.connectix_password)):
        return False
    from apps.referrals.tasks import _queue_once

    target = order.user if recipient_id is None else order.recipient
    if target is None or (recipient_id is not None and target.pk != recipient_id):
        return False
    is_buyer = recipient_id is None
    # Buyers receive actual credentials AND the voucher link; recipients
    # receive the exact same canonical account-details template.
    lines = [connectix_account_message(subscription)]
    if is_buyer:
        lines.extend(("", "🎁 <b>لینک ورود مخصوص گیرنده</b>"))
        gift_link = paid_gift_link(voucher)
        if gift_link:
            lines.extend(("", "🔗 لینک اختصاصی ورود کاربر نهایی:", escape(gift_link),
                          f"⏳ مهلت ورود: {jalali_date(voucher.expires_at, time=True)}",
                          "ابتدا گیرنده باید وارد ربات شود و پروفایل خود را تکمیل کند. "
                          "پس از آن اشتراک به نام او ثبت می‌شود."))
        else:
            logger.error("Gift %s ready but bot username is not valid", order.pk)

    buttons = (
        [
            [{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions", "style": "success"}],
            [{"text": "📖 آکادمی", "callback_data": "useful_category_install"}],
        ]
        if is_buyer else [
            [{"text": "✏️ نام‌گذاری این اشتراک", "callback_data": f"rename_subscription_{subscription.pk}"}],
            [{"text": "📥 دریافت اکانت / QR", "callback_data": f"get_config_{subscription.pk}"}],
            [{"text": "📱 اشتراک‌های من", "callback_data": "my_subscriptions"}],
            [{"text": "📥 دانلود برنامه", "callback_data": "useful_category_downloads"}],
            [{"text": "📖 آکادمی", "callback_data": "useful_category_install"}],
        ]
    )
    return _queue_once(
        user=target,
        brand_id=order.brand_id,
        notification_type="custom",
        dedupe_key=f"gift-details-ready:{voucher.pk}:{target.pk}",
        text="\n".join(line for line in lines if line),
        buttons_data=buttons,
        metadata={"gift_voucher_id": voucher.pk, "event": "gift_handover"},
    )
