"""Resume gift account provisioning for gifts paid before the handover rollout."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.orders.models import GiftVoucher, Order, Payment
from apps.subscriptions.models import Subscription
from apps.vpn_providers.models import VPNProvider


class Command(BaseCommand):
    help = "Prepare paid unclaimed gifts and deliver full credentials once ready."

    def add_arguments(self, parser):
        parser.add_argument("--token", help="One gift token (not the full URL)")
        parser.add_argument("--all", action="store_true", help="All unclaimed, unexpired paid gifts")

    def handle(self, *args, **options):
        if bool(options["token"]) == bool(options["all"]):
            raise CommandError("Specify exactly one of --token or --all")
        vouchers = GiftVoucher.objects.select_related(
            "order", "order__brand", "order__plan", "order__user", "order__plan__vpn_provider"
        ).filter(expires_at__gt=timezone.now(), claimed_by__isnull=True)
        if options["token"]:
            vouchers = vouchers.filter(token=options["token"])
        processed = 0
        for voucher in vouchers.iterator():
            order = voucher.order
            if order.order_type != Order.OrderType.GIFT or order.recipient_id:
                continue
            if order.status not in (Order.OrderStatus.PAID, Order.OrderStatus.PROCESSING,
                                    Order.OrderStatus.COMPLETED):
                continue
            total = Payment.objects.filter(order=order, status=Payment.PaymentStatus.CONFIRMED).aggregate(
                sum=Sum("amount"))["sum"] or 0
            if total < order.final_price:
                continue
            provider = order.plan.vpn_provider if order.plan.vpn_provider_id else VPNProvider.objects.filter(
                brand_id=order.brand_id, is_default=True, status=VPNProvider.ProviderStatus.ACTIVE
            ).first()
            if not provider or provider.brand_id != order.brand_id or provider.status != VPNProvider.ProviderStatus.ACTIVE:
                self.stderr.write(f"Skipping gift {order.pk}: no active provider")
                continue
            with transaction.atomic():
                sub = Subscription.objects.select_for_update().filter(order=order).first()
                if sub is None:
                    sub = Subscription.objects.create(
                        brand_id=order.brand_id, user_id=order.user_id, owner_id=order.user_id,
                        plan=order.plan, order=order, vpn_provider=provider,
                        status=Subscription.SubscriptionStatus.PENDING, is_gift=True,
                        starts_at=timezone.now(), traffic_limit_gb=order.plan.traffic_limit_gb,
                    )
                if sub.status == Subscription.SubscriptionStatus.ACTIVE:
                    from apps.orders.gift_delivery import queue_ready_gift_details
                    transaction.on_commit(lambda order_pk=order.pk: queue_ready_gift_details(order_id=order_pk))
                elif sub.provisioning_state not in {"needs_review", "unsupported", "in_progress"}:
                    from apps.subscriptions.tasks import provision_paid_order
                    transaction.on_commit(lambda order_pk=order.pk: provision_paid_order.delay(order_pk))
                else:
                    self.stderr.write(f"Skipping gift {order.pk}: provisioning requires review")
                    continue
            processed += 1
        self.stdout.write(f"Queued/checked {processed} paid gifts")
