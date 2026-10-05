"""Subscription provisioning operations that persist verified provider identity."""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.orders.models import Order
from apps.subscriptions.models import (
    ProviderRemoteSubscription,
    Subscription,
    SubscriptionPlan,
)
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.services.capabilities import capabilities_for
from apps.vpn_providers.services.connectix import (
    ConnectixProvider,
    ConnectixProvisioningRequest,
)
from apps.vpn_providers.services.connectix_client import ConnectixError

logger = logging.getLogger(__name__)


async def provision_order_subscription(order_id: int) -> Subscription | None:
    """Create/resume the local subscription and provision it through its provider."""
    order = await Order.objects.select_related(
        "brand", "user", "recipient", "plan", "plan__vpn_provider"
    ).aget(pk=order_id)
    subscription = (
        await Subscription.objects.filter(order=order)
        .select_related("vpn_provider", "plan", "owner")
        .afirst()
    )
    if subscription and subscription.status == Subscription.SubscriptionStatus.ACTIVE:
        return subscription

    provider = (
        subscription.vpn_provider
        if subscription
        else order.plan.vpn_provider
        if order.plan.vpn_provider_id
        else await VPNProvider.objects.filter(
            brand=order.brand,
            status=VPNProvider.ProviderStatus.ACTIVE,
            is_default=True,
        ).afirst()
    )
    if not provider or provider.brand_id != order.brand_id:
        logger.error("No valid provider configured for paid order %s", order.pk)
        if subscription:
            subscription.provisioning_state = "retryable_error"
            subscription.provisioning_error_code = "provider_not_configured"
            subscription.provisioning_error = (
                "No active provider is configured for this brand."
            )
            subscription.provisioning_retryable = True
            await subscription.asave(
                update_fields=(
                    "provisioning_state",
                    "provisioning_error_code",
                    "provisioning_error",
                    "provisioning_retryable",
                    "updated_at",
                )
            )
        return subscription
    if provider.status != VPNProvider.ProviderStatus.ACTIVE:
        logger.error("Selected provider is inactive for order %s", order.pk)
        if subscription:
            subscription.provisioning_state = "retryable_error"
            subscription.provisioning_error_code = "provider_inactive"
            subscription.provisioning_error = (
                "The selected provider is currently inactive."
            )
            subscription.provisioning_retryable = True
            await subscription.asave(
                update_fields=(
                    "provisioning_state",
                    "provisioning_error_code",
                    "provisioning_error",
                    "provisioning_retryable",
                    "updated_at",
                )
            )
        return subscription

    start_date = subscription.starts_at if subscription else timezone.now()
    end_date = subscription.expires_at if subscription else None
    if order.plan.duration_value and not end_date:
        duration_days = order.plan.duration_value
        if order.plan.duration_unit == SubscriptionPlan.DurationUnit.WEEKS:
            duration_days *= 7
        elif order.plan.duration_unit == SubscriptionPlan.DurationUnit.MONTHS:
            duration_days *= 30
        elif order.plan.duration_unit == SubscriptionPlan.DurationUnit.YEARS:
            duration_days *= 365
        end_date = start_date + timedelta(days=duration_days)

    if not subscription:
        vpn_email = (
            f"user_{order.user_id}_{uuid.uuid4().hex[:8]}@{order.brand.slug}.vpn"
        )
        subscription = await Subscription.objects.acreate(
            brand=order.brand,
            user=order.user,
            plan=order.plan,
            order=order,
            vpn_provider=provider,
            vpn_user_email=vpn_email,
            owner=order.recipient or order.user,
            starts_at=start_date,
            expires_at=end_date,
            traffic_limit_gb=order.plan.traffic_limit_gb,
            status=Subscription.SubscriptionStatus.PENDING,
        )
    else:
        changes = []
        if not subscription.expires_at and end_date:
            subscription.expires_at = end_date
            changes.append("expires_at")
        if subscription.status != Subscription.SubscriptionStatus.PENDING:
            subscription.status = Subscription.SubscriptionStatus.PENDING
            changes.append("status")
        if changes:
            changes.append("updated_at")
            await subscription.asave(update_fields=changes)

    if not capabilities_for(provider.provider_type).provision:
        subscription.provisioning_state = "unsupported"
        subscription.provisioning_error_code = "provider_provisioning_unsupported"
        subscription.provisioning_error = (
            "No implemented provisioning adapter is available for this provider type."
        )
        await subscription.asave(
            update_fields=(
                "provisioning_state",
                "provisioning_error_code",
                "provisioning_error",
                "updated_at",
            )
        )
        return subscription

    if provider.provider_type == VPNProvider.ProviderType.CONNECTIX:
        try:
            await provision_connectix_subscription(subscription)
        except Exception as exc:
            logger.error(
                "Connectix provisioning failed for subscription %s (%s)",
                subscription.pk,
                type(exc).__name__,
            )
    elif provider.provider_type == VPNProvider.ProviderType.HIDDIFY:
        from apps.vpn_providers.services.hiddify import (
            HiddifyLanguage,
            HiddifyProvider,
            HiddifyUser,
            HiddifyUserMode,
        )

        client = HiddifyProvider(
            base_url=provider.base_url,
            api_key=provider.api_key,
            proxy_path=provider.proxy_path or "",
            public_api_key=provider.public_api_key,
        )
        try:
            configs = subscription.connection_configs or {}
            existing_uuid = configs.get("hiddify_uuid") or configs.get("secret_uuid")
            if existing_uuid:
                provider_configs = await client.get_user_configs(
                    secret_uuid=existing_uuid
                )
                subscription_link = next(
                    (
                        config.link
                        for config in provider_configs or []
                        if config.link
                        and (config.name or "").strip().lower() == "subscription link"
                    ),
                    None,
                )
                if subscription_link:
                    subscription.subscription_url = subscription_link
                    subscription.status = Subscription.SubscriptionStatus.ACTIVE
                    subscription.provisioning_state = "active"
                    subscription.provisioning_error_code = ""
                    subscription.provisioning_error = ""
                    subscription.provisioning_retryable = False
                    await subscription.asave(
                        update_fields=(
                            "subscription_url",
                            "status",
                            "provisioning_state",
                            "provisioning_error_code",
                            "provisioning_error",
                            "provisioning_retryable",
                            "updated_at",
                        )
                    )
            else:
                created_user = await client.create_hiddify_user(
                    HiddifyUser(
                        name=f"user_{order.user.telegram_id}_{subscription.pk}",
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
                )
                if not created_user or not created_user.uuid:
                    created_user = None
                if created_user:
                    subscription.connection_configs = {
                        **configs,
                        "secret_uuid": str(created_user.uuid),
                        "hiddify_uuid": str(created_user.uuid),
                        "created_at": timezone.now().isoformat(),
                    }
                    subscription.vpn_user_email = (
                        f"{created_user.uuid}@{order.brand.slug}.vpn"
                    )
                    await subscription.asave(
                        update_fields=(
                            "connection_configs",
                            "vpn_user_email",
                            "updated_at",
                        )
                    )
                    provider.current_users += 1
                    provider.total_subscriptions += 1
                    await provider.asave(
                        update_fields=(
                            "current_users",
                            "total_subscriptions",
                            "updated_at",
                        )
                    )
                    provider_configs = await client.get_user_configs(
                        secret_uuid=str(created_user.uuid)
                    )
                    subscription_link = next(
                        (
                            config.link
                            for config in provider_configs or []
                            if config.link
                            and (config.name or "").strip().lower()
                            == "subscription link"
                        ),
                        None,
                    )
                    if subscription_link:
                        subscription.subscription_url = subscription_link
                        subscription.status = Subscription.SubscriptionStatus.ACTIVE
                        subscription.provisioning_state = "active"
                        subscription.provisioning_error_code = ""
                        subscription.provisioning_error = ""
                        subscription.provisioning_retryable = False
                        await subscription.asave(
                            update_fields=(
                                "subscription_url",
                                "status",
                                "provisioning_state",
                                "provisioning_error_code",
                                "provisioning_error",
                                "provisioning_retryable",
                                "updated_at",
                            )
                        )
        except Exception as exc:
            logger.error(
                "Hiddify provisioning failed for subscription %s (%s)",
                subscription.pk,
                type(exc).__name__,
            )
        finally:
            await client.close()
    else:
        logger.warning(
            "Provider type %s has no provisioning adapter; subscription %s remains pending",
            provider.provider_type,
            subscription.pk,
        )

    await subscription.arefresh_from_db()
    order.status = (
        Order.OrderStatus.COMPLETED
        if subscription.status == Subscription.SubscriptionStatus.ACTIVE
        else Order.OrderStatus.PROCESSING
    )
    await order.asave(update_fields=("status", "updated_at"))
    logger.info("Subscription %s is %s", subscription.pk, subscription.status)
    return subscription


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
        subscription.provisioning_state = "active"
        subscription.provisioning_error_code = ""
        subscription.provisioning_error = ""
        subscription.provisioning_retryable = False
        await subscription.asave(
            update_fields=[
                "subscription_url",
                "connectix_username",
                "status",
                "provisioning_state",
                "provisioning_error_code",
                "provisioning_error",
                "provisioning_retryable",
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
        subscription.provisioning_state = "needs_review"
        subscription.provisioning_error_code = type(exc).__name__[:80]
        subscription.provisioning_error = str(exc)[:1000]
        subscription.provisioning_retryable = False
        await subscription.asave(
            update_fields=(
                "provisioning_state",
                "provisioning_error_code",
                "provisioning_error",
                "provisioning_retryable",
                "updated_at",
            )
        )
        logger.error(
            "Connectix provisioning failed for local subscription %s (%s): %s",
            subscription.pk,
            type(exc).__name__,
            exc,
        )
        return False
    finally:
        await client.close()
