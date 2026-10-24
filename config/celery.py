"""
Celery configuration for Multi-Tenant VPN Platform
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("doctor_vpn_platform")


app.config_from_object("django.conf:settings", namespace="CELERY")


app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")


app.conf.beat_schedule = {
    "sync-vpn-users": {
        "task": "apps.vpn_providers.tasks.sync_all_vpn_users",
        "schedule": 300.0,
    },
    "sync-connectix-plans": {
        "task": "apps.vpn_providers.tasks.sync_all_connectix_plans",
        "schedule": 900.0,
    },
    "check-vpn-health": {
        "task": "apps.vpn_providers.tasks.check_all_providers_health",
        "schedule": 300.0,
    },
    "cleanup-old-provider-health-checks": {
        "task": "apps.vpn_providers.tasks.cleanup_old_health_checks",
        "schedule": 86400.0,
    },
    "cleanup-old-provider-stats": {
        "task": "apps.vpn_providers.tasks.cleanup_old_stats",
        "schedule": 86400.0,
    },
    "recover-confirmed-admin-receipt-deletions": {
        "task": "apps.orders.tasks.recover_confirmed_admin_receipt_deletions",
        "schedule": 300.0,
    },
    "recover-pending-referral-rewards": {
        "task": "apps.referrals.tasks.recover_pending_referral_rewards",
        "schedule": 300.0,
    },
    "recover-referrer-join-notifications": {
        "task": "apps.referrals.tasks.recover_missing_referrer_join_notifications",
        "schedule": 300.0,
    },
    "delete-expired-referrer-join-messages": {
        "task": "apps.referrals.tasks.delete_expired_referrer_join_messages",
        "schedule": 60.0,
    },
    "recover-referrer-purchase-notifications": {
        "task": "apps.referrals.tasks.recover_missing_referrer_purchase_notifications",
        "schedule": 300.0,
    },
    "run-gamification-notifications": {
        "task": "apps.referrals.tasks.run_gamification_notifications",
        "schedule": 60.0,
    },
}

app.conf.timezone = "UTC"
