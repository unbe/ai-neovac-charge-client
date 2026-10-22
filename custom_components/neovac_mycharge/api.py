"""Asynchronous API & SignalR WebSocket client for NeoVac myCharge V."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import secrets
import time
from typing import Any
import urllib.parse

import aiohttp

from .const import (
    API_BASE,
    AUTH_BASE,
    CLIENT_ID,
    DEFAULT_REPORT_DAYS,
    DEFAULT_SCOPES,
    EVENT_CABLE_PLUGGED_IN,
    EVENT_CABLE_UNPLUGGED,
    EVENT_CHARGING_PAUSED_EV,
    EVENT_CHARGING_PAUSED_EVSE,
    EVENT_CHARGING_RESUMED,
    EVENT_CHARGING_STARTED,
    EVENT_CHARGING_STOPPED,
    EVENT_ERROR,
    HUB_CONNECTOR,
    HUB_EVENTS,
    PAUSED_STATUSES,
    PLUGGED_IN_STATUSES,
    RECORD_SEPARATOR,
    REDIRECT_URI,
    STATUS_AVAILABLE,
    STATUS_CHARGING,
    STATUS_CONNECTED,
    STATUS_FAULTED,
    STATUS_FINISHING,
    STATUS_OCCUPIED,
    STATUS_PREPARING,
    STATUS_SUSPENDED_EV,
    STATUS_SUSPENDED_EVSE,
    canonical_status,
    is_plugged_in_status,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30.0)


class NeoVacError(Exception):
    """Base exception for NeoVac errors."""


class NeoVacAuthError(NeoVacError):
    """Raised when authentication or token refresh fails (401/invalid_grant)."""


class NeoVacConnectionError(NeoVacError):
    """Raised when a WebSocket or network connection is dropped or fails."""


class NeoVacApiError(NeoVacError):
    """Raised when a REST API call returns a non-2xx status."""

    def __init__(
        self,
        status_code: int,
        message: str,
        endpoint: str,
        method: str,
        raw_body: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.endpoint = endpoint
        self.method = method
        self.raw_body = raw_body
        self.data = data or {}

        explanation = message
        if message == "Rejected":
            explanation = (
                "Command 'Rejected' by the charging station. "
                "Check that the vehicle is plugged in, not already in an active "
                "transaction, or not suspended."
            )
        self.explanation = explanation
        super().__init__(f"[{method} {endpoint}] HTTP {status_code}: {explanation}")


def evaluate_state_transitions(
    previous_status: str | None,
    new_status: str | None,
    error_code: str | None = None,
    error_charging: bool = False,
) -> list[str]:
    """Determines which high-level lifecycle events occurred between two states.

    Returns a list of event type strings (e.g. ['cable_plugged_in', 'charging_started']).
    """
    events: list[str] = []
    if not new_status or previous_status == new_status:
        if error_charging or (error_code and error_code not in ("NoError", "None", "0", "")):
            events.append(EVENT_ERROR)
        return events

    # If previous_status is None (initial load), do not emit transition events
    if previous_status is None:
        return events

    canonical_prev = canonical_status(previous_status)
    canonical_new = canonical_status(new_status)

    if canonical_prev == canonical_new:
        if error_charging or (error_code and error_code not in ("NoError", "None", "0", "")):
            events.append(EVENT_ERROR)
        return events

    was_plugged = is_plugged_in_status(canonical_prev)
    is_plugged = is_plugged_in_status(canonical_new)

    # Cable plug / unplug transitions
    if not was_plugged and is_plugged:
        events.append(EVENT_CABLE_PLUGGED_IN)
    elif was_plugged and not is_plugged and canonical_new == STATUS_AVAILABLE:
        events.append(EVENT_CABLE_UNPLUGGED)

    # Charging lifecycle transitions
    if canonical_new == STATUS_CHARGING:
        if canonical_prev in PAUSED_STATUSES or canonical_prev.lower() in PAUSED_STATUSES:
            events.append(EVENT_CHARGING_RESUMED)
        elif canonical_prev != STATUS_CHARGING:
            events.append(EVENT_CHARGING_STARTED)
    elif canonical_new == STATUS_SUSPENDED_EV and canonical_prev != STATUS_SUSPENDED_EV:
        events.append(EVENT_CHARGING_PAUSED_EV)
    elif canonical_new == STATUS_SUSPENDED_EVSE and canonical_prev != STATUS_SUSPENDED_EVSE:
        events.append(EVENT_CHARGING_PAUSED_EVSE)
    elif canonical_prev in (STATUS_CHARGING, STATUS_SUSPENDED_EV, STATUS_SUSPENDED_EVSE):
        if canonical_new in (
            STATUS_FINISHING,
            STATUS_AVAILABLE,
            STATUS_CONNECTED,
            STATUS_OCCUPIED,
            STATUS_PREPARING,
        ):
            events.append(EVENT_CHARGING_STOPPED)

    if (
        canonical_new == STATUS_FAULTED
        or error_charging
        or (error_code and error_code not in ("NoError", "None", "0", ""))
    ):
        events.append(EVENT_ERROR)

    return events


class NeoVacAsyncClient:
    """Async client for NeoVac myCharge V REST API and SignalR WebSocket hubs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str = "",
        refresh_token: str = "",
        auth_base: str = AUTH_BASE,
        api_base: str = API_BASE,
        token_update_callback: Callable[[str, str], Awaitable[None] | None] | None = None,
    ) -> None:
        self._session = session
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.auth_base = auth_base.rstrip("/")
        self.api_base = api_base.rstrip("/")
        self._token_update_callback = token_update_callback
        self._refresh_lock = asyncio.Lock()

    # -------------------------------------------------------------------------
    # OAuth2 PKCE & Token Management
    # -------------------------------------------------------------------------

    @staticmethod
    def generate_pkce_pair() -> tuple[str, str]:
        """Generates (code_verifier, code_challenge) using SHA-256 for PKCE."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return verifier, challenge

    def build_authorize_url(
        self, code_challenge: str, state: str | None = None, nonce: str | None = None
    ) -> str:
        """Builds the OAuth2 PKCE authorization URL."""
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "scope": DEFAULT_SCOPES,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state or secrets.token_urlsafe(16),
            "nonce": nonce or secrets.token_urlsafe(16),
        }
        return f"{self.auth_base}/connect/authorize?{urllib.parse.urlencode(params)}"

    @staticmethod
    def extract_code_from_redirect(redirect_url_or_code: str) -> str:
        """Extracts the OAuth2 authorization code from a redirect URL or raw code."""
        code = redirect_url_or_code.strip()
        if "code=" in code:
            parsed = urllib.parse.urlparse(code)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                return qs["code"][0]
            qs_frag = urllib.parse.parse_qs(parsed.fragment)
            if "code" in qs_frag:
                return qs_frag["code"][0]
        return code

    async def async_exchange_code(
        self, redirect_url_or_code: str, code_verifier: str
    ) -> dict[str, Any]:
        """Exchanges an authorization code for access and refresh tokens."""
        code = self.extract_code_from_redirect(redirect_url_or_code)
        url = f"{self.auth_base}/connect/token"
        payload = {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
            "code": code,
        }
        try:
            async with self._session.post(
                url,
                data=payload,
                headers={"Accept": "application/json"},
                timeout=DEFAULT_REQUEST_TIMEOUT,
            ) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    raise NeoVacAuthError(f"Code exchange failed (HTTP {resp.status}): {raw}")
                data = json.loads(raw)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise NeoVacConnectionError(f"Network error during code exchange: {err}") from err

        if "access_token" not in data:
            raise NeoVacAuthError(f"Missing access_token in response: {data}")

        self.access_token = data["access_token"]
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]

        await self._notify_token_updated()
        return data

    async def async_refresh_access_token(self) -> str:
        """Refreshes the OAuth2 access token using the stored refresh token."""
        async with self._refresh_lock:
            if not self.refresh_token:
                raise NeoVacAuthError("No refresh token available to refresh access token.")

            url = f"{self.auth_base}/connect/token"
            payload = {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
            try:
                async with self._session.post(
                    url,
                    data=payload,
                    headers={"Accept": "application/json"},
                    timeout=DEFAULT_REQUEST_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status in (400, 401):
                        raise NeoVacAuthError(f"Refresh token invalid or expired: {raw}")
                    if resp.status >= 400:
                        raise NeoVacApiError(
                            status_code=resp.status,
                            message=f"Token refresh failed: {raw}",
                            endpoint="/connect/token",
                            method="POST",
                            raw_body=raw,
                        )
                    data = json.loads(raw)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                raise NeoVacConnectionError(
                    f"Network error refreshing access token: {err}"
                ) from err

            if "access_token" not in data:
                raise NeoVacAuthError(f"Token refresh did not return access_token: {data}")

            self.access_token = data["access_token"]
            if "refresh_token" in data:
                self.refresh_token = data["refresh_token"]

            await self._notify_token_updated()
            return self.access_token

    async def _notify_token_updated(self) -> None:
        if self._token_update_callback:
            res = self._token_update_callback(self.access_token, self.refresh_token)
            if asyncio.iscoroutine(res):
                await res

    # -------------------------------------------------------------------------
    # Core HTTP Request Handler
    # -------------------------------------------------------------------------

    async def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        retry_on_401: bool = True,
    ) -> Any:
        """Executes an authenticated HTTP request with automatic 401 refresh."""
        if not self.access_token and self.refresh_token:
            await self.async_refresh_access_token()
        if not self.access_token:
            raise NeoVacAuthError("No access token available.")

        url = f"{self.api_base}{endpoint}"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        try:
            async with self._session.request(
                method,
                url,
                json=data,
                headers=headers,
                timeout=DEFAULT_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401 and retry_on_401 and self.refresh_token:
                    await self.async_refresh_access_token()
                    return await self._request(
                        endpoint,
                        method=method,
                        data=data,
                        retry_on_401=False,
                    )

                raw_text = await resp.text()
                if resp.status == 401:
                    raise NeoVacAuthError(f"Unauthorized request to {endpoint}")

                if resp.status >= 400:
                    parsed_data: dict[str, Any] = {}
                    error_msg = f"HTTP {resp.status}"
                    if raw_text.strip():
                        try:
                            parsed = json.loads(raw_text)
                            if isinstance(parsed, dict):
                                parsed_data = parsed
                                error_msg = (
                                    parsed.get("message")
                                    or parsed.get("error_description")
                                    or parsed.get("error")
                                    or str(parsed)
                                )
                        except Exception as err:
                            _LOGGER.warning(
                                "Failed to parse error response JSON for %s %s: %s (body preview: %s)",
                                method,
                                endpoint,
                                err,
                                raw_text[:200],
                            )
                            error_msg = raw_text.strip() or f"HTTP {resp.status}"

                    _LOGGER.error(
                        "NeoVac API request failed [%s %s]: %s (status %s)",
                        method,
                        endpoint,
                        error_msg,
                        resp.status,
                    )

                    raise NeoVacApiError(
                        status_code=resp.status,
                        message=error_msg,
                        endpoint=endpoint,
                        method=method,
                        raw_body=raw_text,
                        data=parsed_data,
                    )

                if not raw_text:
                    return None
                try:
                    return json.loads(raw_text)
                except json.JSONDecodeError as err:
                    _LOGGER.warning(
                        "Non-JSON response from [%s %s]: %s (body preview: %s)",
                        method,
                        endpoint,
                        err,
                        raw_text[:200],
                    )
                    return raw_text
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise NeoVacConnectionError(
                f"Network error connecting to {endpoint}: {err}"
            ) from err

    # -------------------------------------------------------------------------
    # User, Sites & Charge Points
    # -------------------------------------------------------------------------

    async def async_get_user_info(self) -> dict[str, Any]:
        """Fetches user profile, sites, and assigned charge points."""
        res = await self._request("/mobile/api/user/info", method="GET")
        return res if isinstance(res, dict) else {}

    # -------------------------------------------------------------------------
    # Charging Control & Hardware Settings
    # -------------------------------------------------------------------------

    async def async_start_charging(
        self, charge_point_id: int, rfid_tag: str | None = None
    ) -> dict[str, Any]:
        """Sends a remote start transaction command."""
        payload: dict[str, Any] = {"chargePointId": int(charge_point_id)}
        if rfid_tag:
            payload["rfidTag"] = rfid_tag
        res = await self._request(
            "/mobile/api/charge-point/commands/remote-start-transaction",
            method="POST",
            data=payload,
        )
        return res if isinstance(res, dict) else {}

    async def async_stop_charging(self, charge_point_id: int) -> dict[str, Any]:
        """Sends a remote stop transaction command."""
        res = await self._request(
            "/mobile/api/charge-point/commands/remote-stop-transaction",
            method="POST",
            data={"chargePointId": int(charge_point_id)},
        )
        return res if isinstance(res, dict) else {}

    async def async_set_permanent_cable_lock(
        self, charge_point_id: int, enabled: bool
    ) -> dict[str, Any]:
        """Enables or disables permanent cable lock."""
        res = await self._request(
            f"/mobile/api/charge-point/{int(charge_point_id)}/lock-cable",
            method="POST",
            data={"enabled": bool(enabled)},
        )
        return res if isinstance(res, dict) else {}

    async def async_set_night_charging(
        self, charge_point_id: int, enabled: bool
    ) -> dict[str, Any]:
        """Enables or disables night charging schedule (22:00 - 06:00)."""
        res = await self._request(
            f"/mobile/api/charge-point/{int(charge_point_id)}/night-charging",
            method="POST",
            data={"enabled": bool(enabled)},
        )
        return res if isinstance(res, dict) else {}

    async def async_set_plug_and_charge(
        self, charge_point_id: int, enabled: bool
    ) -> dict[str, Any]:
        """Enables or disables plug-and-charge."""
        res = await self._request(
            f"/mobile/api/charge-point/{int(charge_point_id)}/plug-and-charge",
            method="POST",
            data={"enabled": bool(enabled)},
        )
        return res if isinstance(res, dict) else {}

    async def async_rename_station(
        self, charge_point_id: int, new_name: str
    ) -> dict[str, Any]:
        """Updates the custom display name of the station."""
        res = await self._request(
            f"/mobile/api/charge-point/{int(charge_point_id)}/update-name",
            method="POST",
            data={"newName": str(new_name)},
        )
        return res if isinstance(res, dict) else {}

    # -------------------------------------------------------------------------
    # RFID Badge Management
    # -------------------------------------------------------------------------

    async def async_list_rfids(self) -> list[dict[str, Any]]:
        """Lists all registered RFID badges for the account."""
        res = await self._request("/mobile/api/user/rfid", method="GET")
        if isinstance(res, dict):
            return res.get("rfids", [])
        if isinstance(res, list):
            return res
        return []

    async def async_add_rfid(self, rfid_tag: str, rfid_name: str) -> dict[str, Any]:
        """Registers a new physical RFID badge."""
        clean_tag = rfid_tag.replace(":", "").replace("-", "").replace(" ", "").upper()
        res = await self._request(
            "/mobile/api/user/rfid",
            method="POST",
            data={"rfidTag": clean_tag, "rfidName": rfid_name},
        )
        return res if isinstance(res, dict) else {}

    async def async_update_rfid(self, rfid_id: int, rfid_name: str) -> dict[str, Any]:
        """Renames an existing RFID badge."""
        res = await self._request(
            f"/mobile/api/user/rfid/{int(rfid_id)}",
            method="PATCH",
            data={"rfidName": rfid_name},
        )
        return res if isinstance(res, dict) else {}

    async def async_delete_rfid(self, rfid_id: int) -> dict[str, Any]:
        """Deletes an RFID badge by ID."""
        res = await self._request(
            f"/mobile/api/user/rfid/{int(rfid_id)}", method="DELETE"
        )
        return res if isinstance(res, dict) else {}

    # -------------------------------------------------------------------------
    # Reports, Analytics, Pricing, Invoices & Invitations
    # -------------------------------------------------------------------------

    async def async_get_charge_report(
        self,
        charge_point_id: int,
        days: int = DEFAULT_REPORT_DAYS,
        earliest_date: str | None = None,
        latest_date: str | None = None,
    ) -> dict[str, Any]:
        """Fetches charging session history and aggregated kWh/CHF totals."""
        now = datetime.now(timezone.utc)
        start = earliest_date or (now - timedelta(days=days)).strftime("%Y-%m-%d")
        end = latest_date or now.strftime("%Y-%m-%d")
        query = urllib.parse.urlencode({"earliestDate": start, "latestDate": end})
        res = await self._request(
            f"/mobile/api/charge-point/{int(charge_point_id)}/report?{query}",
            method="GET",
        )
        return res if isinstance(res, dict) else {}

    async def async_get_site_report(
        self,
        site_id: str,
        days: int = DEFAULT_REPORT_DAYS,
        earliest_date: str | None = None,
        latest_date: str | None = None,
    ) -> dict[str, Any]:
        """Fetches site-wide charging report."""
        now = datetime.now(timezone.utc)
        start = earliest_date or (now - timedelta(days=days)).strftime("%Y-%m-%d")
        end = latest_date or now.strftime("%Y-%m-%d")
        query = urllib.parse.urlencode({"earliestDate": start, "latestDate": end})
        res = await self._request(
            f"/mobile/api/sites/{site_id}/report?{query}", method="GET"
        )
        return res if isinstance(res, dict) else {}

    async def async_request_email_report(
        self,
        charge_point_id: int,
        days: int = DEFAULT_REPORT_DAYS,
        earliest_date: str | None = None,
        latest_date: str | None = None,
    ) -> dict[str, Any]:
        """Requests a PDF report to be sent via email."""
        now = datetime.now(timezone.utc)
        start = earliest_date or (now - timedelta(days=days)).strftime("%Y-%m-%d")
        end = latest_date or now.strftime("%Y-%m-%d")
        payload = {
            "chargePointId": int(charge_point_id),
            "earliestDate": start,
            "latestDate": end,
        }
        res = await self._request(
            "/mobile/api/charge-reports/create-charge-point-report",
            method="POST",
            data=payload,
        )
        return res if isinstance(res, dict) else {}

    async def async_get_analytics(
        self,
        charge_point_id: int,
        resolution: str = "Month",
        days: int = DEFAULT_REPORT_DAYS,
        earliest_date: str | None = None,
        latest_date: str | None = None,
    ) -> dict[str, Any]:
        """Fetches energy consumption analytics curve (Day, Month, or Year)."""
        now = datetime.now(timezone.utc)
        start = earliest_date or (now - timedelta(days=days)).strftime("%Y-%m-%d")
        end = latest_date or now.strftime("%Y-%m-%d")
        query = urllib.parse.urlencode(
            {"earliestDate": start, "latestDate": end, "resolution": resolution}
        )
        res = await self._request(
            f"/mobile/api/charge-point/{int(charge_point_id)}/analytics?{query}",
            method="GET",
        )
        return res if isinstance(res, dict) else {}

    async def async_get_pricing(
        self, charge_point_id: int, connector_number: int = 1
    ) -> dict[str, Any]:
        """Fetches active electricity tariffs and connector pricing."""
        res = await self._request(
            f"/mobile/api/charge-point/{int(charge_point_id)}/pricing-details",
            method="GET",
        )
        if isinstance(res, dict) and "pricingDetails" in res and isinstance(res["pricingDetails"], dict):
            return {**res, **res["pricingDetails"]}
        return res if isinstance(res, dict) else {}

    async def async_list_invoices(self) -> list[dict[str, Any]]:
        """Lists billing records and monthly charging invoices."""
        res = await self._request("/mobile/api/billing/invoices", method="GET")
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            return res.get("invoices", [])
        return []

    async def async_download_invoice_pdf_bytes(self, invoice_id: int | str) -> bytes:
        """Downloads an invoice PDF and decodes the JSON base64 payload if wrapped."""
        url = f"{self.api_base}/mobile/api/billing/invoices/{invoice_id}/pdf"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json, application/pdf",
        }
        async with self._session.get(url, headers=headers) as resp:
            if resp.status == 401 and self.refresh_token:
                await self.async_refresh_access_token()
                headers["Authorization"] = f"Bearer {self.access_token}"
                async with self._session.get(url, headers=headers) as retry_resp:
                    raw_bytes = await retry_resp.read()
            else:
                raw_bytes = await resp.read()

        if not raw_bytes.startswith(b"%PDF"):
            try:
                payload = json.loads(raw_bytes.decode("utf-8"))
                if isinstance(payload, dict) and payload.get("pdfBytes"):
                    return base64.b64decode(payload["pdfBytes"])
            except Exception as err:
                _LOGGER.warning(
                    "Failed to decode non-PDF invoice payload for ID %s: %s",
                    invoice_id,
                    err,
                )
        return raw_bytes

    async def async_get_guest_invitations(
        self, charge_point_id: int
    ) -> list[dict[str, Any]]:
        """Fetches active guest invitations for a charge point."""
        res = await self._request(
            f"/mobile/api/charge-point/{int(charge_point_id)}/guest-invitation",
            method="GET",
        )
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            return res.get("invitations", [])
        return []

    async def async_get_notification_settings(self) -> dict[str, Any]:
        """Fetches user email report and notification settings."""
        res = await self._request("/mobile/api/user/notification/settings", method="GET")
        return res if isinstance(res, dict) else {}

    # -------------------------------------------------------------------------
    # Real-Time SignalR WebSocket Streaming
    # -------------------------------------------------------------------------

    async def async_negotiate_signalr(self, hub_name: str) -> dict[str, Any]:
        """Performs ASP.NET Core SignalR negotiate handshake."""
        return await self._request(
            f"/mobile/hubs/{hub_name}/negotiate?negotiateVersion=1",
            method="POST",
        )

    async def async_stream_signalr_hub(
        self,
        hub_name: str,
        event_callback: Callable[[str, list[Any]], Awaitable[None] | None],
        charge_point_ids: list[int] | None = None,
        stop_event: asyncio.Event | None = None,
        on_connected: Callable[[], Awaitable[None] | None] | None = None,
    ) -> None:
        """Connects to a SignalR hub via WebSocket and dispatches incoming events.

        Automatically sends the SignalR JSON v1 handshake, subscribes to either
        `SubscribeToTransactionsFromAllSites` (on `events` hub) or
        `JoinConnectorAsync(cp_id, 1)` (on `connector` hub), handles keepalive pings,
        and detects broken connections with WebSocket heartbeat (55s).
        """
        neg_data = await self.async_negotiate_signalr(hub_name)
        if not isinstance(neg_data, dict):
            raise NeoVacApiError(
                status_code=500,
                message="Invalid SignalR negotiation response",
                endpoint=f"/mobile/hubs/{hub_name}/negotiate",
                method="POST",
            )

        connection_token = neg_data.get("connectionToken") or neg_data.get("connectionId")
        if not connection_token:
            raise NeoVacApiError(
                status_code=500,
                message=f"Missing connectionToken in negotiate response: {neg_data}",
                endpoint=f"/mobile/hubs/{hub_name}/negotiate",
                method="POST",
            )

        base_ws = self.api_base.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = (
            f"{base_ws}/mobile/hubs/{hub_name}?"
            f"id={urllib.parse.quote(str(connection_token))}"
            f"&access_token={urllib.parse.quote(self.access_token)}"
        )
        headers = {"Authorization": f"Bearer {self.access_token}"}

        try:
            ws = await self._session.ws_connect(
                ws_url, headers=headers, autoping=True, heartbeat=55.0
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise NeoVacAuthError(
                    f"SignalR WebSocket upgrade unauthorized (HTTP {err.status}): {err}"
                ) from err
            raise NeoVacConnectionError(
                f"SignalR WebSocket handshake failed on hub '{hub_name}': {err}"
            ) from err
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise NeoVacConnectionError(
                f"SignalR WebSocket connection failed on hub '{hub_name}': {err}"
            ) from err

        async with ws:
            # 1. Send SignalR JSON v1 Handshake
            await ws.send_str(
                json.dumps({"protocol": "json", "version": 1}) + RECORD_SEPARATOR
            )
            first_msg = await asyncio.wait_for(ws.receive(), timeout=10.0)
            if first_msg.type == aiohttp.WSMsgType.TEXT:
                for chunk in first_msg.data.split(RECORD_SEPARATOR):
                    if chunk.strip():
                        try:
                            h_obj = json.loads(chunk)
                        except json.JSONDecodeError as err:
                            _LOGGER.warning(
                                "SignalR handshake invalid JSON on hub '%s': %s (chunk: %s)",
                                hub_name,
                                err,
                                chunk[:200],
                            )
                            continue
                        if h_obj.get("error"):
                            _LOGGER.error(
                                "SignalR handshake error on hub '%s': %s",
                                hub_name,
                                h_obj["error"],
                            )
                            if "unauthorized" in str(h_obj["error"]).lower() or "token" in str(h_obj["error"]).lower():
                                raise NeoVacAuthError(f"SignalR handshake auth error: {h_obj['error']}")
                            raise NeoVacApiError(
                                status_code=400,
                                message=f"SignalR handshake error: {h_obj['error']}",
                                endpoint=f"/mobile/hubs/{hub_name}",
                                method="WS",
                            )

            # 2. Subscribe to hub streams
            if hub_name == HUB_CONNECTOR and charge_point_ids:
                for cp_id in charge_point_ids:
                    sub_payload = {
                        "type": 1,
                        "target": "JoinConnectorAsync",
                        "arguments": [int(cp_id), 1],
                    }
                    await ws.send_str(json.dumps(sub_payload) + RECORD_SEPARATOR)
            elif hub_name == HUB_EVENTS:
                for sub_target in (
                    "SubscribeToTransactionsFromAllSites",
                    "SubscribeToStatusFromAllSites",
                ):
                    sub_payload = {
                        "type": 1,
                        "target": sub_target,
                        "arguments": [],
                    }
                    await ws.send_str(json.dumps(sub_payload) + RECORD_SEPARATOR)

            # Notify caller that connection and subscriptions succeeded
            if on_connected:
                try:
                    res = on_connected()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as err:
                    _LOGGER.warning("Error in on_connected callback on hub '%s': %s", hub_name, err)

            # 3. Read loop with keepalive tracking
            buffer = ""
            last_activity = time.monotonic()
            last_ping_sent = time.monotonic()
            server_timeout = 120.0  # SignalR keepalive timeout
            ping_interval = 55.0    # Send client keepalive ping every 55s of idle

            while stop_event is None or not stop_event.is_set():
                now = time.monotonic()
                if now - last_activity > server_timeout:
                    _LOGGER.warning(
                        "SignalR hub '%s' keepalive timeout (no activity for %.1fs), closing connection",
                        hub_name,
                        now - last_activity,
                    )
                    break

                if now - last_ping_sent >= ping_interval:
                    try:
                        await ws.send_str(json.dumps({"type": 6}) + RECORD_SEPARATOR)
                        last_ping_sent = now
                    except Exception as err:
                        _LOGGER.warning("Failed to send keepalive ping on hub '%s': %s", hub_name, err)
                        break

                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue

                last_activity = time.monotonic()

                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    _LOGGER.warning(
                        "SignalR WebSocket connection closed or error on hub '%s': type=%s data=%s",
                        hub_name,
                        msg.type,
                        msg.data,
                    )
                    break

                if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    raw_str = (
                        msg.data.decode("utf-8", errors="ignore")
                        if isinstance(msg.data, bytes)
                        else msg.data
                    )
                    buffer += raw_str
                    while RECORD_SEPARATOR in buffer:
                        frame, buffer = buffer.split(RECORD_SEPARATOR, 1)
                        if not frame.strip():
                            continue
                        try:
                            payload = json.loads(frame)
                        except json.JSONDecodeError as err:
                            _LOGGER.warning(
                                "SignalR JSON decode error on hub '%s': %s (frame preview: %s)",
                                hub_name,
                                err,
                                frame[:200],
                            )
                            continue

                        msg_type = payload.get("type")
                        if msg_type == 1:  # Invocation
                            target = payload.get("target", "")
                            args = payload.get("arguments", [])
                            try:
                                res = event_callback(target, args)
                                if asyncio.iscoroutine(res):
                                    await res
                            except Exception as err:
                                _LOGGER.error(
                                    "Error in SignalR event callback for '%s' on hub '%s': %s",
                                    target,
                                    hub_name,
                                    err,
                                    exc_info=True,
                                )
                        elif msg_type == 6:  # Ping
                            try:
                                await ws.send_str(
                                    json.dumps({"type": 6}) + RECORD_SEPARATOR
                                )
                            except Exception:
                                pass
                            last_ping_sent = time.monotonic()
                        elif msg_type == 7:  # Close
                            _LOGGER.warning(
                                "SignalR server sent Close message (type 7) on hub '%s': %s",
                                hub_name,
                                payload,
                            )
                            return
