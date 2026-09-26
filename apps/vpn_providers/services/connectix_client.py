"""Async HTTP client for the observed Connectix seller-panel API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


class ConnectixError(Exception):
    """Base error for Connectix API failures."""


class ConnectixAuthenticationError(ConnectixError):
    pass


class ConnectixAuthorizationError(ConnectixError):
    pass


class ConnectixValidationError(ConnectixError):
    pass


class ConnectixNotFoundError(ConnectixError):
    pass


class ConnectixUpstreamError(ConnectixError):
    pass


class ConnectixTimeoutError(ConnectixUpstreamError):
    pass


class ConnectixMalformedResponse(ConnectixError):
    pass


@dataclass(frozen=True)
class ConnectixClientRecord:
    remote_id: str
    username: str
    password: str = field(repr=False)
    name: str = ""
    is_active: bool = False
    is_expired: bool = False
    expire_date: str = ""
    remains_days: str = ""
    used_traffic: str = ""
    plan_name: str = ""
    group_name: str = ""
    subscription_link: str = field(default="", repr=False)
    outline_link: str = field(default="", repr=False)

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> ConnectixClientRecord:
        remote_id = data.get("id")
        username = data.get("username")
        if not isinstance(remote_id, (str, int)) or not str(remote_id):
            raise ConnectixMalformedResponse("Client record has no id")
        if not isinstance(username, str) or not username:
            raise ConnectixMalformedResponse("Client record has no username")
        password = data.get("password", "")
        if password is None:
            password = ""
        if not isinstance(password, str):
            raise ConnectixMalformedResponse("Client record password is malformed")
        return cls(
            remote_id=str(remote_id),
            username=username,
            password=password,
            name=_optional_str(data, "name"),
            is_active=_optional_bool(data, "is_active"),
            is_expired=_optional_bool(data, "is_expired"),
            expire_date=_optional_str(data, "expire_date"),
            remains_days=_optional_str(data, "remains_days"),
            used_traffic=_optional_str(data, "used_traffic"),
            plan_name=_optional_str(data, "plan_name"),
            group_name=_optional_str(data, "group_name"),
            subscription_link=_optional_str(data, "subscription_link"),
            outline_link=_optional_str(data, "outline_link"),
        )


@dataclass(frozen=True)
class ConnectixClientPage:
    clients: tuple[ConnectixClientRecord, ...]
    current_page: int
    last_page: int
    total: int


@dataclass(frozen=True)
class ConnectixSellerPlan:
    plan_id: str
    title: str
    price: str = ""
    period: str = ""
    period_unit: str = ""
    traffic_amount: str = ""
    count_of_devices: int | None = None
    group_name: str = ""
    displayed_in_panel: bool | None = None
    displayed_in_robot: bool | None = None


def _optional_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ConnectixMalformedResponse(f"Client field {key} is malformed")


def _optional_bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if value in (True, 1, "1", "true"):
        return True
    if value in (False, 0, "0", "false", None):
        return False
    raise ConnectixMalformedResponse(f"Client field {key} is malformed")


class ConnectixClient:
    """Transport, bearer-token lifecycle, response parsing, and safe errors."""

    LOGIN_PATH = "/v1/seller/auth/login"
    CLIENTS_PATH = "/v1/seller/clients"
    METADATA_PATH = "/v1/seller/clients/meta-data"
    SELLER_PATH = "/v1/seller/seller-data"
    DASHBOARD_PATH = "/v1/seller/dashboard"
    SELLER_PLANS_PATH = "/v1/seller/seller-plans"

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._password = password
        self._token: str | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=min(timeout_seconds, 10),
                read=timeout_seconds,
                write=timeout_seconds,
                pool=min(timeout_seconds, 5),
            ),
            transport=transport,
            trust_env=False,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en",
                "Origin": "https://seller.connectix.vip",
                "Referer": "https://seller.connectix.vip/",
            },
        )

    async def _login(self) -> None:
        try:
            response = await self._client.post(
                f"{self.base_url}{self.LOGIN_PATH}",
                json={
                    "email": self.username,
                    "password": self._password,
                    "rememberMe": False,
                    "device_browser": "Chrome",
                    "device_os": "Linux",
                },
            )
        except httpx.TimeoutException as exc:
            raise ConnectixTimeoutError("Connectix login timed out") from exc
        except httpx.HTTPError as exc:
            raise ConnectixUpstreamError("Connectix login request failed") from exc

        if response.status_code in (401, 403):
            raise ConnectixAuthenticationError("Connectix login was rejected")
        if response.status_code >= 500:
            raise ConnectixUpstreamError(
                f"Connectix login failed with status {response.status_code}"
            )
        if response.status_code >= 400:
            raise ConnectixValidationError(
                f"Connectix login failed with status {response.status_code}"
            )
        body = _json_object(response)
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise ConnectixMalformedResponse("Connectix login response has no token")
        self._token = token

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        if not self._token:
            await self._login()

        headers = {"Authorization": f"Bearer {self._token}"}
        logger.debug("Connectix request method=%s path=%s", method, urlsplit(path).path)
        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise ConnectixTimeoutError("Connectix request timed out") from exc
        except httpx.HTTPError as exc:
            raise ConnectixUpstreamError("Connectix request failed") from exc

        if response.status_code == 401 and retry_auth and method.upper() == "GET":
            self._token = None
            await self._login()
            return await self._request_json(
                method, path, params=params, json_body=json_body, retry_auth=False
            )
        if response.status_code == 401:
            raise ConnectixAuthenticationError("Connectix authorization expired")
        if response.status_code == 403:
            raise ConnectixAuthorizationError("Connectix rejected this operation")
        if response.status_code == 404:
            raise ConnectixNotFoundError("Connectix resource was not found")
        if response.status_code == 422:
            raise ConnectixValidationError("Connectix rejected the request fields")
        if response.status_code == 429:
            raise ConnectixUpstreamError("Connectix rate limit was reached")
        if response.status_code >= 500:
            raise ConnectixUpstreamError(
                f"Connectix returned status {response.status_code}"
            )
        if response.status_code >= 400:
            raise ConnectixValidationError(
                f"Connectix returned status {response.status_code}"
            )
        return _json_object(response)

    async def get_seller_data(self) -> dict[str, Any]:
        return await self._request_json("GET", self.SELLER_PATH)

    async def get_client_metadata(self) -> dict[str, Any]:
        body = await self._request_json("GET", self.METADATA_PATH)
        if not isinstance(body.get("seller_plans"), list) or not isinstance(
            body.get("groups"), list
        ):
            raise ConnectixMalformedResponse("Connectix client metadata is malformed")
        return body

    async def get_dashboard(
        self, *, free_premium_chart_type: str, report_type: str
    ) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            self.DASHBOARD_PATH,
            params={
                "freeAndPremiumChartType": free_premium_chart_type,
                "type": report_type,
            },
        )

    async def get_seller_plans(
        self, *, for_client_page: bool = True, is_archived: bool = False
    ) -> tuple[ConnectixSellerPlan, ...]:
        body = await self._request_json(
            "GET",
            self.SELLER_PLANS_PATH,
            params={
                "forClientPage": str(for_client_page).lower(),
                "is_archived": str(is_archived).lower(),
            },
        )
        groups = body.get("seller_plan_group")
        if not isinstance(groups, list):
            raise ConnectixMalformedResponse("Connectix seller plan list is malformed")
        plans: list[ConnectixSellerPlan] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(
                group.get("seller_plans"), list
            ):
                raise ConnectixMalformedResponse(
                    "Connectix seller plan group is malformed"
                )
            for item in group["seller_plans"]:
                if not isinstance(item, dict):
                    raise ConnectixMalformedResponse(
                        "Connectix seller plan is malformed"
                    )
                plan_id = item.get("id")
                title = item.get("title")
                if not isinstance(plan_id, (str, int)) or not isinstance(title, str):
                    raise ConnectixMalformedResponse(
                        "Connectix seller plan lacks an id/title"
                    )
                translations = item.get("group_name_translations")
                group_name = ""
                if isinstance(translations, dict):
                    group_name = str(
                        translations.get("en") or translations.get("fa") or ""
                    )
                count = item.get("count_of_devices")
                plans.append(
                    ConnectixSellerPlan(
                        plan_id=str(plan_id),
                        title=title,
                        price=_optional_str(item, "price"),
                        period=_optional_str(item, "period"),
                        period_unit=_optional_str(item, "period_unit"),
                        traffic_amount=_optional_str(item, "traffic_amount"),
                        count_of_devices=count if isinstance(count, int) else None,
                        group_name=group_name,
                        displayed_in_panel=_optional_nullable_bool(
                            item, "is_displayed_in_panel"
                        ),
                        displayed_in_robot=_optional_nullable_bool(
                            item, "is_displayed_in_robot"
                        ),
                    )
                )
        return tuple(plans)

    async def get_clients(
        self,
        *,
        page: int = 1,
        record_per_page: int = 30,
        is_export: bool = False,
        quick_filters_value: str = "",
    ) -> ConnectixClientPage:
        body = await self._request_json(
            "GET",
            self.CLIENTS_PATH,
            params={
                "page": page,
                "is_export": str(is_export).lower(),
                "recordPerPage": record_per_page,
                "quickFiltersValue": quick_filters_value,
            },
        )
        outer = body.get("clients")
        if not isinstance(outer, dict) or not isinstance(outer.get("data"), list):
            raise ConnectixMalformedResponse("Connectix client list is malformed")
        records = tuple(
            ConnectixClientRecord.from_payload(item)
            for item in outer["data"]
            if isinstance(item, dict)
        )
        if len(records) != len(outer["data"]):
            raise ConnectixMalformedResponse(
                "Connectix client list contains invalid records"
            )
        current_page = _required_int(outer, "current_page")
        last_page = _required_int(outer, "last_page")
        total = _required_int(outer, "total")
        return ConnectixClientPage(records, current_page, last_page, total)

    async def find_client(self, remote_id: str) -> ConnectixClientRecord:
        page_number = 1
        while page_number <= 200:
            page = await self.get_clients(page=page_number)
            for record in page.clients:
                if record.remote_id == remote_id:
                    return record
            if page_number >= page.last_page:
                break
            page_number += 1
        raise ConnectixNotFoundError("Created client was not found in the client list")

    async def create_client(self, payload: dict[str, Any]) -> str:
        """Create once. This operation is intentionally never retried."""
        body = await self._request_json(
            "POST", f"{self.CLIENTS_PATH}/store", json_body=payload, retry_auth=False
        )
        client_id = body.get("client_id")
        if not isinstance(client_id, (str, int)) or not str(client_id):
            raise ConnectixMalformedResponse(
                "Connectix create response has no client id"
            )
        return str(client_id)

    async def health_check(self) -> dict[str, Any]:
        await self.get_seller_data()
        return {"healthy": True, "response_time_ms": 0, "error": None}

    async def close(self) -> None:
        await self._client.aclose()


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise ConnectixMalformedResponse("Connectix returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ConnectixMalformedResponse("Connectix response must be a JSON object")
    return value


def _required_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConnectixMalformedResponse(
            f"Connectix pagination field {key} is malformed"
        ) from exc


def _optional_nullable_bool(data: dict[str, Any], key: str) -> bool | None:
    value = data.get(key)
    if value is None:
        return None
    return _optional_bool(data, key)
