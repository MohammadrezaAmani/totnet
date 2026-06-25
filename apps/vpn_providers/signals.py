"""
Signals for VPN Provider integration
Handles automatic sync with Hiddify panel
"""

import logging
from datetime import datetime

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Payment
from apps.subscriptions.models import Subscription

from .models import HiddifyAdmin, VPNProvider

logger = logging.getLogger(__name__)


@receiver(post_save, sender=VPNProvider)
def vpn_provider_created_or_updated(sender, instance, created, **kwargs):
    """Handle VPN Provider creation/update - sync admins"""
    if (
        instance.provider_type == VPNProvider.ProviderType.HIDDIFY
        and instance.status == VPNProvider.ProviderStatus.ACTIVE
    ):
        try:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(sync_hiddify_admins(instance))
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Error syncing Hiddify admins: {e}")


@receiver(post_save, sender=Subscription)
def subscription_created(sender, instance, created, **kwargs):
    """Handle subscription creation - create user in VPN panel"""
    if (
        created
        and instance.vpn_provider
        and instance.status == Subscription.SubscriptionStatus.ACTIVE
    ):
        if instance.vpn_provider.provider_type == VPNProvider.ProviderType.HIDDIFY:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(create_hiddify_user_for_subscription(instance))
            finally:
                loop.close()


@receiver(post_save, sender=Payment)
def payment_status_changed(sender, instance: Payment, created, **kwargs):
    if (
        instance.status == Payment.PaymentStatus.CONFIRMED
        and not instance.order.subscriptions.exists()
    ):
        Subscription.objects.create(
            brand=instance.brand,
            user=instance.user,
            plan=instance.order.plan,
            order=instance.order,
            vpn_provider=VPNProvider.objects.filter(
                brand=instance.brand, provider_type=VPNProvider.ProviderType.HIDDIFY
            ).first(),
            owner=instance.user,
            status=Subscription.SubscriptionStatus.ACTIVE,
            starts_at=datetime.now(),
        )


@receiver(pre_save, sender=Subscription)
def subscription_status_change(sender, instance, **kwargs):
    """Handle subscription status changes - enable/disable in VPN panel"""
    if instance.pk:
        try:
            old_instance = Subscription.objects.get(pk=instance.pk)
            if old_instance.status != instance.status:
                if (
                    instance.vpn_provider
                    and instance.vpn_provider.provider_type
                    == VPNProvider.ProviderType.HIDDIFY
                ):
                    import asyncio

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(update_hiddify_user_status(instance))
                    finally:
                        loop.close()
        except Subscription.DoesNotExist:
            pass


@receiver(post_delete, sender=Subscription)
def subscription_deleted(sender, instance, **kwargs):
    """Handle subscription deletion - remove user from VPN panel"""
    if (
        instance.vpn_provider
        and instance.vpn_provider.provider_type == VPNProvider.ProviderType.HIDDIFY
    ):
        try:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(delete_hiddify_user(instance))
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Error deleting Hiddify user: {e}")


@receiver(post_save, sender=User)
def user_created(sender, instance, created, **kwargs):
    """Handle user creation - check if they're a Hiddify admin"""
    if created and instance.telegram_id:
        try:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(sync_user_with_hiddify_admin(instance))
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Error syncing user with Hiddify admin: {e}")


async def sync_hiddify_admins(provider: VPNProvider):
    """Sync Hiddify panel admins with local database"""
    from .services.hiddify import HiddifyProvider

    hiddify = HiddifyProvider(
        base_url=provider.base_url,
        api_key=provider.api_key,
        proxy_path=provider.proxy_path or "",
        public_api_key=provider.public_api_key,
    )

    try:
        admins = await hiddify.get_all_admins()
        if not admins:
            logger.warning(f"No admins found for Hiddify provider {provider.name}")
            return

        for admin_data in admins:
            admin, created = await HiddifyAdmin.objects.aupdate_or_create(
                uuid=admin_data.uuid,
                defaults={
                    "provider": provider,
                    "name": admin_data.name,
                    "mode": (
                        admin_data.mode.value
                        if hasattr(admin_data.mode, "value")
                        else admin_data.mode
                    ),
                    "lang": (
                        admin_data.lang.value
                        if hasattr(admin_data.lang, "value")
                        else admin_data.lang
                    ),
                    "telegram_id": admin_data.telegram_id,
                    "comment": admin_data.comment,
                    "can_add_admin": admin_data.can_add_admin,
                    "max_users": admin_data.max_users,
                    "max_active_users": admin_data.max_active_users,
                    "is_synced": True,
                    "last_sync": timezone.now(),
                },
            )

            if admin.telegram_id:
                try:
                    local_user = await User.objects.filter(
                        telegram_id=admin.telegram_id
                    ).afirst()
                    if local_user:
                        admin.local_user = local_user
                        await admin.asave()
                except Exception as e:
                    logger.warning(f"Could not link Hiddify admin to local user: {e}")

            logger.info(
                f"{'Created' if created else 'Updated'} Hiddify admin: {admin.name}"
            )

    except Exception as e:
        logger.error(f"Error syncing Hiddify admins: {e}")
    finally:
        await hiddify.close()


