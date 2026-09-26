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
    "cleanup-old-provider-health-checks": {
        "task": "apps.vpn_providers.tasks.cleanup_old_health_checks",
        "schedule": 86400.0,
    },
    "cleanup-old-provider-stats": {
        "task": "apps.vpn_providers.tasks.cleanup_old_stats",
        "schedule": 86400.0,
    },
}

app.conf.timezone = "UTC"
