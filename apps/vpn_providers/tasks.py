"""
Celery tasks for VPN Provider operations
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.subscriptions.models import Subscription

from .models import VPNProvider, VPNProviderHealthCheck, VPNProviderStats
from .services.base import VPNProviderFactory

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
async def sync_vpn_users(self, provider_id: int):
    """Sync users for a specific VPN provider"""
    try:
        provider = VPNProvider.objects.get(id=provider_id)

        vpn_service = VPNProviderFactory.create(
            provider.provider_type,
            base_url=provider.base_url,
            api_key=provider.api_key,
            **provider.configuration,
        )

        subscriptions = Subscription.objects.filter(
            vpn_provider=provider, status=Subscription.SubscriptionStatus.ACTIVE
        ).select_related("user", "plan")

        from .services.base import VPNUser

        vpn_users = []

        for sub in subscriptions:
            vpn_user = VPNUser(
                email=sub.vpn_user_email,
                proxies={},
                inbounds=["main"],
                traffic_limit=(
                    sub.traffic_limit_gb * 1024**3 if sub.traffic_limit_gb else None
                ),
                expire_time=sub.expires_at,
                enable=True,
            )
            vpn_users.append(vpn_user)

        success = await vpn_service.sync_users(vpn_users)

        if success:
            provider.last_sync = timezone.now()
            provider.save()
            logger.info(
                f"Successfully synced {len(vpn_users)} users for provider {provider.name}"
            )
        else:
            logger.error(f"Failed to sync users for provider {provider.name}")

        await vpn_service.close()
        return success

    except VPNProvider.DoesNotExist:
        logger.error(f"VPN Provider with id {provider_id} not found")
    except Exception as e:
        logger.error(f"Error syncing VPN users for provider {provider_id}: {e}")

        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@shared_task
def sync_all_vpn_users():
    """Sync users for all active VPN providers"""
    active_providers = VPNProvider.objects.filter(
        status=VPNProvider.ProviderStatus.ACTIVE
    )

    for provider in active_providers:
        sync_vpn_users.delay(provider.id)


@shared_task(bind=True, max_retries=3)
async def check_provider_health(self, provider_id: int):
    """Check health of a specific VPN provider"""
    try:
        provider = VPNProvider.objects.get(id=provider_id)

        vpn_service = VPNProviderFactory.create(
            provider.provider_type,
            base_url=provider.base_url,
            api_key=provider.api_key,
            **provider.configuration,
        )

        health_result = await vpn_service.health_check()

        VPNProviderHealthCheck.objects.create(
            provider=provider,
            check_time=timezone.now(),
            is_healthy=health_result["healthy"],
            response_time=health_result.get("response_time_ms", 0),
            api_accessible=health_result["healthy"],
            error_message=health_result.get("error"),
            version=(
                health_result.get("server_info", {}).get("version")
                if health_result.get("server_info")
                else None
            ),
        )

        if health_result["healthy"]:
            provider.health_status = "healthy"
            provider.response_time = health_result.get("response_time_ms", 0)
        else:
            provider.health_status = "unhealthy"
            if provider.status == VPNProvider.ProviderStatus.ACTIVE:
                provider.status = VPNProvider.ProviderStatus.ERROR

        provider.last_health_check = timezone.now()
        provider.save()

        await vpn_service.close()
        return health_result["healthy"]

    except VPNProvider.DoesNotExist:
        logger.error(f"VPN Provider with id {provider_id} not found")
    except Exception as e:
        logger.error(f"Error checking health for provider {provider_id}: {e}")
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@shared_task
def check_all_providers_health():
    """Check health of all VPN providers"""
    providers = VPNProvider.objects.all()

    for provider in providers:
        check_provider_health.delay(provider.id)


@shared_task(bind=True, max_retries=3)
async def collect_provider_stats(self, provider_id: int):
    """Collect statistics from a VPN provider"""
    try:
        provider = VPNProvider.objects.get(id=provider_id)

        vpn_service = VPNProviderFactory.create(
            provider.provider_type,
            base_url=provider.base_url,
            api_key=provider.api_key,
            **provider.configuration,
        )

        backend_stats = await vpn_service.get_backend_stats()
        system_stats = await vpn_service.get_system_stats()
        online_users = await vpn_service.get_online_users()

        if backend_stats and system_stats:
            VPNProviderStats.objects.create(
                provider=provider,
                collected_at=timezone.now(),
                total_users=len(online_users),
                online_users=len(online_users),
                cpu_usage=system_stats.get("cpu_usage", 0),
                memory_usage=system_stats.get("memory_usage", 0),
                disk_usage=system_stats.get("disk_usage", 0),
                additional_metrics={
                    "backend_stats": backend_stats,
                    "system_stats": system_stats,
                },
            )

            provider.current_users = len(online_users)
            provider.save()

        await vpn_service.close()

    except VPNProvider.DoesNotExist:
        logger.error(f"VPN Provider with id {provider_id} not found")
    except Exception as e:
        logger.error(f"Error collecting stats for provider {provider_id}: {e}")
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@shared_task
def cleanup_old_health_checks():
    """Clean up old health check records"""
    cutoff_date = timezone.now() - timedelta(days=30)
    deleted_count = VPNProviderHealthCheck.objects.filter(
        check_time__lt=cutoff_date
    ).delete()[0]

    logger.info(f"Cleaned up {deleted_count} old health check records")


@shared_task
def cleanup_old_stats():
    """Clean up old statistics records"""
    cutoff_date = timezone.now() - timedelta(days=90)
    deleted_count = VPNProviderStats.objects.filter(
        collected_at__lt=cutoff_date
    ).delete()[0]

    logger.info(f"Cleaned up {deleted_count} old stats records")
