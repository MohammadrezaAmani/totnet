"""
Base VPN Provider Service for Multi-Tenant VPN Platform
Abstract interface for all VPN provider integrations
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class VPNUser:
    """VPN User data structure"""

    email: str
    proxies: Dict[str, Any]
    inbounds: List[str]
    traffic_limit: Optional[int] = None  # bytes
    expire_time: Optional[datetime] = None
    enable: bool = True


@dataclass
class VPNStats:
    """VPN Statistics data structure"""

    upload: int  # bytes
    download: int  # bytes
    total: int  # bytes
    online: bool = False


@dataclass
class VPNServerInfo:
    """VPN Server information"""

    version: str
    started: bool
    users_count: int
    traffic_stats: Dict[str, Any]


@dataclass
class VPNConfig:
    """VPN Configuration data structure"""

    subscription_url: str
    configs: Dict[str, str]  # Protocol -> Config URL
    qr_codes: List[str]


class BaseVPNProvider(ABC):
    """Base class for all VPN provider integrations"""

    def __init__(self, base_url: str, api_key: str, **kwargs):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.config = kwargs
        self.session_timeout = kwargs.get("timeout", 30)

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test connection to VPN provider"""
        pass

    @abstractmethod
    async def get_server_info(self) -> VPNServerInfo:
        """Get server information"""
        pass

    @abstractmethod
    async def create_user(self, user: VPNUser) -> bool:
        """Create a new VPN user"""
        pass

    @abstractmethod
    async def update_user(self, user: VPNUser) -> bool:
        """Update existing VPN user"""
        pass

    @abstractmethod
    async def delete_user(self, email: str) -> bool:
        """Delete VPN user"""
        pass

    @abstractmethod
    async def get_user_stats(
        self, email: str, reset: bool = False
    ) -> Optional[VPNStats]:
        """Get user traffic statistics"""
        pass

    @abstractmethod
    async def get_user_config(self, email: str) -> Optional[VPNConfig]:
        """Get user connection configuration"""
        pass

    @abstractmethod
    async def sync_users(self, users: List[VPNUser]) -> bool:
        """Sync multiple users at once"""
        pass

    @abstractmethod
    async def get_online_users(self) -> List[str]:
        """Get list of online user emails"""
        pass

    @abstractmethod
    async def start_backend(self) -> bool:
        """Start VPN backend service"""
        pass

    @abstractmethod
    async def stop_backend(self) -> bool:
        """Stop VPN backend service"""
        pass

    async def health_check(self) -> Dict[str, Any]:
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
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers with authentication"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/x-protobuf",
            "Accept": "application/x-protobuf",
        }

    def _log_request(self, method: str, url: str, data: Any = None):
        """Log API request"""
        logger.debug(f"VPN API Request: {method} {url}")
        if data:
            logger.debug(f"Request data: {data}")

    def _log_response(self, url: str, status_code: int, response_data: Any = None):
        """Log API response"""
        logger.debug(f"VPN API Response: {url} - Status: {status_code}")
        if response_data:
            logger.debug(f"Response data: {response_data}")

    def _log_error(self, operation: str, error: Exception):
        """Log operation error"""
        logger.error(f"VPN Provider Error ({operation}): {error}")


class VPNProviderFactory:
    """Factory for creating VPN provider instances"""

    _providers = {}

    @classmethod
    def register(cls, provider_type: str, provider_class):
        """Register a provider class"""
        cls._providers[provider_type] = provider_class

    @classmethod
    def create(cls, provider_type: str, **kwargs) -> BaseVPNProvider:
        """Create provider instance"""
        if provider_type not in cls._providers:
            raise ValueError(f"Unknown provider type: {provider_type}")

        return cls._providers[provider_type](**kwargs)

    @classmethod
    def get_available_providers(cls) -> List[str]:
        """Get list of available provider types"""
        return list(cls._providers.keys())
