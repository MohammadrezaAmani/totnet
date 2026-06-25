"""
PasarGuard VPN Provider Implementation
Complete integration with PasarGuard Panel API and Node API

Two API layers are supported:
  - Panel API (/api/...) → JSON, JWT Bearer auth → user management, templates, subscriptions
  - Node API  (root)     → Protobuf, API-key auth → backend control, stats, user-sync, logs
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from apps.vpn_providers.services.base import (
    BaseVPNProvider,
    VPNConfig,
    VPNProviderFactory,
    VPNServerInfo,
    VPNStats,
    VPNUser,
)

logger = logging.getLogger(__name__)


class BackendType(Enum):
    XRAY = 0
    WIREGUARD = 1


class StatType(Enum):
    OUTBOUNDS = 0
    OUTBOUND = 1
    INBOUNDS = 2
    INBOUND = 3
    USERS_STAT = 4
    USER_STAT = 5


class UserStatus(Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    EXPIRED = "expired"
    LIMITED = "limited"
    ON_HOLD = "on_hold"


class DataLimitResetStrategy(Enum):
    NO_RESET = "no_reset"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass
class ProxySettings:
    """Protocol-specific proxy credentials for a user."""

    vmess: Optional[Dict[str, Any]] = None
    vless: Optional[Dict[str, Any]] = None
    trojan: Optional[Dict[str, Any]] = None
    shadowsocks: Optional[Dict[str, Any]] = None
    wireguard: Optional[Dict[str, Any]] = None
    hysteria: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {}
        for proto in (
            "vmess",
            "vless",
            "trojan",
            "shadowsocks",
            "wireguard",
            "hysteria",
        ):
            val = getattr(self, proto)
            if val is not None:
                result[proto] = val
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProxySettings":
        return cls(
            vmess=data.get("vmess"),
            vless=data.get("vless"),
            trojan=data.get("trojan"),
            shadowsocks=data.get("shadowsocks"),
            wireguard=data.get("wireguard"),
            hysteria=data.get("hysteria"),
        )


@dataclass
class PasarGuardUser:
    """Full PasarGuard Panel user representation."""

    id: Optional[int] = None
    username: str = ""
    proxy_settings: Optional[ProxySettings] = None
    expire: Optional[str] = None
    data_limit: Optional[int] = 0
    data_limit_reset_strategy: str = "no_reset"
    note: Optional[str] = None
    on_hold_expire_duration: Optional[int] = None
    on_hold_timeout: Optional[str] = None
    group_ids: List[int] = field(default_factory=lambda: [1])
    auto_delete_in_days: Optional[int] = None
    hwid_limit: int = 1
    next_plan: Optional[Any] = None
    status: str = "disabled"
    used_traffic: int = 0
    lifetime_used_traffic: int = 0
    created_at: Optional[str] = None
    edit_at: Optional[str] = None
    online_at: Optional[str] = None
    subscription_url: Optional[str] = None
    admin: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "username": self.username,
            "group_ids": self.group_ids,
            "data_limit": self.data_limit,
            "data_limit_reset_strategy": self.data_limit_reset_strategy,
            "hwid_limit": self.hwid_limit,
            "status": self.status,
        }
        if self.proxy_settings:
            result["proxy_settings"] = self.proxy_settings.to_dict()
        for opt_key in (
            "expire",
            "note",
            "on_hold_expire_duration",
            "on_hold_timeout",
            "auto_delete_in_days",
        ):
            val = getattr(self, opt_key)
            if val is not None:
                result[opt_key] = val
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PasarGuardUser":
        proxy_settings = None
        if "proxy_settings" in data:
            proxy_settings = ProxySettings.from_dict(data["proxy_settings"])
        return cls(
            id=data.get("id"),
            username=data.get("username", ""),
            proxy_settings=proxy_settings,
            expire=data.get("expire"),
            data_limit=data.get("data_limit", 0),
            data_limit_reset_strategy=data.get("data_limit_reset_strategy", "no_reset"),
            note=data.get("note"),
            on_hold_expire_duration=data.get("on_hold_expire_duration"),
            on_hold_timeout=data.get("on_hold_timeout"),
            group_ids=data.get("group_ids", [1]),
            auto_delete_in_days=data.get("auto_delete_in_days"),
            hwid_limit=data.get("hwid_limit", 1),
            next_plan=data.get("next_plan"),
            status=data.get("status", "disabled"),
            used_traffic=data.get("used_traffic", 0),
            lifetime_used_traffic=data.get("lifetime_used_traffic", 0),
            created_at=data.get("created_at"),
            edit_at=data.get("edit_at"),
            online_at=data.get("online_at"),
            subscription_url=data.get("subscription_url"),
            admin=data.get("admin"),
        )


@dataclass
class UserTemplate:
    """PasarGuard user template for bulk creation."""

    id: Optional[int] = None
    name: str = ""
    data_limit: int = 0
    hwid_limit: int = 1
    expire_duration: int = 2592000
    username_prefix: str = ""
    username_suffix: str = ""
    group_ids: List[int] = field(default_factory=lambda: [1])
    extra_settings: Optional[Dict[str, Any]] = None
    status: str = "on_hold"
    reset_usages: bool = False
    on_hold_timeout: Optional[str] = None
    data_limit_reset_strategy: str = "no_reset"
    is_disabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "data_limit": self.data_limit,
            "hwid_limit": self.hwid_limit,
            "expire_duration": self.expire_duration,
            "username_prefix": self.username_prefix,
            "username_suffix": self.username_suffix,
            "group_ids": self.group_ids,
            "extra_settings": self.extra_settings or {},
            "status": self.status,
            "reset_usages": self.reset_usages,
            "on_hold_timeout": self.on_hold_timeout,
            "data_limit_reset_strategy": self.data_limit_reset_strategy,
            "is_disabled": self.is_disabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserTemplate":
        return cls(
            id=data.get("id"),
            name=data.get("name", ""),
            data_limit=data.get("data_limit", 0),
            hwid_limit=data.get("hwid_limit", 1),
            expire_duration=data.get("expire_duration", 2592000),
            username_prefix=data.get("username_prefix", ""),
            username_suffix=data.get("username_suffix", ""),
            group_ids=data.get("group_ids", [1]),
            extra_settings=data.get("extra_settings"),
            status=data.get("status", "on_hold"),
            reset_usages=data.get("reset_usages", False),
            on_hold_timeout=data.get("on_hold_timeout"),
            data_limit_reset_strategy=data.get("data_limit_reset_strategy", "no_reset"),
            is_disabled=data.get("is_disabled", False),
        )


@dataclass
class SystemUserStats:
    """System-wide user statistics from the Panel API."""

    total_user: int = 0
    online_users: int = 0
    active_users: int = 0
    on_hold_users: int = 0
    disabled_users: int = 0
    expired_users: int = 0
    limited_users: int = 0
    incoming_bandwidth: int = 0
    outgoing_bandwidth: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SystemUserStats":
        return cls(
            total_user=data.get("total_user", 0),
            online_users=data.get("online_users", 0),
            active_users=data.get("active_users", 0),
            on_hold_users=data.get("on_hold_users", 0),
            disabled_users=data.get("disabled_users", 0),
            expired_users=data.get("expired_users", 0),
            limited_users=data.get("limited_users", 0),
            incoming_bandwidth=data.get("incoming_bandwidth", 0),
            outgoing_bandwidth=data.get("outgoing_bandwidth", 0),
        )


@dataclass
class BulkCreateResult:
    """Result of bulk user creation from a template."""

    subscription_urls: List[str] = field(default_factory=list)
    created: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BulkCreateResult":
        return cls(
            subscription_urls=data.get("subscription_urls", []),
            created=data.get("created", 0),
        )


@dataclass
class BaseInfoResponse:
    """Node base info (GET /info)."""

    started: bool = False
    core_version: str = "unknown"
    node_version: str = "unknown"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseInfoResponse":
        return cls(
            started=data.get("started", False),
            core_version=data.get("core_version", "unknown"),
            node_version=data.get("node_version", "unknown"),
        )


@dataclass
class StatEntry:
    """Single stat from traffic statistics."""

    name: str = ""
    type: str = ""
    link: str = ""
    value: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StatEntry":
        return cls(
            name=data.get("name", ""),
            type=data.get("type", ""),
            link=data.get("link", ""),
            value=data.get("value", 0),
        )


@dataclass
class LatencyInfo:
    """Outbound latency information."""

    name: str = ""
    alive: bool = False
    delay: int = 0
    link: str = ""
    last_seen_time: int = 0
    last_try_time: int = 0
    source: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LatencyInfo":
        return cls(
            name=data.get("name", ""),
            alive=data.get("alive", False),
            delay=data.get("delay", 0),
            link=data.get("link", ""),
            last_seen_time=data.get("last_seen_time", 0),
            last_try_time=data.get("last_try_time", 0),
            source=data.get("source", ""),
        )


@dataclass
class OnlineUser:
    """Online user entry from Node API."""

    name: str = ""
    online: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OnlineUser":
        return cls(
            name=data.get("name", ""),
            online=data.get("online", False),
        )


class PasarGuardProvider(BaseVPNProvider):
    """
    PasarGuard VPN Provider – supports Panel API *and* Node API.

    Panel API  (/api/…)  → JSON, JWT ``Bearer`` auth
    Node API   (root)    → Protobuf-style JSON, ``API_KEY`` auth
    """

    def __init__(self, base_url: str, api_key: str, **kwargs):
        super().__init__(base_url, api_key, **kwargs)
        self.client: Optional[httpx.AsyncClient] = None
        self.token: Optional[str] = None
        self.config = kwargs

        self.username: Optional[str] = kwargs.get("username")
        self.password: Optional[str] = kwargs.get("password")

        self.node_api_key: str = kwargs.get("node_api_key", api_key or "")

    def _panel_headers(self, form: bool = False) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if not form:
            headers["Content-Type"] = "application/json"
        return headers

    def _node_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "*/*",
            "Content-Type": "application/x-protobuf",
        }
        if self.node_api_key:
            headers["Authorization"] = f"Bearer {self.node_api_key}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if not self.client:
            self.client = httpx.AsyncClient(
                timeout=self.session_timeout,
                verify=False,
            )
        return self.client

    async def _panel_request(
        self,
        method: str,
        endpoint: str,
        data: Any = None,
        params: Optional[Dict[str, Any]] = None,
        form: bool = False,
        stream: bool = False,
    ) -> Any:
        """Hit a Panel API endpoint: ``{base_url}/api{endpoint}``"""
        url = f"{self.base_url}/api{endpoint}"
        headers = self._panel_headers(form=form)
        return await self._do_request(url, method, headers, data, params, form, stream)

    async def _node_request(
        self,
        method: str,
        endpoint: str,
        data: Any = None,
        params: Optional[Dict[str, Any]] = None,
        stream: bool = False,
    ) -> Any:
        """Hit a Node API endpoint: ``{base_url}{endpoint}``"""
        url = f"{self.base_url}{endpoint}"
        headers = self._node_headers()
        return await self._do_request(
            url, method, headers, data, params, form=False, stream=stream
        )

    async def _do_request(
        self,
        url: str,
        method: str,
        headers: Dict[str, str],
        data: Any,
        params: Optional[Dict[str, Any]],
        form: bool,
        stream: bool,
    ) -> Any:
        self._log_request(method, url, data)

        try:
            client = await self._get_client()
            kw: Dict[str, Any] = {"headers": headers, "params": params}

            m = method.upper()
            if m == "GET":
                resp = await client.get(url, **kw)
            elif m == "POST":
                resp = await (
                    client.post(url, data=data, **kw)
                    if form
                    else client.post(url, json=data, **kw)
                )
            elif m == "PUT":
                resp = await (
                    client.put(url, data=data, **kw)
                    if form
                    else client.put(url, json=data, **kw)
                )
            elif m == "DELETE":
                resp = await client.delete(url, **kw)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            self._log_response(url, resp.status_code, resp.text[:500])

            if resp.status_code in (200, 201):
                if stream:
                    return resp
                try:
                    return resp.json()
                except Exception:
                    return resp.text
            else:
                logger.error(f"PasarGuard API Error [{resp.status_code}]: {resp.text}")
                return None

        except Exception as e:
            self._log_error(f"{method} {url}", e)
            return None

    async def authenticate(self) -> Optional[str]:
        """Authenticate against the Panel API and store the JWT token.

        Uses ``application/x-www-form-urlencoded`` body (OAuth2 password grant).
        """
        try:
            result = await self._panel_request(
                "POST",
                "/admin/token",
                data={
                    "grant_type": "password",
                    "username": self.username,
                    "password": self.password,
                },
                form=True,
            )
            if result:
                self.token = result.get("access_token")
                return self.token
            return None
        except Exception as e:
            self._log_error("authenticate", e)
            return None

    async def get_token(self) -> Optional[str]:
        """Backward-compatible alias for :meth:`authenticate`."""
        return await self.authenticate()

    async def test_connection(self) -> bool:
        """Test connectivity to the PasarGuard Node."""
        try:
            result = await self._node_request("GET", "/info")
            return result is not None
        except Exception as e:
            logger.error(f"PasarGuard connection test failed: {e}")
            return False

    async def get_server_info(self) -> Optional[VPNServerInfo]:
        """Get basic node information (BaseVPNProvider interface)."""
        try:
            info = await self._node_request("GET", "/info")
            if info:
                return VPNServerInfo(
                    version=info.get("node_version", "unknown"),
                    started=info.get("started", False),
                    traffic_stats={},
                )
            return None
        except Exception as e:
            self._log_error("get_server_info", e)
            return None

    async def get_base_info(self) -> Optional[BaseInfoResponse]:
        """Get detailed node base info."""
        try:
            info = await self._node_request("GET", "/info")
            if info:
                return BaseInfoResponse.from_dict(info)
            return None
        except Exception as e:
            self._log_error("get_base_info", e)
            return None

    async def start_backend(
        self,
        backend_type: BackendType = BackendType.XRAY,
        config: str = "{}",
        users: Optional[List[Dict[str, Any]]] = None,
        keep_alive: int = 60,
        exclude_inbounds: Optional[List[str]] = None,
    ) -> bool:
        """Start the backend on the Node.

        ``POST /start`` — available even before the backend is running.
        """
        try:
            payload = {
                "type": backend_type.value,
                "config": config,
                "users": users or [],
                "keep_alive": keep_alive,
                "exclude_inbounds": exclude_inbounds or [],
            }

            if not config or config == "{}":
                payload["config"] = self.config.get("xray_config", "{}")
            if keep_alive == 60 and "keep_alive" in self.config:
                payload["keep_alive"] = self.config["keep_alive"]
            if not exclude_inbounds and "exclude_inbounds" in self.config:
                payload["exclude_inbounds"] = self.config["exclude_inbounds"]

            result = await self._node_request("POST", "/start", payload)
            return result is not None
        except Exception as e:
            self._log_error("start_backend", e)
            return False

    async def stop_backend(self) -> bool:
        """Stop the backend on the Node.  ``PUT /stop``"""
        try:
            result = await self._node_request("PUT", "/stop")
            return result is not None
        except Exception as e:
            self._log_error("stop_backend", e)
            return False

    async def list_users(
        self,
        limit: int = 10,
        offset: int = 0,
        sort: str = "-created_at",
        load_sub: bool = True,
        is_protocol: bool = False,
        is_id: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """List users with pagination.  ``GET /api/users``

        Returns the raw dict ``{"users": [...], "total": N}``.
        """
        try:
            params = {
                "limit": limit,
                "offset": offset,
                "sort": sort,
                "load_sub": str(load_sub).lower(),
                "is_protocol": str(is_protocol).lower(),
                "is_id": str(is_id).lower(),
            }
            return await self._panel_request("GET", "/users", params=params)
        except Exception as e:
            self._log_error("list_users", e)
            return None

    async def get_user(self, user_id: int) -> Optional[PasarGuardUser]:
        """Get a single user by ID.  ``GET /api/user/{id}``"""
        try:
            result = await self._panel_request("GET", f"/user/{user_id}")
            if result:
                return PasarGuardUser.from_dict(result)
            return None
        except Exception as e:
            self._log_error("get_user", e)
            return None

    async def find_user_by_username(self, username: str) -> Optional[PasarGuardUser]:
        """Search for a user by username across all pages."""
        try:
            offset = 0
            page_size = 100
            while True:
                result = await self.list_users(
                    limit=page_size, offset=offset, is_id=False
                )
                if not result or "users" not in result:
                    return None
                for u in result["users"]:
                    if u.get("username") == username:
                        return PasarGuardUser.from_dict(u)
                if offset + page_size >= result.get("total", 0):
                    break
                offset += page_size
            return None
        except Exception as e:
            self._log_error("find_user_by_username", e)
            return None

    async def create_user(self, user: VPNUser) -> bool:
        """Create a user via the Panel API.  ``POST /api/user``"""
        try:
            payload = self._panel_user_payload(user)
            result = await self._panel_request("POST", "/user", payload)
            return result is not None
        except Exception as e:
            self._log_error("create_user", e)
            return False

    async def create_panel_user(self, user: PasarGuardUser) -> Optional[PasarGuardUser]:
        """Create a full Panel user and return the created object."""
        try:
            result = await self._panel_request("POST", "/user", user.to_dict())
            if result:
                return PasarGuardUser.from_dict(result)
            return None
        except Exception as e:
            self._log_error("create_panel_user", e)
            return None

    async def update_user(self, user: VPNUser) -> bool:
        """Update a user (delegates to create — Panel upserts)."""
        return await self.create_user(user)

    async def update_panel_user(
        self, user_id: int, user: PasarGuardUser
    ) -> Optional[PasarGuardUser]:
        """Update a full Panel user.  ``PUT /api/user/{id}``"""
        try:
            result = await self._panel_request(
                "PUT", f"/user/{user_id}", user.to_dict()
            )
            if result:
                return PasarGuardUser.from_dict(result)
            return None
        except Exception as e:
            self._log_error("update_panel_user", e)
            return None

    async def delete_user(self, user_id_or_email) -> bool:
        """Delete a user.  ``DELETE /api/user/{id}``"""
        try:
            result = await self._panel_request("DELETE", f"/user/{user_id_or_email}")

            return result is not None or result == ""
        except Exception as e:
            self._log_error("delete_user", e)
            return False

    async def enable_user(self, user_id: int) -> Optional[PasarGuardUser]:
        """Set a user's status to *active*."""
        try:
            user = await self.get_user(user_id)
            if not user:
                return None
            user.status = "active"
            return await self.update_panel_user(user_id, user)
        except Exception as e:
            self._log_error("enable_user", e)
            return None

    async def disable_user(self, user_id: int) -> Optional[PasarGuardUser]:
        """Set a user's status to *disabled*."""
        try:
            user = await self.get_user(user_id)
            if not user:
                return None
            user.status = "disabled"
            return await self.update_panel_user(user_id, user)
        except Exception as e:
            self._log_error("disable_user", e)
            return None

    async def reset_user_traffic(self, user_id: int) -> Optional[PasarGuardUser]:
        """Reset a user's traffic counters.  ``POST /api/user/{id}/reset``"""
        try:
            result = await self._panel_request("POST", f"/user/{user_id}/reset")
            if result:
                return PasarGuardUser.from_dict(result)
            return None
        except Exception as e:
            self._log_error("reset_user_traffic", e)
            return None

    async def revoke_user_subscription(self, user_id: int) -> Optional[PasarGuardUser]:
        """Revoke the current sub and generate fresh links.  ``POST /api/user/{id}/revoke_sub``"""
        try:
            result = await self._panel_request("POST", f"/user/{user_id}/revoke_sub")
            if result:
                return PasarGuardUser.from_dict(result)
            return None
        except Exception as e:
            self._log_error("revoke_user_subscription", e)
            return None

    async def bulk_create_users_from_template(
        self,
        user_template_id: int,
        count: int = 1,
        username: Optional[str] = None,
        strategy: str = "sequence",
        note: Optional[str] = None,
    ) -> Optional[BulkCreateResult]:
        """Bulk-create users from a template.  ``POST /api/users/bulk/from_template``

        Args:
            user_template_id: ID of the template to base users on.
            count: Number of users to create.
            username: Base username (template prefix/suffix applied automatically).
            strategy: ``"sequence"`` | ``"random"`` | ``"fixed"``.
            note: Optional note to attach to every created user.
        """
        try:
            payload: Dict[str, Any] = {
                "user_template_id": user_template_id,
                "count": count,
                "strategy": strategy,
            }
            if username is not None:
                payload["username"] = username
            if note is not None:
                payload["note"] = note

            result = await self._panel_request(
                "POST", "/users/bulk/from_template", payload
            )
            if result:
                return BulkCreateResult.from_dict(result)
            return None
        except Exception as e:
            self._log_error("bulk_create_users_from_template", e)
            return None

    async def list_user_templates(self) -> Optional[List[UserTemplate]]:
        """List all user templates.  ``GET /api/user_templates``"""
        try:
            result = await self._panel_request("GET", "/user_templates")
            if result and isinstance(result, list):
                return [UserTemplate.from_dict(t) for t in result]
            return None
        except Exception as e:
            self._log_error("list_user_templates", e)
            return None

    async def get_user_template(self, template_id: int) -> Optional[UserTemplate]:
        """Get a single template.  ``GET /api/user_template/{id}``"""
        try:
            result = await self._panel_request("GET", f"/user_template/{template_id}")
            if result:
                return UserTemplate.from_dict(result)
            return None
        except Exception as e:
            self._log_error("get_user_template", e)
            return None

    async def create_user_template(
        self, template: UserTemplate
    ) -> Optional[UserTemplate]:
        """Create a template.  ``POST /api/user_template``"""
        try:
            result = await self._panel_request(
                "POST", "/user_template", template.to_dict()
            )
            if result:
                return UserTemplate.from_dict(result)
            return None
        except Exception as e:
            self._log_error("create_user_template", e)
            return None

    async def update_user_template(
        self, template_id: int, template: UserTemplate
    ) -> Optional[UserTemplate]:
        """Update a template.  ``PUT /api/user_template/{id}``"""
        try:
            result = await self._panel_request(
                "PUT", f"/user_template/{template_id}", template.to_dict()
            )
            if result:
                return UserTemplate.from_dict(result)
            return None
        except Exception as e:
            self._log_error("update_user_template", e)
            return None

    async def delete_user_template(self, template_id: int) -> bool:
        """Delete a template.  ``DELETE /api/user_template/{id}``"""
        try:
            result = await self._panel_request(
                "DELETE", f"/user_template/{template_id}"
            )
            return result is not None
        except Exception as e:
            self._log_error("delete_user_template", e)
            return False

    async def get_user_subscription_links(self, user_id: int) -> Optional[List[str]]:
        """Get raw subscription protocol links.  ``GET /api/user/{id}/subscription/links``

        The response is plain text with one URI per line (e.g. ``vless://…``).
        """
        try:
            result = await self._panel_request(
                "GET", f"/user/{user_id}/subscription/links"
            )
            if result is None:
                return None
            if isinstance(result, str):
                return [
                    line.strip() for line in result.strip().splitlines() if line.strip()
                ]
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            self._log_error("get_user_subscription_links", e)
            return None

    async def get_user_config(self, email: str) -> Optional[VPNConfig]:
        """Get a VPNConfig for a user identified by username/email.

        Finds the user via Panel API, then fetches subscription links.
        """
        try:
            user = await self.find_user_by_username(email)
            if not user or user.id is None:
                return None

            subscription_url = (
                f"{self.base_url}{user.subscription_url}"
                if user.subscription_url
                else ""
            )

            links = await self.get_user_subscription_links(user.id)
            configs: Dict[str, str] = {}
            qr_codes: List[str] = []

            if links:
                for link in links:
                    proto = link.split("://")[0] if "://" in link else "unknown"
                    configs[proto] = link

            return VPNConfig(
                subscription_url=subscription_url,
                configs=configs,
                qr_codes=qr_codes,
            )
        except Exception as e:
            self._log_error("get_user_config", e)
            return None

    async def get_system_user_stats(self) -> Optional[SystemUserStats]:
        """System-wide user statistics.  ``GET /api/system/users``"""
        try:
            result = await self._panel_request("GET", "/system/users")
            if result:
                return SystemUserStats.from_dict(result)
            return None
        except Exception as e:
            self._log_error("get_system_user_stats", e)
            return None

    async def get_user_stats(
        self, email: str, reset: bool = False
    ) -> Optional[VPNStats]:
        """Per-user traffic statistics.  ``GET /stats/``"""
        try:
            params = {
                "name": email,
                "reset": str(reset).lower(),
                "type": StatType.USER_STAT.value,
            }
            result = await self._node_request("GET", "/stats/", params=params)
            if result:
                up = result.get("uplink", 0)
                down = result.get("downlink", 0)
                return VPNStats(upload=up, download=down, total=up + down)
            return None
        except Exception as e:
            self._log_error("get_user_stats", e)
            return None

    async def get_traffic_stats(
        self,
        name: str = "",
        reset: bool = False,
        stat_type: StatType = StatType.OUTBOUNDS,
    ) -> Optional[List[StatEntry]]:
        """Generic traffic statistics.  ``GET /stats/``"""
        try:
            params = {
                "name": name,
                "reset": str(reset).lower(),
                "type": stat_type.value,
            }
            result = await self._node_request("GET", "/stats/", params=params)
            if result:
                if isinstance(result, list):
                    return [StatEntry.from_dict(s) for s in result]
                return [StatEntry.from_dict(result)]
            return None
        except Exception as e:
            self._log_error("get_traffic_stats", e)
            return None

    async def get_outbounds_latency(
        self, outbound_name: str = ""
    ) -> Optional[List[LatencyInfo]]:
        """Outbound latency.  ``GET /stats/latency``"""
        try:
            params = {"name": outbound_name}
            result = await self._node_request("GET", "/stats/latency", params=params)
            if result:
                if isinstance(result, list):
                    return [LatencyInfo.from_dict(l) for l in result]
                return [LatencyInfo.from_dict(result)]
            return None
        except Exception as e:
            self._log_error("get_outbounds_latency", e)
            return None

    async def get_online_users(self) -> List[str]:
        """Online user list.  ``GET /stats/user/online``"""
        try:
            params = {"type": StatType.USERS_STAT.value}
            result = await self._node_request(
                "GET", "/stats/user/online", params=params
            )
            if result and "users" in result:
                return [
                    u.get("name", "") for u in result["users"] if u.get("online", False)
                ]
            return []
        except Exception as e:
            self._log_error("get_online_users", e)
            return []

    async def get_online_ips(self, email: str) -> List[str]:
        """Online IPs for a user.  ``GET /stats/user/online_ip``"""
        try:
            params = {"name": email}
            result = await self._node_request(
                "GET", "/stats/user/online_ip", params=params
            )
            if result and "ips" in result:
                return result["ips"]
            return []
        except Exception as e:
            self._log_error("get_online_ips", e)
            return []

    async def get_backend_stats(self) -> Optional[Dict[str, Any]]:
        """Backend runtime stats.  ``GET /stats/backend``"""
        try:
            return await self._node_request("GET", "/stats/backend")
        except Exception as e:
            self._log_error("get_backend_stats", e)
            return None

    async def get_system_stats(self) -> Optional[Dict[str, Any]]:
        """System stats.  ``GET /stats/system``"""
        try:
            return await self._node_request("GET", "/stats/system")
        except Exception as e:
            self._log_error("get_system_stats", e)
            return None

    async def sync_user(self, user: VPNUser) -> bool:
        """Sync one user to the backend.  ``PUT /user/sync``"""
        try:
            payload = self._node_user_payload(user)
            result = await self._node_request("PUT", "/user/sync", payload)
            return result is not None
        except Exception as e:
            self._log_error("sync_user", e)
            return False

    async def sync_users(self, users: List[VPNUser]) -> bool:
        """Sync all users (replaces full set).  ``PUT /users/sync``"""
        try:
            payload = {"users": [self._node_user_payload(u) for u in users]}
            result = await self._node_request("PUT", "/users/sync", payload)
            return result is not None
        except Exception as e:
            self._log_error("sync_users", e)
            return False

    async def sync_users_chunked(
        self, users: List[VPNUser], chunk_size: int = 100
    ) -> bool:
        """Sync users in chunks.  ``PUT /users/sync/chunked``

        Each chunk is a ``UsersChunk`` with *index* and *last* flag.
        """
        try:
            total_chunks = max(1, (len(users) + chunk_size - 1) // chunk_size)
            for i in range(0, len(users), chunk_size):
                chunk = users[i : i + chunk_size]
                idx = i // chunk_size
                is_last = (idx + 1) >= total_chunks

                payload = {
                    "users": [self._node_user_payload(u) for u in chunk],
                    "index": idx,
                    "last": is_last,
                }
                result = await self._node_request("PUT", "/users/sync/chunked", payload)
                if not result:
                    logger.error(f"Chunk {idx}/{total_chunks} sync failed")
                    return False
                await asyncio.sleep(0.1)
            return True
        except Exception as e:
            self._log_error("sync_users_chunked", e)
            return False

    async def stream_logs(self, callback=None) -> None:
        """Stream backend logs.  ``GET /logs`` (Server-Sent Events)"""
        try:
            response = await self._node_request("GET", "/logs", stream=True)
            if response:
                async for line in response.aiter_lines():
                    if callback:
                        await callback(line)
                    else:
                        logger.info(f"PasarGuard Log: {line}")
        except Exception as e:
            self._log_error("stream_logs", e)

    def _node_user_payload(self, user: VPNUser) -> Dict[str, Any]:
        """Format a :class:`VPNUser` for the **Node** API."""
        proxies: Dict[str, Any] = {}
        if user.proxies:
            for proto, cfg in user.proxies.items():
                proxies[proto] = cfg
        return {
            "email": user.email,
            "proxies": proxies,
            "inbounds": user.inbounds,
        }

    def _panel_user_payload(self, user: VPNUser) -> Dict[str, Any]:
        """Format a :class:`VPNUser` for the **Panel** API."""
        payload: Dict[str, Any] = {
            "username": user.email,
            "group_ids": self.config.get("group_ids", [1]),
            "data_limit": self.config.get("data_limit", 0),
            "data_limit_reset_strategy": self.config.get(
                "data_limit_reset_strategy", "no_reset"
            ),
            "hwid_limit": self.config.get("hwid_limit", 1),
            "status": "active" if user.enable else "disabled",
        }
        if user.proxies:
            proxy_settings: Dict[str, Any] = {}
            for proto, cfg in user.proxies.items():
                proxy_settings[proto] = cfg
            payload["proxy_settings"] = proxy_settings
        if user.inbounds:
            payload["inbounds"] = user.inbounds
        return payload

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None


VPNProviderFactory.register("pasarguard", PasarGuardProvider)


if __name__ == "__main__":

    async def main() -> None:
        provider = PasarGuardProvider(
            base_url="https://r1.firefly-service.com:2096",
            api_key=None,
            username="Drahmad",
            password="DR@hmad12345",
        )

        token = await provider.authenticate()
        print(f"✓ Token acquired: {token is not None}\n")
        if not token:
            return

        sys_stats = await provider.get_system_user_stats()
        if sys_stats:
            print("📊 System Stats:")
            print(f"   Total Users : {sys_stats.total_user}")
            print(f"   Online      : {sys_stats.online_users}")
            print(f"   Active      : {sys_stats.active_users}")
            print(f"   Disabled    : {sys_stats.disabled_users}")
            print(f"   Expired     : {sys_stats.expired_users}")
            print(f"   Limited     : {sys_stats.limited_users}")
            print(f"   In BW       : {sys_stats.incoming_bandwidth / 1e9:.2f} GB")
            print(f"   Out BW      : {sys_stats.outgoing_bandwidth / 1e9:.2f} GB\n")

        users = await provider.list_users(limit=5)
        if users:
            print(f"👥 Users (showing 5 / {users.get('total', '?')}):")
            for u in users.get("users", []):
                pg = PasarGuardUser.from_dict(u)
                traffic_gb = pg.used_traffic / (1024**3)
                print(
                    f"   [{pg.id}] {pg.username:<18} "
                    f"status={pg.status:<9} "
                    f"traffic={traffic_gb:.2f} GB"
                )
            print()

        templates = await provider.list_user_templates()
        if templates:
            print("📋 User Templates:")
            for t in templates:
                print(
                    f"   [{t.id}] {t.name:<12} "
                    f"prefix={t.username_prefix} "
                    f"duration={t.expire_duration}s"
                )
            print()

        if templates:
            result = await provider.bulk_create_users_from_template(
                user_template_id=templates[0].id,
                count=1,
                username="TestAPI",
                note="Created via API integration test",
            )
            if result:
                print(f"➕ Bulk Create: {result.created} user(s) created")
                for url in result.subscription_urls:
                    print(f"   Sub URL: {url}")
            print()

        if users and users.get("users"):
            first_id = users["users"][0]["id"]
            links = await provider.get_user_subscription_links(first_id)
            if links:
                print(f"🔗 Subscription links for user #{first_id}:")
                for link in links:
                    display = link[:80] + "…" if len(link) > 80 else link
                    print(f"   {display}")
            print()

        info = await provider.get_base_info()
        if info:
            print("🖥  Node Info:")
            print(f"   Started      : {info.started}")
            print(f"   Core Version : {info.core_version}")
            print(f"   Node Version : {info.node_version}")
            print()

        await provider.close()

    asyncio.run(main())
