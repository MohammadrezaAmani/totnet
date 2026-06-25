"""
VPN Provider Models - Abstract layer for multiple VPN panels
Supports: PasarGuard, Marzban, Hiddify, XUI, Connectix and future providers
"""

from django.db import models


class VPNProvider(models.Model):
    """Base VPN provider configuration"""

    class ProviderType(models.TextChoices):
        PASARGUARD = "pasarguard", "PasarGuard"
        MARZBAN = "marzban", "Marzban"
        HIDDIFY = "hiddify", "Hiddify"
        XUI = "xui", "X-UI"
        CONNECTIX = "connectix", "Connectix"
        CUSTOM = "custom", "Custom Provider"

    class ProviderStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        MAINTENANCE = "maintenance", "Maintenance"
        ERROR = "error", "Error"

    name = models.CharField(max_length=100)
    provider_type = models.CharField(max_length=20, choices=ProviderType.choices)
    description = models.TextField(null=True, blank=True)

    base_url = models.URLField()
    api_key = models.CharField(max_length=500)

    configuration = models.JSONField(default=dict, blank=True)

    max_users = models.PositiveIntegerField(default=1000)
    current_users = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20, choices=ProviderStatus.choices, default=ProviderStatus.ACTIVE
    )
    is_default = models.BooleanField(default=False)

    priority = models.PositiveIntegerField(default=1)

    last_health_check = models.DateTimeField(null=True, blank=True)
    health_status = models.CharField(max_length=20, default="unknown")
    response_time = models.PositiveIntegerField(null=True, blank=True)

    total_subscriptions = models.PositiveIntegerField(default=0)

    brand = models.ForeignKey(
        "brands.Brand", on_delete=models.CASCADE, related_name="vpn_providers"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vpn_providers"
        unique_together = ["brand", "name"]
        indexes = [
            models.Index(fields=["brand", "status"]),
            models.Index(fields=["provider_type"]),
            models.Index(fields=["priority"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_provider_type_display()})"


class VPNServer(models.Model):
    """Individual VPN servers/nodes under a provider"""

    class ServerStatus(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        MAINTENANCE = "maintenance", "Maintenance"
        OVERLOADED = "overloaded", "Overloaded"

    provider = models.ForeignKey(
        VPNProvider, on_delete=models.CASCADE, related_name="servers"
    )

    name = models.CharField(max_length=100)
    location = models.CharField(max_length=100)
    country_code = models.CharField(max_length=2)

    ip_address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=443)
    domain = models.CharField(max_length=255, null=True, blank=True)

    max_users = models.PositiveIntegerField(default=100)
    current_users = models.PositiveIntegerField(default=0)

    cpu_usage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    memory_usage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    bandwidth_usage = models.BigIntegerField(default=0)

    status = models.CharField(
        max_length=20, choices=ServerStatus.choices, default=ServerStatus.ONLINE
    )

    inbound_configs = models.JSONField(default=list, blank=True)
    protocols = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vpn_servers"
        unique_together = ["provider", "name"]


class VPNProviderLog(models.Model):
    """Logs for VPN provider operations"""

    class LogLevel(models.TextChoices):
        DEBUG = "debug", "Debug"
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"
        CRITICAL = "critical", "Critical"

    class ActionType(models.TextChoices):
        CREATE_USER = "create_user", "Create User"
        UPDATE_USER = "update_user", "Update User"
        DELETE_USER = "delete_user", "Delete User"
        START_BACKEND = "start_backend", "Start Backend"
        STOP_BACKEND = "stop_backend", "Stop Backend"
        HEALTH_CHECK = "health_check", "Health Check"
        SYNC_USERS = "sync_users", "Sync Users"
        GET_STATS = "get_stats", "Get Statistics"

    provider = models.ForeignKey(
        VPNProvider, on_delete=models.CASCADE, related_name="logs"
    )

    level = models.CharField(max_length=20, choices=LogLevel.choices)
    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    message = models.TextField()

    request_data = models.JSONField(null=True, blank=True)
    response_data = models.JSONField(null=True, blank=True)

    duration = models.PositiveIntegerField(null=True, blank=True)

    error_code = models.CharField(max_length=50, null=True, blank=True)
    stack_trace = models.TextField(null=True, blank=True)

    user_email = models.CharField(max_length=255, null=True, blank=True)
    subscription_id = models.CharField(max_length=100, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vpn_provider_logs"
        indexes = [
            models.Index(fields=["provider", "created_at"]),
            models.Index(fields=["level", "created_at"]),
            models.Index(fields=["action_type"]),
        ]
        ordering = ["-created_at"]


class VPNProviderStats(models.Model):
    """Statistics collected from VPN providers"""

    provider = models.ForeignKey(
        VPNProvider, on_delete=models.CASCADE, related_name="stats"
    )

    collected_at = models.DateTimeField()

    total_users = models.PositiveIntegerField(default=0)
    online_users = models.PositiveIntegerField(default=0)

    total_upload = models.BigIntegerField(default=0)
    total_download = models.BigIntegerField(default=0)

    cpu_usage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    memory_usage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    disk_usage = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    network_upload_speed = models.BigIntegerField(default=0)
    network_download_speed = models.BigIntegerField(default=0)

    additional_metrics = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "vpn_provider_stats"
        unique_together = ["provider", "collected_at"]
        ordering = ["-collected_at"]


class VPNProviderHealthCheck(models.Model):
    """Health check results for VPN providers"""

    provider = models.ForeignKey(
        VPNProvider, on_delete=models.CASCADE, related_name="health_checks"
    )

    check_time = models.DateTimeField()
    is_healthy = models.BooleanField()
    response_time = models.PositiveIntegerField()

    api_accessible = models.BooleanField(default=False)
    backend_running = models.BooleanField(default=False)
    database_accessible = models.BooleanField(default=False)

    error_message = models.TextField(null=True, blank=True)
    status_code = models.PositiveIntegerField(null=True, blank=True)

    version = models.CharField(max_length=50, null=True, blank=True)
    uptime = models.DurationField(null=True, blank=True)

    class Meta:
        db_table = "vpn_provider_health_checks"
        ordering = ["-check_time"]


class VPNProviderInbound(models.Model):
    """Inbound configurations for VPN providers"""

    class ProtocolType(models.TextChoices):
        VMESS = "vmess", "VMess"
        VLESS = "vless", "VLESS"
        TROJAN = "trojan", "Trojan"
        SHADOWSOCKS = "shadowsocks", "Shadowsocks"
        WIREGUARD = "wireguard", "WireGuard"
        HYSTERIA = "hysteria", "Hysteria"

    provider = models.ForeignKey(
        VPNProvider, on_delete=models.CASCADE, related_name="inbounds"
    )

    tag = models.CharField(max_length=100)
    protocol = models.CharField(max_length=20, choices=ProtocolType.choices)
    port = models.PositiveIntegerField()

    settings = models.JSONField(default=dict, blank=True)
    stream_settings = models.JSONField(default=dict, blank=True)

    max_users = models.PositiveIntegerField(default=100)
    current_users = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vpn_provider_inbounds"
        unique_together = ["provider", "tag"]
