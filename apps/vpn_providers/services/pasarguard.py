"""
PasarGuard VPN Provider Implementation
Complete integration with PasarGuard Node API
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from .base import (
    BaseVPNProvider,
    VPNConfig,
    VPNProviderFactory,
    VPNServerInfo,
    VPNStats,
    VPNUser,
)

logger = logging.getLogger(__name__)


class PasarGuardProvider(BaseVPNProvider):
    """PasarGuard VPN Provider Implementation"""

    def __init__(self, base_url: str, api_key: str, **kwargs):
        super().__init__(base_url, api_key, **kwargs)
        self.client = None

    async def _get_client(self):
        """Get HTTP client with proper configuration"""
        if not self.client:
            self.client = httpx.AsyncClient(
                timeout=self.session_timeout,
            )
        return self.client

    async def _make_request(
        self, method: str, endpoint: str, data: Any = None, stream: bool = False
    ):
        """Make HTTP request to PasarGuard API"""
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()

        self._log_request(method, url, data)

        try:
            client = await self._get_client()

            if method.upper() == "GET":
                response = await client.get(url, headers=headers)
            elif method.upper() == "POST":
                response = await client.post(
                    url,
                    headers=headers,
                    content=data if isinstance(data, bytes) else json.dumps(data),
                )
            elif method.upper() == "PUT":
                response = await client.put(
                    url,
                    headers=headers,
                    content=data if isinstance(data, bytes) else json.dumps(data),
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            self._log_response(url, response.status_code, response.text[:500])

            if response.status_code == 200:
                if stream:
                    return response
                try:
                    return response.json()
                except Exception as _:
                    return response.text
            else:
                logger.error(
                    f"PasarGuard API Error: {response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            self._log_error(f"{method} {endpoint}", e)
            return None

    async def test_connection(self) -> bool:
        """Test connection to PasarGuard node"""
        try:
            result = await self._make_request("GET", "/info")
            return result is not None
        except Exception as e:
            logger.error(f"PasarGuard connection test failed: {e}")
            return False

    async def get_server_info(self) -> VPNServerInfo:
        """Get PasarGuard server information"""
        try:
            info = await self._make_request("GET", "/info")
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

    async def start_backend(self) -> bool:
        """Start PasarGuard backend"""
        try:
            backend_config = {
                "config": self.config.get("xray_config", "{}"),
                "users": [],
                "keep_alive": self.config.get("keep_alive", 60),
                "exclude_inbounds": self.config.get("exclude_inbounds", []),
            }

            result = await self._make_request("POST", "/start", backend_config)
            return result is not None
        except Exception as e:
            self._log_error("start_backend", e)
            return False

    async def stop_backend(self) -> bool:
        """Stop PasarGuard backend"""
        try:
            result = await self._make_request("PUT", "/stop")
            return result is not None
        except Exception as e:
            self._log_error("stop_backend", e)
            return False

    async def create_user(self, user: VPNUser) -> bool:
        """Create user in PasarGuard"""
        try:
            user_data = self._format_user_data(user)
            result = await self._make_request("PUT", "/user/sync", user_data)
            return result is not None
        except Exception as e:
            self._log_error("create_user", e)
            return False

    async def update_user(self, user: VPNUser) -> bool:
        """Update user in PasarGuard"""
        return await self.create_user(user)

    async def delete_user(self, email: str) -> bool:
        """Delete user from PasarGuard by setting empty config"""
        try:
            empty_user = VPNUser(email=email, proxies={}, inbounds=[], enable=False)
            return await self.create_user(empty_user)
        except Exception as e:
            self._log_error("delete_user", e)
            return False

    async def get_user_stats(
        self, email: str, reset: bool = False
    ) -> Optional[VPNStats]:
        """Get user traffic statistics from PasarGuard"""
        try:
            result = await self._make_request("GET", "/stats/", stats_request)
            if result:
                return VPNStats(
                    upload=result.get("uplink", 0),
                    download=result.get("downlink", 0),
                    total=result.get("uplink", 0) + result.get("downlink", 0),
                )
            return None
        except Exception as e:
            self._log_error("get_user_stats", e)
            return None

    async def get_user_config(self, email: str) -> Optional[VPNConfig]:
        """Get user configuration from PasarGuard"""
        try:
            server_info = await self.get_server_info()
            if not server_info:
                return None

            subscription_url = f"{self.base_url}/subscription/{email}"

            configs = {}
            qr_codes = []

            configs["vless"] = (
                f"vless://{email}@{self.base_url}:443?type=ws&security=tls#PasarGuard-VLESS"
            )
            configs["vmess"] = (
                f"vmess://{email}@{self.base_url}:443?type=ws&security=tls#PasarGuard-VMess"
            )

            return VPNConfig(
                subscription_url=subscription_url, configs=configs, qr_codes=qr_codes
            )
        except Exception as e:
            self._log_error("get_user_config", e)
            return None

    async def sync_users(self, users: List[VPNUser]) -> bool:
        """Sync multiple users to PasarGuard"""
        try:
            users_data = {"users": [self._format_user_data(user) for user in users]}

            result = await self._make_request("PUT", "/users/sync", users_data)
            return result is not None
        except Exception as e:
            self._log_error("sync_users", e)
            return False

    async def sync_users_chunked(
        self, users: List[VPNUser], chunk_size: int = 100
    ) -> bool:
        """Sync users in chunks for better performance"""
        try:
            _ = (len(users) + chunk_size - 1) // chunk_size

            for i in range(0, len(users), chunk_size):
                chunk = users[i : i + chunk_size]
                chunk_data = {
                    "users": [self._format_user_data(user) for user in chunk],
                    "index": i // chunk_size,
                    "last": (i + chunk_size) >= len(users),
                }

                result = await self._make_request(
                    "PUT", "/users/sync/chunked", chunk_data
                )
                if not result:
                    return False

                await asyncio.sleep(0.1)

            return True
        except Exception as e:
            self._log_error("sync_users_chunked", e)
            return False

    async def get_online_users(self) -> List[str]:
        """Get list of online users from PasarGuard"""
        try:
            result = await self._make_request("GET", "/stats/user/online")
            if result and "users" in result:
                return [
                    user.get("name", "")
                    for user in result["users"]
                    if user.get("online", False)
                ]
            return []
        except Exception as e:
            self._log_error("get_online_users", e)
            return []

    async def get_online_ips(self, email: str) -> List[str]:
        """Get online IP addresses for a specific user"""
        try:
            result = await self._make_request(
                "GET", "/stats/user/online_ip", stats_request
            )
            if result and "ips" in result:
                return result["ips"]
            return []
        except Exception as e:
            self._log_error("get_online_ips", e)
            return []

    async def get_backend_stats(self) -> Optional[Dict[str, Any]]:
        """Get backend runtime statistics"""
        try:
            result = await self._make_request("GET", "/stats/backend")
            return result
        except Exception as e:
            self._log_error("get_backend_stats", e)
            return None

    async def get_system_stats(self) -> Optional[Dict[str, Any]]:
        """Get system statistics"""
        try:
            result = await self._make_request("GET", "/stats/system")
            return result
        except Exception as e:
            self._log_error("get_system_stats", e)
            return None

    async def get_outbounds_latency(
        self, outbound_name: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Get outbound latency statistics"""
        try:
            latency_request = {"name": outbound_name}

            result = await self._make_request("GET", "/stats/latency", latency_request)
            return result
        except Exception as e:
            self._log_error("get_outbounds_latency", e)
            return None

    async def stream_logs(self, callback=None):
        """Stream backend logs from PasarGuard"""
        try:
            response = await self._make_request("GET", "/logs", stream=True)
            if response:
                async for line in response.aiter_lines():
                    if callback:
                        await callback(line)
                    else:
                        logger.info(f"PasarGuard Log: {line}")
        except Exception as e:
            self._log_error("stream_logs", e)

    def _format_user_data(self, user: VPNUser) -> Dict[str, Any]:
        """Format user data for PasarGuard API"""
        return {"email": user.email, "proxies": user.proxies, "inbounds": user.inbounds}

    async def close(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()


VPNProviderFactory.register("pasarguard", PasarGuardProvider)
