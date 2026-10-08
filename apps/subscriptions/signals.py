from django.db import transaction
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .catalog import invalidate_plan_catalog
from .models import SubscriptionPlan
from apps.vpn_providers.models import VPNProvider


@receiver([post_save, post_delete], sender=SubscriptionPlan)
def refresh_changed_plan_catalog(sender, instance, **kwargs):
    transaction.on_commit(lambda: invalidate_plan_catalog(instance.brand_id))


@receiver(post_save, sender=VPNProvider)
def invalidate_provider_plan_catalog(sender, instance, **kwargs):
    transaction.on_commit(lambda: invalidate_plan_catalog(instance.brand_id))
