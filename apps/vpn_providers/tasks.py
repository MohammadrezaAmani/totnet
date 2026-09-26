"""Synchronous Celery entry points for asynchronous provider clients."""

import logging
from datetime import timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.subscriptions.models import (
    ProviderRemoteSubscription,
    Subscription,
)

from .models import VPNProvider, VPNProviderHealthCheck, VPNProviderStats
from .services.base import VPNProviderFactory, VPNUser
from .services.connectix import ConnectixProvider

logger = logging.getLogger(__name__)


def _client(provider):
    if provider.provider_type == VPNProvider.ProviderType.CONNECTIX:
        return VPNProviderFactory.create(
            provider.provider_type,
            base_url=provider.base_url or settings.CONNECTIX_API_BASE_URL,
            username=settings.CONNECTIX_USERNAME,
            password=settings.CONNECTIX_PASSWORD,
            timeout_seconds=settings.CONNECTIX_TIMEOUT_SECONDS,
        )
    configuration = dict(provider.configuration or {})
    configuration.update(
        base_url=provider.base_url,
        api_key=provider.api_key,
        public_api_key=provider.public_api_key,
        proxy_path=provider.proxy_path,
    )
    return VPNProviderFactory.create(provider.provider_type, **configuration)


@shared_task(bind=True, max_retries=3)
def sync_vpn_users(self, provider_id: int):
    """Synchronize active subscriptions to one supported provider."""
    try:
        provider = VPNProvider.objects.get(pk=provider_id)
    except VPNProvider.DoesNotExist:
        logger.warning("Provider sync skipped: provider %s does not exist", provider_id)
        return False

    if provider.provider_type == VPNProvider.ProviderType.CONNECTIX:
        try:
            return sync_connectix_status(provider)
        except Exception as exc:
            raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))

    client = _client(provider)
    if not hasattr(client, "sync_users"):
        logger.info(
            "Provider %s does not support bulk user synchronization", provider.pk
        )
        async_to_sync(client.close)()
        return False
    subscriptions = Subscription.objects.filter(
        vpn_provider=provider, status=Subscription.SubscriptionStatus.ACTIVE
    ).select_related("user", "plan")
    users = [
        VPNUser(
            email=subscription.vpn_user_email or str(subscription.subscription_id),
            proxies={},
            inbounds=["main"],
            traffic_limit=(
                subscription.traffic_limit_gb * 1024**3
                if subscription.traffic_limit_gb
                else None
            ),
            expire_time=subscription.expires_at,
            enable=True,
        )
        for subscription in subscriptions
    ]

    try:
        success = async_to_sync(client.sync_users)(users)
        if success:
            provider.last_sync = timezone.now()
            provider.save(update_fields=["last_sync", "updated_at"])
        else:
            logger.warning(
                "Provider sync returned failure for provider %s", provider.pk
            )
        return bool(success)
    except Exception as exc:
        logger.error(
            "Provider sync failed for provider %s (%s)", provider.pk, type(exc).__name__
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
    finally:
        async_to_sync(client.close)()


def sync_connectix_status(provider):
    """Refresh saved Connectix identities using the observed paginated client list."""
    client = _client(provider)
    if not isinstance(client, ConnectixProvider):
        return False

    async def fetch_all():
        records = {}
        try:
            page_number = 1
            while page_number <= 200:
                page = await client.client.get_clients(page=page_number)
                records.update({record.remote_id: record for record in page.clients})
                if page_number >= page.last_page:
                    return records
                page_number += 1
            raise ValueError(
                "Connectix client listing exceeded the pagination safety limit"
            )
        finally:
            await client.close()

    try:
        remote_clients = async_to_sync(fetch_all)()
    except Exception as exc:
        logger.error(
            "Connectix status sync failed for provider %s (%s)",
            provider.pk,
            type(exc).__name__,
        )
        raise

    synced_at = timezone.now()
    remote_accounts = ProviderRemoteSubscription.objects.filter(
        provider=provider, remote_id__isnull=False
    ).select_related("subscription")
    for remote_account in remote_accounts.iterator():
        record = remote_clients.get(remote_account.remote_id)
        if record is None:
            continue
        remote_account.remote_status = (
            "expired"
            if record.is_expired
            else "active"
            if record.is_active
            else "inactive"
        )
        remote_account.subscription_url = record.subscription_link
        remote_account.metadata = {
            **(remote_account.metadata or {}),
            "plan_name": record.plan_name,
            "group_name": record.group_name,
            "expire_date": record.expire_date,
            "remains_days": record.remains_days,
            "used_traffic_raw": record.used_traffic,
        }
        remote_account.last_synced_at = synced_at
        remote_account.last_error = ""
        remote_account.save(
            update_fields=(
                "remote_status",
                "subscription_url",
                "metadata",
                "last_synced_at",
                "last_error",
                "updated_at",
            )
        )

        subscription = remote_account.subscription
        if subscription.status in (
            Subscription.SubscriptionStatus.CANCELLED,
            Subscription.SubscriptionStatus.PENDING,
        ):
            continue
        if record.is_expired:
            subscription.status = Subscription.SubscriptionStatus.EXPIRED
        elif not record.is_active:
            subscription.status = Subscription.SubscriptionStatus.SUSPENDED
        else:
            subscription.status = Subscription.SubscriptionStatus.ACTIVE
            if record.subscription_link:
                subscription.subscription_url = record.subscription_link
        update_fields = ["status", "updated_at"]
        if record.is_active and not record.is_expired and record.subscription_link:
            update_fields.append("subscription_url")
        subscription.save(update_fields=update_fields)

    provider.last_sync = synced_at
    provider.save(update_fields=("last_sync", "updated_at"))
    return True


@shared_task
def sync_all_vpn_users():
    """Queue synchronization for active providers that have adapters."""
    for provider in VPNProvider.objects.filter(
        status=VPNProvider.ProviderStatus.ACTIVE
    ):
        if provider.provider_type == VPNProvider.ProviderType.CONNECTIX:
            sync_vpn_users.delay(provider.pk)
            continue
        try:
            client = _client(provider)
        except ValueError:
            logger.warning(
                "Provider %s (%s) is active but has no registered adapter",
                provider.pk,
                provider.provider_type,
            )
            continue
        if not hasattr(client, "sync_users"):
            async_to_sync(client.close)()
            logger.info(
                "Provider %s does not support bulk synchronization", provider.pk
            )
            continue
        async_to_sync(client.close)()
        sync_vpn_users.delay(provider.pk)


@shared_task(bind=True, max_retries=3)
def check_provider_health(self, provider_id: int):
    """Run an authenticated provider health check without changing activation."""
    try:
        provider = VPNProvider.objects.get(pk=provider_id)
    except VPNProvider.DoesNotExist:
        logger.warning("Health check skipped: provider %s does not exist", provider_id)
        return False

    client = _client(provider)
    try:
        result = async_to_sync(client.health_check)()
        checked_at = timezone.now()
        server_info = result.get("server_info")
        version = getattr(server_info, "version", None)
        response_ms = max(0, int(result.get("response_time_ms", 0)))
        error = result.get("error")

        VPNProviderHealthCheck.objects.create(
            provider=provider,
            check_time=checked_at,
            is_healthy=bool(result.get("healthy")),
            response_time=response_ms,
            api_accessible=bool(result.get("healthy")),
            error_message=(str(error)[:1000] if error else None),
            version=version,
        )
        provider.health_status = "healthy" if result.get("healthy") else "unhealthy"
        provider.response_time = response_ms
        provider.last_health_check = checked_at
        provider.save(
            update_fields=[
                "health_status",
                "response_time",
                "last_health_check",
                "updated_at",
            ]
        )
        return bool(result.get("healthy"))
    except Exception as exc:
        logger.error(
            "Provider health check failed for provider %s (%s)",
            provider.pk,
            type(exc).__name__,
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
    finally:
        async_to_sync(client.close)()


@shared_task
def check_all_providers_health():
    """Queue health checks for active providers."""
    for provider in VPNProvider.objects.filter(
        status=VPNProvider.ProviderStatus.ACTIVE
    ):
        try:
            client = _client(provider)
        except ValueError:
            logger.warning(
                "Provider %s (%s) has no registered adapter",
                provider.pk,
                provider.provider_type,
            )
            continue
        async_to_sync(client.close)()
        check_provider_health.delay(provider.pk)


@shared_task(bind=True, max_retries=3)
def collect_provider_stats(self, provider_id: int):
    """Collect provider statistics when the adapter exposes those operations."""
    try:
        provider = VPNProvider.objects.get(pk=provider_id)
    except VPNProvider.DoesNotExist:
        logger.warning(
            "Stats collection skipped: provider %s does not exist", provider_id
        )
        return False

    client = _client(provider)
    methods = ("get_backend_stats", "get_system_stats")
    if not all(hasattr(client, method) for method in methods):
        logger.info("Provider %s does not expose system statistics", provider.pk)
        async_to_sync(client.close)()
        return False

    try:
        backend_stats = async_to_sync(client.get_backend_stats)()
        system_stats = async_to_sync(client.get_system_stats)()
        online_users = async_to_sync(client.get_online_users)()
        if not backend_stats or not system_stats:
            return False
        VPNProviderStats.objects.create(
            provider=provider,
            collected_at=timezone.now(),
            total_users=provider.current_users,
            online_users=len(online_users),
            cpu_usage=system_stats.get("cpu_usage", 0),
            memory_usage=system_stats.get("memory_usage", 0),
            disk_usage=system_stats.get("disk_usage", 0),
            additional_metrics={
                "backend_stats": backend_stats,
                "system_stats": system_stats,
            },
        )
        return True
    except Exception as exc:
        logger.error(
            "Provider stats collection failed for provider %s (%s)",
            provider.pk,
            type(exc).__name__,
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
    finally:
        async_to_sync(client.close)()


@shared_task
def cleanup_old_health_checks():
    cutoff = timezone.now() - timedelta(days=30)
    deleted_count = VPNProviderHealthCheck.objects.filter(
        check_time__lt=cutoff
    ).delete()[0]
    logger.info("Removed %s old provider health checks", deleted_count)


@shared_task
def cleanup_old_stats():
    cutoff = timezone.now() - timedelta(days=90)
    deleted_count = VPNProviderStats.objects.filter(collected_at__lt=cutoff).delete()[0]
    logger.info("Removed %s old provider statistics records", deleted_count)
