"""
Base VPN Provider Service for Multi-Tenant VPN Platform
Abstract interface for all VPN provider integrations
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


@dataclass
class VPNUser:
    """VPN User data structure"""

    email: str
    proxies: dict[str, Any]
    inbounds: list[str]
    traffic_limit: int | None = None
    expire_time: datetime | None = None
    enable: bool = True


@dataclass
class VPNStats:
    """VPN Statistics data structure"""

    upload: int
    download: int
    total: int
    online: bool = False


@dataclass
class VPNServerInfo:
    """VPN Server information"""

    version: str
    started: bool
    users_count: int
    traffic_stats: dict[str, Any]


@dataclass
class VPNConfig:
    """VPN Configuration data structure"""

    subscription_url: str
    configs: dict[str, str]
    qr_codes: list[str]


class BaseVPNProvider(ABC):
    """Base class for all VPN provider integrations"""

    def __init__(
        self, base_url: str, api_key: str, public_api_key: str | None = None, **kwargs
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.public_api_key = public_api_key
        self.config = kwargs
        self.session_timeout = kwargs.get("timeout", 30)

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test connection to VPN provider"""

    @abstractmethod
    async def get_server_info(self) -> VPNServerInfo:
        """Get server information"""

    @abstractmethod
    async def create_user(self, user: VPNUser) -> bool:
        """Create a new VPN user"""

    @abstractmethod
    async def update_user(self, user: VPNUser) -> bool:
        """Update existing VPN user"""

    @abstractmethod
    async def delete_user(self, email: str) -> bool:
        """Delete VPN user"""

    @abstractmethod
    async def get_user_stats(self, email: str, reset: bool = False) -> VPNStats | None:
        """Get user traffic statistics"""

    @abstractmethod
    async def get_user_config(self, email: str) -> VPNConfig | None:
        """Get user connection configuration"""

    @abstractmethod
    async def sync_users(self, users: list[VPNUser]) -> bool:
        """Sync multiple users at once"""

    @abstractmethod
    async def get_online_users(self) -> list[str]:
        """Get list of online user emails"""

    @abstractmethod
    async def start_backend(self) -> bool:
        """Start VPN backend service"""

    @abstractmethod
    async def stop_backend(self) -> bool:
        """Stop VPN backend service"""

    async def health_check(self) -> dict[str, Any]:
        """Perform health check"""
        try:
            start_time = datetime.now()
            connection_ok = await self.test_connection()
            response_time = (datetime.now() - start_time).total_seconds() * 1000

            if connection_ok:
                server_info = await self.get_server_info()
                return {
                    "healthy": True,
                    "response_time_ms": response_time,
                    "server_info": server_info,
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                return {
                    "healthy": False,
                    "response_time_ms": response_time,
                    "error": "Connection failed",
                    "timestamp": datetime.now().isoformat(),
                }
        except Exception as e:
            return {
                "healthy": False,
                "error": type(e).__name__,
                "timestamp": datetime.now().isoformat(),
            }

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers with authentication"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/x-protobuf",
            "Accept": "application/x-protobuf",
        }

    def _log_request(self, method: str, url: str, data: Any = None):
        """Log API request"""
        logger.debug("VPN API request: %s %s", method, urlsplit(url).path)

    def _log_response(self, url: str, status_code: int, response_data: Any = None):
        """Log API response"""
        logger.debug("VPN API response: %s status=%s", urlsplit(url).path, status_code)

    def _log_error(self, operation: str, error: Exception):
        """Log operation error"""
        logger.error(
            "VPN provider operation %s failed (%s)", operation, type(error).__name__
        )

    @abstractmethod
    async def get_token(self) -> str | None:
        """Login and get token from provider"""


class VPNProviderFactory:
    """Factory for creating VPN provider instances"""

    _providers = {}

    @classmethod
    def register(cls, provider_type: str, provider_class):
        """Register a provider class"""
        cls._providers[provider_type] = provider_class

    @classmethod
    def create(cls, provider_type: str, **kwargs):
        """Create provider instance"""
        # Registration must not depend on an unrelated module being imported first.
        if provider_type not in cls._providers:
            modules = {
                "hiddify": "apps.vpn_providers.services.hiddify",
                "pasarguard": "apps.vpn_providers.services.pasarguard",
                "connectix": "apps.vpn_providers.services.connectix",
            }
            module = modules.get(provider_type)
            if module:
                import_module(module)
        if provider_type not in cls._providers:
            raise ValueError(
                f"Provider type '{provider_type}' is not implemented or registered"
            )

        return cls._providers[provider_type](**kwargs)

    @classmethod
    def get_available_providers(cls) -> list[str]:
        """Get list of available provider types"""
        for module in (
            "apps.vpn_providers.services.hiddify",
            "apps.vpn_providers.services.pasarguard",
            "apps.vpn_providers.services.connectix",
        ):
            import_module(module)
        return list(cls._providers.keys())
