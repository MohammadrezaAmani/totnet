"""Bank-transfer quotes and recipient-claimed gift links.

An amount suffix is only for *matching*. An SMS is not bank settlement proof.
These services never auto-confirm a payment from unverified phone data.
"""
from __future__ import annotations

import secrets
import re
from datetime import timedelta
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from apps.accounts.models import User
from apps.orders.models import Order, Payment, PaymentCard, GiftVoucher, CardTransferQuote


class QuoteUnavailable(ValueError):
    pass


@transaction.atomic
def reserve_card_quote(*, order_id: str, user_id: int, brand_id: int, card_id: int):
    order = Order.objects.select_for_update().get(
        order_id=order_id, user_id=user_id, brand_id=brand_id,
        status__in=[Order.OrderStatus.PENDING, Order.OrderStatus.AWAITING_PAYMENT],
    )
    card = PaymentCard.objects.select_for_update().get(pk=card_id, brand_id=brand_id, is_active=True)
    confirmed = Payment.objects.filter(order=order, status=Payment.PaymentStatus.CONFIRMED).aggregate(
        amount=Sum('amount'))['amount'] or Decimal('0')
    due = max(order.final_price - confirmed, Decimal('0'))
    if due <= 0:
        raise QuoteUnavailable('سفارش قبلاً تسویه شده است.')
    now = timezone.now()
    # Release only after the bank-reconciliation safety window, not simply
    # when the UI's 24-hour transfer window expires.
    CardTransferQuote.objects.filter(
        card=card, reserved=True, release_after__lte=now,
    ).update(reserved=False)
    previous = CardTransferQuote.objects.select_for_update().filter(order=order).first()
    if previous and previous.card_id == card.pk and previous.base_amount == due:
        if previous.release_after <= now:
            raise QuoteUnavailable('مهلت واریز این سفارش تمام شده؛ سفارش تازه ثبت کنید.')
        if previous.expires_at <= now:
            previous.expires_at = min(now + timedelta(hours=24), previous.release_after)
            previous.save(update_fields=['expires_at'])
        return previous
    if previous:
        # The amount remains reserved through the bank-message safety window.
        # A changed amount/card uses a new quote; retaining the historical record
        # requires manual investigation rather than silently overwriting it.
        raise QuoteUnavailable('مبلغ یا کارت این سفارش تغییر کرده؛ برای بررسی با پشتیبانی تماس بگیرید.')
    expected = due
    if card.unique_amount_enabled:
        if order.currency not in ('T', 'IRT', 'IRR'):
            raise QuoteUnavailable('شناسه مبلغ برای ارز این سفارش قابل استفاده نیست.')
        limit = min(max(card.unique_amount_max_extra, 1), 99)
        unit = Decimal('10') if order.currency == 'IRR' else Decimal('1')
        for extra in range(1, limit + 1):
            candidate = due + unit * extra
            # Reservation is unique across this card's entire history, including expired quotes.
            if not CardTransferQuote.objects.filter(card=card, expected_amount=candidate).exists():
                expected = candidate
                break
        else:
            raise QuoteUnavailable('در حال حاضر مبلغ یکتای جدید در دسترس نیست؛ کمی بعد تلاش کنید.')
    try:
        with transaction.atomic():
            return CardTransferQuote.objects.create(
                order=order, card=card, base_amount=due,
                expected_amount=expected, unique_suffix=card.unique_amount_enabled,
                reserved=card.unique_amount_enabled,
                expires_at=now + timedelta(hours=24),
                release_after=now + timedelta(days=7),
            )
    except IntegrityError as exc:
        raise QuoteUnavailable('مبلغ پرداخت دیگری هم‌زمان رزرو شد؛ دوباره تلاش کنید.') from exc


def paid_gift_link(voucher: GiftVoucher) -> str | None:
    username = (voucher.order.brand.bot_username or '').lstrip('@').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', username):
        return None
    return f'https://t.me/{username}?start=gift_{voucher.token}'


@transaction.atomic
def issue_paid_gift(*, order_id: int) -> GiftVoucher | None:
    order = Order.objects.select_for_update().select_related('brand').get(pk=order_id)
    if order.order_type != Order.OrderType.GIFT or order.recipient_id:
        return None
    if order.status not in (Order.OrderStatus.PAID, Order.OrderStatus.PROCESSING, Order.OrderStatus.COMPLETED):
        return None
    paid = Payment.objects.filter(order=order, status=Payment.PaymentStatus.CONFIRMED).aggregate(
        total=Sum('amount'))['total'] or Decimal('0')
    if paid < order.final_price:
        return None
    existing = GiftVoucher.objects.filter(order=order).first()
    if existing:
        return existing
    # Token is a bearer credential, not a sequential coupon code.
    hours = min(max(order.brand.gift_expiry_hours, 1), 720)
    return GiftVoucher.objects.create(
        order=order, token=secrets.token_urlsafe(24),
        expires_at=timezone.now() + timedelta(hours=hours),
    )


class GiftClaimError(ValueError):
    pass


