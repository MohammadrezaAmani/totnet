"""Connectix seller adapter built only on the observed seller API operations."""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from .base import VPNProviderFactory
from .connectix_client import (
    ConnectixClient,
    ConnectixClientRecord,
    ConnectixError,
)


class ConnectixUnsupportedOperation(ConnectixError):
    pass


@dataclass(frozen=True)
class ConnectixProvisioningRequest:
    subscription_id: str
    display_name: str
    email: str | None
    telegram_id: int | None
    plan_id: str
    plan_name: str
    group_id: str
    group_name: str
    count_of_devices: int


@dataclass(frozen=True)
class ConnectixProvisioningResult:
    remote_id: str
    username: str
    client: ConnectixClientRecord | None = None


class ConnectixProvider:
    """Connectix-specific provisioning and read operations.

    It deliberately does not implement Hiddify's backend/server lifecycle API.
    """

    provider_type = "connectix"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: float | None = None,
        transport=None,
    ):
        self.client = ConnectixClient(
            base_url=base_url or settings.CONNECTIX_API_BASE_URL,
            username=username if username is not None else settings.CONNECTIX_USERNAME,
            password=password if password is not None else settings.CONNECTIX_PASSWORD,
            timeout_seconds=timeout_seconds or settings.CONNECTIX_TIMEOUT_SECONDS,
            proxy_url=settings.CONNECTIX_PROXY_URL,
            transport=transport,
        )

    async def create_account(
        self, request: ConnectixProvisioningRequest
    ) -> ConnectixProvisioningResult:
        self._validate_request(request)
        seller_data = await self.client.get_seller_data()
        seller = seller_data.get("seller")
        short_id = seller.get("short_id") if isinstance(seller, dict) else None
        if not isinstance(short_id, str) or not short_id:
            raise ConnectixError("Connectix seller response lacks its short ID")

        username = self._new_username(short_id)
        password = self._new_password()
        payload = self.build_create_payload(
            request=request,
            username=username,
            password=password,
        )
        remote_id = await self.client.create_client(payload)
        # Persist this identity before a caller attempts a possibly delayed list read.
        return ConnectixProvisioningResult(
            remote_id=remote_id,
            username=username,
        )

    @staticmethod
    def build_create_payload(
        *,
        request: ConnectixProvisioningRequest,
        username: str,
        password: str,
    ) -> dict[str, Any]:
        """Build the full form-shaped body observed on the seller panel."""
        return {
            "id": None,
            "name": request.display_name,
            "email": request.email,
            "created_at": None,
            "remains_days": None,
            "expire_date": None,
            "count_of_plans": None,
            "plans": [],
            "count_of_devices": request.count_of_devices,
            "added_by": None,
            "password": password,
            "phone": None,
            "chat_id": None,
            # Connectix rejected the supplied Telegram ID during validation.
            # Keep the remote account unlinked; local ownership is tracked here.
            "telegram_id": None,
            "group_id": request.group_id,
            "plan_id": request.plan_id,
            "enable_plan_after_first_login": True,
            "username": username,
            "group_name": request.group_name,
            "plan_name": request.plan_name,
            "used_traffic": "",
            "is_active": True,
            "is_expired": False,
            "connection_status": "",
            "last_active_date": None,
            "subscription_link": "",
            "account_stats_link": "",
            "used_devices": {"os": "", "model": ""},
            "outline_link": "",
            "is_child_protection_enabled": False,
            "notes": f"Local subscription {request.subscription_id}",
        }

    async def get_client(self, remote_id: str) -> ConnectixClientRecord:
        return await self.client.find_client(remote_id)

    async def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        await self.client.get_seller_data()
        return {
            "healthy": True,
            "response_time_ms": round((time.perf_counter() - started) * 1000),
            "error": None,
        }

    async def close(self) -> None:
        await self.client.close()

    @staticmethod
    def _validate_request(request: ConnectixProvisioningRequest) -> None:
        if not request.plan_id or not request.group_id:
            raise ConnectixError("The local plan has no Connectix plan/group mapping")
        if not request.plan_name or not request.group_name:
            raise ConnectixError(
                "The local plan is missing Connectix plan/group labels"
            )
        if request.count_of_devices < 1:
            raise ConnectixError("Connectix device count must be positive")

    @staticmethod
    def _new_username(short_id: str) -> str:
        prefix = "".join(
            c for c in short_id.lower() if c in string.ascii_lowercase + string.digits
        )
        if not prefix:
            raise ConnectixError("Connectix seller short ID cannot form a username")
        suffix = "".join(
            secrets.choice(string.ascii_lowercase + string.digits) for _ in range(5)
        )
        return f"{prefix[:3]}{suffix}"

    @staticmethod
    def _new_password() -> str:
        return "".join(
            secrets.choice(string.ascii_lowercase + string.digits) for _ in range(5)
        )


VPNProviderFactory.register("connectix", ConnectixProvider)
