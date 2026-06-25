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
    "check-vpn-health": {
        "task": "apps.vpn_providers.tasks.check_all_providers_health",
        "schedule": 300.0,
    },
    "process-subscription-notifications": {
        "task": "apps.subscriptions.tasks.process_subscription_notifications",
        "schedule": 60.0,
    },
    "update-subscription-stats": {
        "task": "apps.subscriptions.tasks.update_all_subscription_stats",
        "schedule": 3600.0,
    },
    "process-broadcast-queue": {
        "task": "apps.broadcasts.tasks.process_broadcast_queue",
        "schedule": 30.0,
    },
    "generate-daily-analytics": {
        "task": "apps.analytics.tasks.generate_daily_analytics",
        "schedule": 3600.0,
    },
}

app.conf.timezone = "UTC"
