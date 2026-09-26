"""Synchronous Celery entry points for asynchronous provider clients."""

import logging
from datetime import timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.subscriptions.models import Subscription

from .models import VPNProvider, VPNProviderHealthCheck, VPNProviderStats
from .services.base import VPNProviderFactory, VPNUser

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


@shared_task
def sync_all_vpn_users():
    """Queue synchronization for active providers that have adapters."""
    for provider in VPNProvider.objects.filter(
        status=VPNProvider.ProviderStatus.ACTIVE
    ):
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
