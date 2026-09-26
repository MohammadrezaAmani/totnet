"""Subscription provisioning operations that persist verified provider identity."""

import logging

from django.conf import settings
from django.utils import timezone

from apps.subscriptions.models import ProviderRemoteSubscription, Subscription
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.services.connectix import (
    ConnectixProvider,
    ConnectixProvisioningRequest,
)
from apps.vpn_providers.services.connectix_client import ConnectixError

logger = logging.getLogger(__name__)


async def provision_connectix_subscription(subscription: Subscription) -> bool:
    subscription = await Subscription.objects.select_related(
        "vpn_provider", "plan", "owner"
    ).aget(pk=subscription.pk)
    provider = subscription.vpn_provider
    plan = subscription.plan
    if provider.provider_type != VPNProvider.ProviderType.CONNECTIX:
        raise ValueError("Connectix provisioning requires a Connectix provider")
    if plan.vpn_provider_id != provider.pk or plan.brand_id != subscription.brand_id:
        raise ValueError("The plan is not mapped to this brand's Connectix provider")
    if (
        not plan.upstream_plan_id
        or not plan.upstream_group_id
        or not plan.upstream_count_of_devices
    ):
        raise ValueError("The subscription plan has no complete Connectix mapping")

    client = ConnectixProvider(
        base_url=provider.base_url or settings.CONNECTIX_API_BASE_URL,
        username=settings.CONNECTIX_USERNAME,
        password=settings.CONNECTIX_PASSWORD,
        timeout_seconds=settings.CONNECTIX_TIMEOUT_SECONDS,
    )
    remote_account = await ProviderRemoteSubscription.objects.filter(
        subscription=subscription
    ).afirst()

    try:
        if remote_account and remote_account.remote_id:
            remote_id = remote_account.remote_id
            username = remote_account.username
        else:
            request = ConnectixProvisioningRequest(
                subscription_id=str(subscription.subscription_id),
                display_name=(
                    subscription.owner.full_name
                    or subscription.owner.get_full_name()
                    or subscription.owner.username
                ),
                email=subscription.owner.email or None,
                telegram_id=subscription.owner.telegram_id,
                plan_id=plan.upstream_plan_id,
                plan_name=plan.upstream_plan_name or plan.name,
                group_id=plan.upstream_group_id,
                group_name=plan.upstream_group_name,
                count_of_devices=plan.upstream_count_of_devices,
            )
            created = await client.create_account(request)
            remote_id = created.remote_id
            username = created.username
            (
                remote_account,
                _,
            ) = await ProviderRemoteSubscription.objects.aupdate_or_create(
                subscription=subscription,
                defaults={
                    "provider": provider,
                    "remote_id": remote_id,
                    "username": username,
                    "state": ProviderRemoteSubscription.State.PROVISIONING,
                    "last_error": "",
                    "metadata": {
                        "plan_id": plan.upstream_plan_id,
                        "group_id": plan.upstream_group_id,
                    },
                },
            )
            subscription.connectix_username = created.username
            await subscription.asave(update_fields=["connectix_username", "updated_at"])

        record = await client.get_client(remote_id)
        remote_account.remote_id = remote_id
        remote_account.username = record.username or username
        remote_account.remote_status = (
            "active" if record.is_active and not record.is_expired else "inactive"
        )
        remote_account.subscription_url = record.subscription_link
        remote_account.metadata = {
            **(remote_account.metadata or {}),
            "plan_name": record.plan_name,
            "group_name": record.group_name,
            "expire_date": record.expire_date,
            "remains_days": record.remains_days,
            "used_traffic": record.used_traffic,
        }
        remote_account.last_synced_at = timezone.now()

        if not record.subscription_link:
            remote_account.state = ProviderRemoteSubscription.State.PROVISIONING
            remote_account.last_error = (
                "Connectix has not returned a subscription link yet."
            )
            await remote_account.asave()
            return False
        if not record.is_active or record.is_expired:
            remote_account.state = ProviderRemoteSubscription.State.PROVISIONING
            remote_account.last_error = "Connectix returned the client as inactive."
            await remote_account.asave()
            return False

        remote_account.state = ProviderRemoteSubscription.State.ACTIVE
        remote_account.last_error = ""
        await remote_account.asave()
        subscription.subscription_url = record.subscription_link
        subscription.connectix_username = record.username
        subscription.status = Subscription.SubscriptionStatus.ACTIVE
        await subscription.asave(
            update_fields=[
                "subscription_url",
                "connectix_username",
                "status",
                "updated_at",
            ]
        )
        return True
    except ConnectixError as exc:
        if remote_account:
            remote_account.state = ProviderRemoteSubscription.State.ERROR
            remote_account.last_error = type(exc).__name__
            await remote_account.asave(
                update_fields=["state", "last_error", "updated_at"]
            )
        logger.error(
            "Connectix provisioning failed for local subscription %s (%s)",
            subscription.pk,
            type(exc).__name__,
        )
        return False
    finally:
        await client.close()