@transaction.atomic
def claim_gift(*, token: str, brand_id: int, user_id: int, newly_registered: bool):
    """Claim once, atomically; referral attribution is ONLY for new joiners."""
    if not token or len(token) > 64:
        raise GiftClaimError('کد هدیه معتبر نیست.')
    gift = (GiftVoucher.objects.select_for_update().select_related('order', 'order__plan')
            .filter(token=token, order__brand_id=brand_id).first())
    now = timezone.now()
    if not gift or (gift.expires_at <= now and not gift.claimed_by_id):
        raise GiftClaimError('لینک هدیه نامعتبر یا منقضی شده است.')
    if gift.claimed_by_id and gift.claimed_by_id != user_id:
        raise GiftClaimError('این کد هدیه قبلاً استفاده شده است.')
    order = Order.objects.select_for_update().get(pk=gift.order_id)
    if order.status not in (Order.OrderStatus.PAID, Order.OrderStatus.PROCESSING, Order.OrderStatus.COMPLETED):
        raise GiftClaimError('پرداخت این هدیه هنوز تأیید نشده است.')
    if order.user_id == user_id:
        raise GiftClaimError('نمی‌توانید هدیه خودتان را دریافت کنید.')
    user = User.objects.select_for_update().get(pk=user_id, brand_id=brand_id, is_active=True)
    if order.recipient_id:
        if order.recipient_id == user_id:
            return order
        raise GiftClaimError('این هدیه قبلاً دریافت شده است.')
    if not gift.claimed_by_id:
        gift.claimed_by = user
        gift.claimed_at = now
        gift.new_member_at_claim = bool(newly_registered)
        gift.save(update_fields=['claimed_by', 'claimed_at', 'new_member_at_claim'])
    # Reserve the voucher for this Telegram account, but do not transfer the
    # paid VPN credentials until its initial profile is complete.
    from apps.accounts.models import UserProfile
    profile = UserProfile.objects.filter(user_id=user_id).first()
    if not (user.full_name and user.phone_number and profile and
            (profile.devices or profile.device_type)):
        return order
    order.recipient = user
    order.recipient_claimed_at = now
    order.save(update_fields=['recipient', 'recipient_claimed_at', 'updated_at'])

    if gift.new_member_at_claim and not user.referred_by_id:
        # Invitation is counted only when this Telegram ID was actually created by
        # this /start. Returning users never acquire a new referral relationship.
        from django.db.models import F
        from apps.referrals.models import Referral, ReferralLink
        if not Referral.objects.filter(referee=user, brand_id=brand_id).exists():
            link, _ = ReferralLink.objects.get_or_create(
                user_id=order.user_id, brand_id=brand_id, is_default=True,
            )
            Referral.objects.create(
                referrer_id=order.user_id, referee=user, brand_id=brand_id,
                referral_link=link, status=Referral.ReferralStatus.PENDING,
            )
            user.referred_by_id = order.user_id
            user.save(update_fields=['referred_by', 'updated_at'])
            User.objects.filter(pk=order.user_id).update(referral_count=F('referral_count') + 1)
            ReferralLink.objects.filter(pk=link.pk).update(conversion_count=F('conversion_count') + 1)
            # Gift entry has its own single invitation notice after activation.
            # Do not emit the ordinary join notice as a duplicate.

    # Reuse the paid gift's account, including any already provisioned remote account.
    from apps.subscriptions.models import Subscription
    from apps.vpn_providers.models import VPNProvider
    subscription = Subscription.objects.select_for_update().filter(order=order).first()
    if subscription:
        subscription.owner = user
        subscription.display_name = (user.full_name or user.username)[:100]
        subscription.save(update_fields=['owner', 'display_name', 'updated_at'])
    else:
        provider = order.plan.vpn_provider if order.plan.vpn_provider_id else VPNProvider.objects.filter(
            brand_id=brand_id, is_default=True, status=VPNProvider.ProviderStatus.ACTIVE
        ).first()
        if not provider or provider.status != VPNProvider.ProviderStatus.ACTIVE:
            raise GiftClaimError('فعال‌سازی این سرویس موقتاً در دسترس نیست؛ بعداً دوباره تلاش کنید.')
        subscription = Subscription.objects.create(
            order=order, brand_id=brand_id, user_id=order.user_id, owner=user,
            plan=order.plan, vpn_provider=provider, is_gift=True,
            starts_at=now, traffic_limit_gb=order.plan.traffic_limit_gb,
            status=Subscription.SubscriptionStatus.PENDING,
        )
    if subscription.status == Subscription.SubscriptionStatus.ACTIVE:
        from apps.orders.gift_delivery import queue_ready_gift_details
        transaction.on_commit(
            lambda order_pk=order.pk, recipient_pk=user.pk:
                queue_ready_gift_details(order_id=order_pk, recipient_id=recipient_pk)
        )
    else:
        from apps.orders.signals import _enqueue_order_provisioning
        transaction.on_commit(
            lambda subscription_pk=subscription.pk: _enqueue_order_provisioning(subscription_pk)
        )
    payment_id = (Payment.objects.filter(order=order, status=Payment.PaymentStatus.CONFIRMED)
                  .order_by('-created_at').values_list('payment_id', flat=True).first())
    if payment_id and gift.new_member_at_claim:
        from apps.referrals.services import _enqueue_claimed_gift_reward
        transaction.on_commit(
            lambda paid_id=payment_id: _enqueue_claimed_gift_reward(paid_id)
        )
    return order


@transaction.atomic
def complete_claimed_gifts(*, user_id: int, brand_id: int) -> list[int]:
    """Finish held handovers after the recipient completed initial onboarding.

    Re-running the operation is safe and cannot transfer another user's gift.
    """
    vouchers = list(GiftVoucher.objects.filter(
        claimed_by_id=user_id, order__brand_id=brand_id,
        order__recipient__isnull=True,
    ).values_list('token', flat=True))
    completed = []
    for token in vouchers:
        order = claim_gift(token=token, brand_id=brand_id, user_id=user_id,
                           newly_registered=False)
        from apps.subscriptions.models import Subscription
        sub_pk = Subscription.objects.filter(order=order).values_list('pk', flat=True).first()
        if sub_pk:
            completed.append(sub_pk)
    return completed