async def create_hiddify_user_for_subscription(subscription: Subscription):
    """Create user in Hiddify panel for a subscription"""

    from .services.hiddify import (
        HiddifyLanguage,
        HiddifyProvider,
        HiddifyUser,
        HiddifyUserMode,
    )

    provider = subscription.vpn_provider
    hiddify = HiddifyProvider(
        base_url=provider.base_url,
        api_key=provider.api_key,
        proxy_path=provider.proxy_path or "",
        public_api_key=provider.public_api_key,
    )

    try:
        package_days = None
        if subscription.expires_at and subscription.starts_at:
            package_days = (subscription.expires_at - subscription.starts_at).days

        user = HiddifyUser(
            name=f"user_{subscription.user.telegram_id}_{subscription.id}",
            telegram_id=subscription.user.telegram_id,
            usage_limit_GB=subscription.traffic_limit_gb,
            package_days=package_days,
            start_date=(
                subscription.starts_at.date() if subscription.starts_at else None
            ),
            mode=HiddifyUserMode.NO_RESET,
            enable=True,
            is_active=True,
            lang=HiddifyLanguage.FA,
            comment=f"Subscription {subscription.subscription_id} - Brand: {subscription.brand.name}",
        )

        created_user = await hiddify.create_hiddify_user(user)

        if created_user and created_user.uuid:
            if not subscription.connection_configs:
                subscription.connection_configs = {}
            subscription.connection_configs["secret_uuid"] = str(created_user.uuid)
            subscription.connection_configs["hiddify_uuid"] = str(created_user.uuid)
            subscription.connection_configs["created_at"] = timezone.now().isoformat()
            await subscription.asave()

            provider.current_users += 1
            provider.total_subscriptions += 1
            await provider.asave()

            logger.info(
                f"Created Hiddify user {created_user.uuid} for subscription {subscription.subscription_id}"
            )
        else:
            logger.error(
                f"Failed to create Hiddify user for subscription {subscription.subscription_id}"
            )

    except Exception as e:
        logger.error(f"Error creating Hiddify user: {e}")
    finally:
        await hiddify.close()


async def update_hiddify_user_status(subscription: Subscription):
    """Update user status in Hiddify panel"""
    from .services.hiddify import HiddifyProvider

    if not subscription.connection_configs:
        return

    uuid = subscription.connection_configs.get(
        "hiddify_uuid"
    ) or subscription.connection_configs.get("secret_uuid")
    if not uuid:
        return

    provider = subscription.vpn_provider
    hiddify = HiddifyProvider(
        base_url=provider.base_url,
        api_key=provider.api_key,
        proxy_path=provider.proxy_path or "",
        public_api_key=provider.public_api_key,
    )

    try:
        is_active = subscription.status == Subscription.SubscriptionStatus.ACTIVE

        current_user = await hiddify.get_user(uuid)
        if current_user:
            current_user.enable = is_active
            current_user.is_active = is_active
            await hiddify.update_hiddify_user(uuid, current_user)
            logger.info(f"Updated Hiddify user {uuid} status to {subscription.status}")

    except Exception as e:
        logger.error(f"Error updating Hiddify user status: {e}")
    finally:
        await hiddify.close()


async def delete_hiddify_user(subscription: Subscription):
    """Delete user from Hiddify panel"""
    from .services.hiddify import HiddifyProvider

    if not subscription.connection_configs:
        return

    uuid = subscription.connection_configs.get(
        "hiddify_uuid"
    ) or subscription.connection_configs.get("secret_uuid")
    if not uuid:
        return

    provider = subscription.vpn_provider
    hiddify = HiddifyProvider(
        base_url=provider.base_url,
        api_key=provider.api_key,
        proxy_path=provider.proxy_path or "",
        public_api_key=provider.public_api_key,
    )

    try:
        await hiddify.delete_hiddify_user(uuid)

        if provider.current_users > 0:
            provider.current_users -= 1
        await provider.asave()

        logger.info(f"Deleted Hiddify user {uuid}")

    except Exception as e:
        logger.error(f"Error deleting Hiddify user: {e}")
    finally:
        await hiddify.close()


async def sync_user_with_hiddify_admin(user: User):
    """Check if user is a Hiddify admin and link accounts"""
    if not user.telegram_id:
        return

    admin = await HiddifyAdmin.objects.filter(telegram_id=user.telegram_id).afirst()

    if admin and not admin.local_user:
        admin.local_user = user
        await admin.asave()
        logger.info(f"Linked Hiddify admin {admin.name} to user {user.username}")
