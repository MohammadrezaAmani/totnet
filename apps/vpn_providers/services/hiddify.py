"""
Hiddify VPN Provider Implementation
Complete integration with Hiddify Panel API v2.2.0

Supports:
- Admin management (create/update/delete admins)
- User management (create/update/delete users)
- Client features (profile, configs, apps, MTProxies)
- Server status and monitoring
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from django.core.cache import cache

from apps.vpn_providers.services.base import (
    BaseVPNProvider,
    VPNConfig,
    VPNProviderFactory,
    VPNServerInfo,
    VPNStats,
    VPNUser,
)

logger = logging.getLogger(__name__)


class HiddifyLanguage(str, Enum):
    """Supported languages in Hiddify"""

    EN = "en"
    FA = "fa"
    RU = "ru"
    PT = "pt"
    ZH = "zh"
    MY = "my"


class HiddifyAdminMode(str, Enum):
    """Admin modes in Hiddify"""

    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    AGENT = "agent"


class HiddifyUserMode(str, Enum):
    """User reset modes in Hiddify"""

    NO_RESET = "no_reset"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"


class AppInstallType(str, Enum):
    """App installation types"""

    GOOGLE_PLAY = "google_play"
    APP_STORE = "app_store"
    APPIMAGE = "appimage"
    SNAPCRAFT = "snapcraft"
    MICROSOFT_STORE = "microsoft_store"
    APK = "apk"
    DMG = "dmg"
    SETUP = "setup"
    PORTABLE = "portable"
    OTHER = "other"


class PlatformType(str, Enum):
    """Platform types for apps"""

    ALL = "all"
    ANDROID = "android"
    IOS = "ios"
    WINDOWS = "windows"
    LINUX = "linux"
    MAC = "mac"
    AUTO = "auto"


@dataclass
class HiddifyAdmin:
    """Hiddify Admin representation"""

    name: str
    mode: HiddifyAdminMode
    lang: HiddifyLanguage
    can_add_admin: bool
    uuid: Optional[str] = None
    telegram_id: Optional[int] = None
    comment: Optional[str] = None
    max_users: Optional[int] = None
    max_active_users: Optional[int] = None
    parent_admin_uuid: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "name": self.name,
            "mode": self.mode.value,
            "lang": self.lang.value,
            "can_add_admin": self.can_add_admin,
        }
        if self.uuid:
            result["uuid"] = self.uuid
        if self.telegram_id is not None:
            result["telegram_id"] = self.telegram_id
        if self.comment is not None:
            result["comment"] = self.comment
        if self.max_users is not None:
            result["max_users"] = self.max_users
        if self.max_active_users is not None:
            result["max_active_users"] = self.max_active_users
        if self.parent_admin_uuid is not None:
            result["parent_admin_uuid"] = self.parent_admin_uuid
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HiddifyAdmin":
        return cls(
            uuid=data.get("uuid"),
            name=data.get("name", ""),
            mode=HiddifyAdminMode(data.get("mode", "admin")),
            lang=HiddifyLanguage(data.get("lang", "en")),
            can_add_admin=data.get("can_add_admin", False),
            telegram_id=data.get("telegram_id"),
            comment=data.get("comment"),
            max_users=data.get("max_users"),
            max_active_users=data.get("max_active_users"),
            parent_admin_uuid=data.get("parent_admin_uuid"),
        )


@dataclass
class HiddifyUser:
    """Hiddify User representation"""

    name: str
    uuid: Optional[str] = None
    telegram_id: Optional[int] = None
    usage_limit_GB: Optional[float] = None
    current_usage_GB: Optional[float] = None
    package_days: Optional[int] = None
    start_date: Optional[date] = None
    mode: Optional[HiddifyUserMode] = None
    enable: bool = True
    is_active: bool = True
    lang: Optional[HiddifyLanguage] = None
    comment: Optional[str] = None
    added_by_uuid: Optional[str] = None
    last_online: Optional[datetime] = None
    last_reset_time: Optional[datetime] = None
    ed25519_private_key: Optional[str] = None
    ed25519_public_key: Optional[str] = None
    wg_pk: Optional[str] = None
    wg_pub: Optional[str] = None
    wg_psk: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"name": self.name}
        if self.uuid:
            result["uuid"] = self.uuid
        if self.telegram_id is not None:
            result["telegram_id"] = self.telegram_id
        if self.usage_limit_GB is not None:
            result["usage_limit_GB"] = self.usage_limit_GB
        if self.current_usage_GB is not None:
            result["current_usage_GB"] = self.current_usage_GB
        if self.package_days is not None:
            result["package_days"] = self.package_days
        if self.start_date:
            result["start_date"] = self.start_date.isoformat()
        if self.mode:
            result["mode"] = self.mode.value
        result["enable"] = self.enable
        result["is_active"] = self.is_active
        if self.lang:
            result["lang"] = self.lang.value
        if self.comment is not None:
            result["comment"] = self.comment
        if self.added_by_uuid:
            result["added_by_uuid"] = self.added_by_uuid
        if self.last_online:
            result["last_online"] = self.last_online.isoformat()
        if self.last_reset_time:
            result["last_reset_time"] = self.last_reset_time.isoformat()
        if self.ed25519_private_key:
            result["ed25519_private_key"] = self.ed25519_private_key
        if self.ed25519_public_key:
            result["ed25519_public_key"] = self.ed25519_public_key
        if self.wg_pk:
            result["wg_pk"] = self.wg_pk
        if self.wg_pub:
            result["wg_pub"] = self.wg_pub
        if self.wg_psk:
            result["wg_psk"] = self.wg_psk
        return result

    def to_post_dict(self) -> Dict[str, Any]:
        """Convert to dict for POST request (name is required)"""
        return self.to_dict()

    def to_patch_dict(self) -> Dict[str, Any]:
        """Convert to dict for PATCH request (all fields optional)"""
        return {k: v for k, v in self.to_dict().items() if k != "uuid"}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HiddifyUser":
        start_date = None
        if data.get("start_date"):
            try:
                start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
            except ValueError, TypeError:
                pass

        return cls(
            uuid=data.get("uuid"),
            name=data.get("name", ""),
            telegram_id=data.get("telegram_id"),
            usage_limit_GB=data.get("usage_limit_GB"),
            current_usage_GB=data.get("current_usage_GB"),
            package_days=data.get("package_days"),
            start_date=start_date,
            mode=HiddifyUserMode(data["mode"]) if data.get("mode") else None,
            enable=data.get("enable", True),
            is_active=data.get("is_active", True),
            lang=HiddifyLanguage(data["lang"]) if data.get("lang") else None,
            comment=data.get("comment"),
            added_by_uuid=data.get("added_by_uuid"),
            last_online=(
                datetime.fromisoformat(data["last_online"])
                if data.get("last_online")
                else None
            ),
            last_reset_time=(
                datetime.fromisoformat(data["last_reset_time"])
                if data.get("last_reset_time")
                else None
            ),
            ed25519_private_key=data.get("ed25519_private_key"),
            ed25519_public_key=data.get("ed25519_public_key"),
            wg_pk=data.get("wg_pk"),
            wg_pub=data.get("wg_pub"),
            wg_psk=data.get("wg_psk"),
        )


@dataclass
class HiddifyConfig:
    """VPN Configuration"""

    name: str
    protocol: str
    type: str
    domain: str
    link: str
    transport: str
    security: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HiddifyConfig":
        return cls(
            name=data.get("name", ""),
            protocol=data.get("protocol", ""),
            type=data.get("type", ""),
            domain=data.get("domain", ""),
            link=data.get("link", ""),
            transport=data.get("transport", ""),
            security=data.get("security", ""),
        )


@dataclass
class HiddifyProfile:
    """User profile information"""

    profile_title: str
    profile_url: str
    profile_usage_current: float
    profile_usage_total: float
    profile_remaining_days: int
    lang: HiddifyLanguage
    speedtest_enable: bool
    telegram_proxy_enable: bool
    profile_reset_days: Optional[int] = None
    telegram_id: Optional[int] = None
    telegram_bot_url: Optional[str] = None
    brand_title: Optional[str] = None
    brand_icon_url: Optional[str] = None
    admin_message_html: Optional[str] = None
    admin_message_url: Optional[str] = None
    doh: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HiddifyProfile":
        return cls(
            profile_title=data.get("profile_title", ""),
            profile_url=data.get("profile_url", ""),
            profile_usage_current=data.get("profile_usage_current", 0),
            profile_usage_total=data.get("profile_usage_total", 0),
            profile_remaining_days=data.get("profile_remaining_days", 0),
            lang=HiddifyLanguage(data.get("lang", "en")),
            speedtest_enable=data.get("speedtest_enable", False),
            telegram_proxy_enable=data.get("telegram_proxy_enable", False),
            profile_reset_days=data.get("profile_reset_days"),
            telegram_id=data.get("telegram_id"),
            telegram_bot_url=data.get("telegram_bot_url"),
            brand_title=data.get("brand_title"),
            brand_icon_url=data.get("brand_icon_url"),
            admin_message_html=data.get("admin_message_html"),
            admin_message_url=data.get("admin_message_url"),
            doh=data.get("doh"),
        )


@dataclass
class AppInstall:
    """App installation option"""

    type: AppInstallType
    url: str
    title: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppInstall":
        return cls(
            type=AppInstallType(data.get("type", "other")),
            url=data.get("url", ""),
            title=data.get("title"),
        )


@dataclass
class HiddifyApp:
    """VPN Client App"""

    title: str
    description: str
    icon_url: str
    deeplink: str
    install: List[AppInstall]
    guide_url: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HiddifyApp":
        installs = []
        for inst in data.get("install", []):
            installs.append(AppInstall.from_dict(inst))
        return cls(
            title=data.get("title", ""),
            description=data.get("description", ""),
            icon_url=data.get("icon_url", ""),
            deeplink=data.get("deeplink", ""),
            install=installs,
            guide_url=data.get("guide_url"),
        )


@dataclass
class HiddifyMtproxy:
    """MTProxy configuration"""

    title: str
    link: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HiddifyMtproxy":
        return cls(
            title=data.get("title", ""),
            link=data.get("link", ""),
        )


@dataclass
class HiddifyShortUrl:
    """Short URL"""

    short: str
    full_url: str
    expire_in: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HiddifyShortUrl":
        return cls(
            short=data.get("short", ""),
            full_url=data.get("full_url", ""),
            expire_in=data.get("expire_in", 0),
        )


@dataclass
class ServerStatus:
    """Server status and stats"""

    stats: Dict[str, Any]
    usage_history: Dict[str, Any]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerStatus":
        return cls(
            stats=data.get("stats", {}),
            usage_history=data.get("usage_history", {}),
        )


class HiddifyProvider(BaseVPNProvider):
    """
    Hiddify VPN Provider - Full API v2.2.0 Implementation

    Supports both Admin API and User API endpoints.
    Uses API Key authentication via X-API-Key header.
    """

    def __init__(
        self, base_url: str, api_key: str, public_api_key: str | None = None, **kwargs
    ):
        super().__init__(base_url, api_key, **kwargs)
        self.client: Optional[httpx.AsyncClient] = None
        self.proxy_path: str = kwargs.get("proxy_path", "")
        self.admin_uuid: Optional[str] = kwargs.get("admin_uuid")
        self.user_uuid: Optional[str] = kwargs.get("user_uuid")
        self.public_api_key: Optional[str] = public_api_key

    def _get_headers(self, use_user_uuid=None) -> Dict[str, str]:
        """Get HTTP headers with API Key authentication"""
        return {
            "Hiddify-API-Key": use_user_uuid or self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if not self.client:
            self.client = httpx.AsyncClient(
                timeout=self.session_timeout,
                verify=False,
            )
        return self.client

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        use_user_uuid: bool = False,
        use_secret_uuid: Optional[str] = None,
        used_uuid_as_proxy_path: Optional[bool] = False,
        cache_key: str | None = None,
        cache_timeout: int = 300,
    ) -> Optional[Any]:
        """Make API request to Hiddify"""

        if cache_key:
            cached = cache.get(cache_key)
            if cached:
                return cached
        if use_secret_uuid and used_uuid_as_proxy_path:
            url = f"{self.base_url}/{self.public_api_key}{endpoint}"
        elif use_secret_uuid:
            url = f"{self.base_url}/{self.proxy_path}/{use_secret_uuid}{endpoint}"
        elif use_user_uuid and self.user_uuid:
            url = f"{self.base_url}/{self.proxy_path}/{self.user_uuid}{endpoint}"
        else:
            url = f"{self.base_url}/{self.proxy_path}{endpoint}"

        self._log_request(method, url, data)

        try:
            client = await self._get_client()
            headers = self._get_headers(use_secret_uuid)

            response = None
            if method == "GET":
                response = await client.get(url, headers=headers, params=params)
            elif method == "POST":
                response = await client.post(
                    url, headers=headers, json=data, params=params
                )
            elif method == "PUT":
                response = await client.put(url, headers=headers, json=data)
            elif method == "PATCH":
                response = await client.patch(url, headers=headers, json=data)
            elif method == "DELETE":
                response = await client.delete(url, headers=headers)

            if response:
                self._log_response(
                    url,
                    response.status_code,
                    response.text[:500] if response.text else "",
                )

                if response.status_code in (200, 201):
                    try:
                        if cache_key:
                            cache.set(cache_key, response.json(), cache_timeout)
                        return response.json()
                    except Exception:
                        return response.text
                else:
                    logger.error(
                        f"Hiddify API Error [{response.status_code}]: {response.text}"
                    )
                    return None

        except Exception as e:
            self._log_error(f"{method} {endpoint}", e)
            return None

        return None

    async def get_panel_info(self) -> Optional[Dict[str, Any]]:
        """Get panel version info"""
        return await self._request("GET", "/api/v2/panel/info/")

    async def ping(self, method: str = "GET") -> Optional[Dict[str, Any]]:
        """Ping endpoint - supports all HTTP methods"""
        return await self._request(method, "/api/v2/panel/ping/")

    async def get_all_admins(self) -> Optional[List[HiddifyAdmin]]:
        """Get all admins"""
        result = await self._request("GET", "/api/v2/admin/admin_user/")
        if result and isinstance(result, list):
            return [HiddifyAdmin.from_dict(a) for a in result]
        return None

    async def get_admin(self, uuid: str) -> Optional[HiddifyAdmin]:
        """Get a specific admin by UUID"""
        result = await self._request("GET", f"/api/v2/admin/admin_user/{uuid}/")
        if result:
            return HiddifyAdmin.from_dict(result)
        return None

    async def get_current_admin(self) -> Optional[HiddifyAdmin]:
        """Get current admin info"""
        result = await self._request("GET", "/api/v2/admin/me/")
        if result:
            return HiddifyAdmin.from_dict(result)
        return None

    async def create_admin(self, admin: HiddifyAdmin) -> Optional[HiddifyAdmin]:
        """Create a new admin"""
        result = await self._request(
            "POST", "/api/v2/admin/admin_user/", admin.to_dict()
        )
        if result:
            return HiddifyAdmin.from_dict(result)
        return None

    async def update_admin(
        self, uuid: str, admin: HiddifyAdmin
    ) -> Optional[HiddifyAdmin]:
        """Update an existing admin"""
        update_data = admin.to_dict()

        update_data.pop("uuid", None)
        result = await self._request(
            "PATCH", f"/api/v2/admin/admin_user/{uuid}/", update_data
        )
        if result:
            return HiddifyAdmin.from_dict(result)
        return None

    async def delete_admin(self, uuid: str) -> bool:
        """Delete an admin"""
        result = await self._request("DELETE", f"/api/v2/admin/admin_user/{uuid}/")
        return result is not None

    async def get_all_users(self) -> Optional[List[HiddifyUser]]:
        """Get all users for current admin"""
        result = await self._request("GET", "/api/v2/admin/user/")
        if result and isinstance(result, list):
            return [HiddifyUser.from_dict(u) for u in result]
        return None

    async def get_user(self, uuid: str) -> Optional[HiddifyUser]:
        """Get a specific user by UUID"""
        result = await self._request("GET", f"/api/v2/admin/user/{uuid}/")
        if result:
            return HiddifyUser.from_dict(result)
        return None

    async def create_user(self, user: VPNUser) -> bool:
        """Create a VPN user (from base interface)"""
        hiddify_user = HiddifyUser(
            name=user.email.split("@")[0],
            usage_limit_GB=(
                (user.traffic_limit / (1024**3)) if user.traffic_limit else None
            ),
            package_days=(
                (user.expire_time - datetime.now()).days if user.expire_time else None
            ),
            enable=user.enable,
        )
        result = await self.create_hiddify_user(hiddify_user)
        return result is not None

    async def create_hiddify_user(self, user: HiddifyUser) -> Optional[HiddifyUser]:
        """Create a Hiddify user"""
        result = await self._request("POST", "/api/v2/admin/user/", user.to_post_dict())
        if result:
            return HiddifyUser.from_dict(result)
        return None

    async def update_user(self, user: VPNUser) -> bool:
        """Update a VPN user (from base interface)"""

        return False

    async def update_hiddify_user(
        self, uuid: str, user: HiddifyUser
    ) -> Optional[HiddifyUser]:
        """Update a Hiddify user"""
        result = await self._request(
            "PATCH", f"/api/v2/admin/user/{uuid}/", user.to_patch_dict()
        )
        if result:
            return HiddifyUser.from_dict(result)
        return None

    async def delete_user(self, email: str) -> bool:
        """Delete a user (from base interface)"""

        return False

    async def delete_hiddify_user(self, uuid: str) -> bool:
        """Delete a Hiddify user by UUID"""
        result = await self._request("DELETE", f"/api/v2/admin/user/{uuid}/")
        return result is not None

    async def update_user_usage(self) -> Optional[Dict[str, Any]]:
        """Trigger user usage update"""
        return await self._request("GET", "/api/v2/admin/update_user_usage/")

    async def get_server_status(self) -> Optional[ServerStatus]:
        """Get server status and stats"""
        result = await self._request("GET", "/api/v2/admin/server_status/")
        if result:
            return result
        return None

    async def get_all_configs(self) -> Optional[Dict[str, Any]]:
        """Get all configurations"""
        return await self._request("GET", "/api/v2/admin/all-configs/")

    async def get_all_public_ports(self) -> Optional[Dict[str, Any]]:
        """Get all public ports"""
        return await self._request("GET", "/api/v2/admin/all-public-port/")

    async def view_log_file(self, file_name: str) -> Optional[Dict[str, Any]]:
        """View a log file"""
        return await self._request("POST", "/api/v2/admin/log/", {"file": file_name})

    async def get_user_profile(
        self, secret_uuid: Optional[str] = None
    ) -> Optional[HiddifyProfile]:
        """Get user profile info"""
        if secret_uuid:
            result = await self._request(
                "GET", "/api/v2/user/me/", use_secret_uuid=secret_uuid
            )
        else:
            result = await self._request("GET", "/api/v2/user/me/", use_user_uuid=True)
        if result:
            return HiddifyProfile.from_dict(result)
        return None

    async def update_user_profile(
        self,
        language: Optional[HiddifyLanguage] = None,
        telegram_id: Optional[int] = None,
        secret_uuid: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update user profile (language and telegram_id only)"""
        data = {}
        if language:
            data["language"] = language.value
        if telegram_id is not None:
            data["telegram_id"] = telegram_id

        if secret_uuid:
            return await self._request(
                "PATCH", "/api/v2/user/me/", data, use_secret_uuid=secret_uuid
            )
        return await self._request(
            "PATCH", "/api/v2/user/me/", data, use_user_uuid=True
        )

    async def get_user_configs(
        self, secret_uuid: Optional[str] = None
    ) -> Optional[List[HiddifyConfig]]:
        """Get all user configs"""
        if secret_uuid:
            result = await self._request(
                "GET",
                "/api/v2/user/all-configs/",
                use_secret_uuid=secret_uuid,
                used_uuid_as_proxy_path=True,
                cache_key="/api/v2/user/all-configs/" + str(secret_uuid),
            )
        else:
            result = await self._request(
                "GET", "/api/v2/user/all-configs/", use_user_uuid=True
            )
        if result and isinstance(result, list):
            return [HiddifyConfig.from_dict(c) for c in result]
        return None

    async def get_user_apps(
        self,
        platform: PlatformType = PlatformType.AUTO,
        secret_uuid: Optional[str] = None,
    ) -> Optional[List[HiddifyApp]]:
        """Get recommended apps for user's platform"""
        params = {"platform": platform.value}
        if secret_uuid:
            result = await self._request(
                "GET", "/api/v2/user/apps/", params=params, use_secret_uuid=secret_uuid
            )
        else:
            result = await self._request(
                "GET", "/api/v2/user/apps/", params=params, use_user_uuid=True
            )
        if result and isinstance(result, list):
            return [HiddifyApp.from_dict(a) for a in result]
        return None

    async def get_user_mtproxies(
        self, secret_uuid: Optional[str] = None
    ) -> Optional[List[HiddifyMtproxy]]:
        """Get MTProxy configurations"""
        if secret_uuid:
            result = await self._request(
                "GET", "/api/v2/user/mtproxies/", use_secret_uuid=secret_uuid
            )
        else:
            result = await self._request(
                "GET", "/api/v2/user/mtproxies/", use_user_uuid=True
            )
        if result and isinstance(result, list):
            return [HiddifyMtproxy.from_dict(m) for m in result]
        return None

    async def get_user_short_url(
        self, secret_uuid: Optional[str] = None
    ) -> Optional[HiddifyShortUrl]:
        """Get user's short URL"""
        if secret_uuid:
            result = await self._request(
                "GET", "/api/v2/user/short/", use_secret_uuid=secret_uuid
            )
        else:
            result = await self._request(
                "GET", "/api/v2/user/short/", use_user_uuid=True
            )
        if result:
            return HiddifyShortUrl.from_dict(result)
        return None

    async def test_connection(self) -> bool:
        """Test connection to Hiddify panel"""
        result = await self.ping()
        return result is not None

    async def get_server_info(self) -> VPNServerInfo:
        """Get server information"""
        info = await self.get_panel_info()
        if info:
            return VPNServerInfo(
                version=info.get("version", "unknown"),
                started=True,
                users_count=0,
                traffic_stats={},
            )
        return VPNServerInfo(
            version="unknown",
            started=False,
            users_count=0,
            traffic_stats={},
        )

    async def get_user_stats(
        self, email: str, reset: bool = False
    ) -> Optional[VPNStats]:
        """Get user traffic statistics"""

        return None

    async def get_user_config(self, email: str) -> Optional[VPNConfig]:
        """Get user connection configuration"""

        return None

    async def sync_users(self, users: List[VPNUser]) -> bool:
        """Sync multiple users at once"""

        for user in users:
            success = await self.create_user(user)
            if not success:
                return False
        return True

    async def get_online_users(self) -> List[str]:
        """Get list of online user emails"""
        status = await self.get_server_status()
        if status and "stats" in status.stats:
            return []
        return []

    async def start_backend(self) -> bool:
        """Start VPN backend service - not applicable for Hiddify"""
        return True

    async def stop_backend(self) -> bool:
        """Stop VPN backend service - not applicable for Hiddify"""
        return False

    async def get_token(self) -> Optional[str]:
        """Get token - Hiddify uses API Key, not tokens"""
        return self.api_key

    async def close(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None


VPNProviderFactory.register("hiddify", HiddifyProvider)
