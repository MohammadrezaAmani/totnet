"""Lifecycle anchors and current subscription audiences shared by admin and workers."""

from datetime import timedelta
from decimal import Decimal
from html import escape

from django.db.models import DecimalField, Exists, F, Max, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.models import BotState
from apps.orders.models import Order, Payment
from apps.subscriptions.models import Subscription
from .models import (
    GamificationNotification,
    NotificationRule,
    NotificationTrigger,
    ServiceSurvey,
)


def audience_users(brand_id, audience="all"):
    now = timezone.now()
    subscriptions = Subscription.objects.filter(
        brand_id=brand_id,
        owner_id=OuterRef("pk"),
        status="active",
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if audience in {"royal", "normal", "iran_ip"}:
        subscriptions = subscriptions.filter(plan__service_category=audience)
    users = User.objects.filter(
        brand_id=brand_id, is_active=True, telegram_id__isnull=False
    ).exclude(telegram_id=0)
    users = users.filter(
        Q(profile__isnull=True) | Q(profile__notification_enabled=True)
    )
    if audience != "all":
        users = users.annotate(matches_audience=Exists(subscriptions)).filter(
            matches_audience=audience != "inactive"
        )
    return users


def lifecycle_candidates(
    *, brand_id, trigger, delay_minutes, audience="all", limit=500
):
    """Yield user, stable occurrence key and timestamp; never start a clock on partial payment."""
    cutoff = timezone.now() - timedelta(minutes=delay_minutes)
    users = audience_users(brand_id, audience)
    if trigger == NotificationTrigger.REFERRAL_LINK_JOIN:
        from .models import ReferralClick
        visits = (
            ReferralClick.objects.filter(
                brand_id=brand_id, visitor__in=users,
                is_registration=True, created_at__lte=cutoff,
                link__is_active=True,
            )
            .select_related("visitor", "link", "link__welcome_coupon")
            .order_by("created_at", "pk")
        )
        for visit in visits.iterator(chunk_size=200):
            yield visit.visitor, f"referral-link:{visit.pk}", visit.created_at
        return
    if trigger == NotificationTrigger.FIRST_ACTIVE_SUBSCRIPTION:
        active_subscriptions = (
            Subscription.objects.filter(
                brand_id=brand_id, owner__in=users,
                status=Subscription.SubscriptionStatus.ACTIVE,
            )
            .select_related("owner")
            .order_by("owner_id", "starts_at", "pk")
        )
        seen_users = set()
        for subscription in active_subscriptions.iterator(chunk_size=200):
            if subscription.owner_id in seen_users:
                continue
            seen_users.add(subscription.owner_id)
            anchor = subscription.starts_at or subscription.created_at
            if anchor and anchor <= cutoff:
                yield subscription.owner, f"first-active:{subscription.pk}", anchor
        return
    if trigger == NotificationTrigger.JOINED:
        states = (
            BotState.objects.filter(
                brand_id=brand_id, user__in=users, created_at__lte=cutoff
            )
            .select_related("user")
            .order_by("created_at", "pk")
        )
        for state in states.iterator(chunk_size=200):
            yield state.user, f"joined:{state.pk}", state.created_at
        return
    confirmed = Q(payments__status=Payment.PaymentStatus.CONFIRMED)
    orders = (
        Order.objects.filter(
            brand_id=brand_id,
            user__in=users,
            order_type__in=["new_subscription", "renewal", "upgrade", "gift"],
            status__in=["paid", "processing", "completed"],
            final_price__gt=0,
        )
        .annotate(
            confirmed_total=Coalesce(
                Sum("payments__amount", filter=confirmed),
                Value(Decimal(0)),
                output_field=DecimalField(max_digits=25, decimal_places=10),
            ),
            paid_at=Max(
                Coalesce("payments__verified_at", "payments__created_at"),
                filter=confirmed,
            ),
        )
        .filter(confirmed_total__gte=F("final_price"))
        .select_related("user")
        .order_by("user_id", "paid_at", "pk")
    )
    seen = set()
    for order in orders.iterator(chunk_size=200):
        if trigger == NotificationTrigger.FIRST_PURCHASE:
            if order.user_id in seen:
                continue
            seen.add(order.user_id)
        if order.paid_at and order.paid_at <= cutoff:
            yield order.user, f"purchase:{order.pk}", order.paid_at


def _render_rule_message(message, referral_visit=None):
    """Escape admin copy and inject scoped, safe referral-promo variables."""
    from html import escape
    safe = escape(message)
    if referral_visit and referral_visit.link.welcome_coupon_id:
        link = referral_visit.link
        expires_at = referral_visit.created_at + timedelta(hours=link.welcome_coupon_valid_hours)
        from utils.jalali import jalali_date
        replacements = {
            "{coupon_code}": escape(link.welcome_coupon.code),
            "{coupon_expiry}": escape(str(jalali_date(expires_at, time=True))),
        }
    else:
        replacements = {"{coupon_code}": "", "{coupon_expiry}": ""}
    for placeholder, value in replacements.items():
        safe = safe.replace(placeholder, value)
    return safe


def run_notification_rules(limit=500):
    from .tasks import _queue_once

    counts = {}
    for rule in NotificationRule.objects.filter(
        is_active=True, brand__status="active"
    ).iterator():
        queued = 0
        for user, occurrence, anchor_at in lifecycle_candidates(
            brand_id=rule.brand_id,
            trigger=rule.trigger,
            delay_minutes=rule.delay_minutes,
            audience=rule.audience,
            limit=limit,
        ):
            # The first registration visit is immutable, so link-specific
            # campaigns cannot be triggered by clicking a different link later.
            referral_visit = None
            if rule.trigger == NotificationTrigger.REFERRAL_LINK_JOIN:
                from .models import ReferralClick
                visit_id = int(occurrence.rsplit(":", 1)[-1])
                referral_visit = ReferralClick.objects.select_related(
                    "link", "link__welcome_coupon"
                ).filter(pk=visit_id, brand_id=rule.brand_id, visitor=user).first()
                if not referral_visit or (
                    rule.referral_link_id and referral_visit.link_id != rule.referral_link_id
                ):
                    continue
            if (
                rule.kind != "custom"
                and GamificationNotification.objects.filter(
                    user=user,
                    brand_id=rule.brand_id,
                    notification_type=rule.kind,
                    sent_at__isnull=False,
                ).exists()
            ):
                continue
            key = f"rule:{rule.pk}:{user.pk}:{occurrence}"
            if GamificationNotification.objects.filter(
                dedupe_key=key, sent_at__isnull=False
            ).exists():
                continue
            if rule.kind == NotificationRule.Kind.SURVEY:
                survey, _ = ServiceSurvey.objects.get_or_create(
                    user=user, brand_id=rule.brand_id
                )
                if survey.completed_at:
                    continue
            if rule.kind == NotificationRule.Kind.PROFILE_COMPLETION:
                from apps.accounts.models import UserProfile

                profile, _ = UserProfile.objects.get_or_create(user=user)
                if profile.profile_reward_granted_at:
                    continue
            action = rule.button_action or {
                "survey": "service_survey_start",
                "profile_completion": "complete_profile",
                "referral_intro": "referral_system",
            }.get(rule.kind)
            buttons = (
                [
                    [
                        {
                            "text": rule.button_text
                            or {
                                "survey": "⭐ شروع نظرسنجی",
                                "profile_completion": "تکمیل پروفایل",
                                "referral_intro": "👥 معرفی دوستان",
                            }.get(rule.kind, "مشاهده"),
                            "callback_data": action,
                        }
                    ]
                ]
                if action
                else None
            )
            if _queue_once(
                user=user,
                brand_id=rule.brand_id,
                notification_type=rule.kind,
                dedupe_key=key,
                text=_render_rule_message(rule.message, referral_visit),
                buttons_data=buttons,
                metadata={
                    "rule_id": rule.pk,
                    "trigger": rule.trigger,
                    "anchor_at": anchor_at.isoformat(),
                },
                rule_id=rule.pk,
            ):
                queued += 1
            if queued >= limit:
                break
        counts[rule.pk] = queued
    return counts
