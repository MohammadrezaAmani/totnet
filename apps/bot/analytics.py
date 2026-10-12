"""Brand-scoped behavioral and financial summaries for gamification decisions."""

from decimal import Decimal

from django.db.models import Avg, Count, DecimalField, Exists, F, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from apps.orders.models import Order, Wallet
from apps.referrals.models import (
    GamificationNotification,
    ReferralClick,
    ServiceSurvey,
    UserChallenge,
)
from .models import BehaviorEvent


def record_event(
    *,
    user_id,
    brand_id,
    event,
    value="",
    metadata=None,
    dedupe_key=None,
    outcome="completed",
):
    data = dict(
        user_id=user_id,
        brand_id=brand_id,
        event=event,
        value=str(value)[:120],
        metadata=metadata or {},
        outcome=outcome,
    )
    if dedupe_key:
        return BehaviorEvent.objects.get_or_create(
            dedupe_key=dedupe_key, defaults=data
        )[0]
    return BehaviorEvent.objects.create(**data)


def paid_orders(brand_id):
    return (
        Order.objects.filter(
            brand_id=brand_id,
            status__in=["paid", "processing", "completed"],
            final_price__gt=0,
        )
        .annotate(
            paid_total=Coalesce(
                Sum("payments__amount", filter=Q(payments__status="confirmed")),
                Value(Decimal(0)),
                output_field=DecimalField(max_digits=25, decimal_places=10),
            ),
        )
        .filter(paid_total__gte=F("final_price"))
    )


def referral_link_metrics(link):
    visitor_ids = ReferralClick.objects.filter(link=link).values("visitor_id")
    orders = paid_orders(link.brand_id).filter(
        user_id__in=visitor_ids,
        order_type__in=["new_subscription", "renewal", "upgrade", "gift"],
    )
    orders = orders.annotate(visited_before_order=Exists(ReferralClick.objects.filter(link=link, visitor_id=OuterRef("user_id"), created_at__lte=OuterRef("created_at")))).filter(visited_before_order=True)
    orders = Order.objects.filter(pk__in=orders.values("pk"))
    return {
        "visits": link.visits.count(),
        "registrations": link.visits.filter(is_registration=True).count(),
        "referrals": link.referrals.count(),
        "buyers": orders.values("user_id").distinct().count(),
        "purchases": orders.count(),
        "revenue": list(
            orders.order_by().values("currency").annotate(total=Sum("final_price"))
        ),
    }


def gamification_report(brand_id):
    challenges = UserChallenge.objects.filter(brand_id=brand_id)
    statuses = {
        row["status"]: row["count"]
        for row in challenges.values("status").annotate(count=Count("pk"))
    }
    participants = sum(statuses.get(s, 0) for s in ["active", "succeeded", "failed"])
    settled = statuses.get("succeeded", 0) + statuses.get("failed", 0)
    result = {
        "statuses": statuses,
        "participants": participants,
        "success_rate": round(100 * statuses.get("succeeded", 0) / settled, 1)
        if settled
        else None,
        "tiers": list(
            challenges.filter(tier__isnull=False)
            .order_by("target_referrals")
            .values("target_referrals")
            .annotate(
                selected=Count("pk"),
                successes=Count("pk", filter=Q(status="succeeded")),
                failures=Count("pk", filter=Q(status="failed")),
                average_progress=Avg("successful_referrals"),
            )
        ),
        "notifications": list(
            GamificationNotification.objects.filter(brand_id=brand_id)
            .order_by()
            .values("notification_type", "rule_id")
            .annotate(
                queued=Count("pk"), sent=Count("pk", filter=Q(sent_at__isnull=False))
            )
        ),
        "surveys": ServiceSurvey.objects.filter(brand_id=brand_id).aggregate(
            total=Count("pk"),
            completed=Count("pk", filter=Q(completed_at__isnull=False)),
            quality=Avg("quality_rating"),
            app=Avg("app_rating"),
            support=Avg("support_rating"),
        ),
        "choices": list(
            BehaviorEvent.objects.filter(
                brand_id=brand_id,
                event__in=[
                    "plan_duration",
                    "plan_volume",
                    "plan_users",
                    "service_category",
                    "challenge_tier",
                    "survey_choice",
                ],
            )
            .order_by()
            .values("event", "value")
            .annotate(count=Count("pk"), users=Count("user_id", distinct=True))
            .order_by("-count")[:30]
        ),
        "errors": BehaviorEvent.objects.filter(
            brand_id=brand_id, outcome="failed"
        ).count(),
        "frozen": Wallet.objects.filter(brand_id=brand_id).aggregate(
            total=Sum("challenge_frozen_balance")
        )["total"]
        or Decimal(0),
        "challenge_rewards": challenges.filter(status="succeeded").aggregate(
            total=Sum("reward_amount")
        )["total"]
        or Decimal(0),
        "updated_at": timezone.now().isoformat(),
    }
    return result


def callback_event(data):
    if data.startswith("pf:"):
        parts = data.split(":")
        return ("plan_" + parts[2], parts[3]) if len(parts) == 4 else ("callback", data)
    prefixes = {
        "purchase_category_": "service_category",
        "challenge_tier_": "challenge_tier",
        "select_plan_": "plan_selected",
        "survey_rate_": "survey_choice",
        "service_survey_": "survey_start",
        "buy_for_other_": "purchase_other",
        "challenge_accept_": "challenge_accept",
        "challenge_decline_": "challenge_decline",
    }
    for prefix, event in prefixes.items():
        if data.startswith(prefix):
            return event, data[len(prefix) :]
    return "callback", data
