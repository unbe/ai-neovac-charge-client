"""NeoVac myCharge V Python API Client.

A comprehensive Python client to inspect, monitor, and control NeoVac EV charging
stations without the official mobile application.

Includes:
- Station control (Start, Stop, Cable Lock, Night Schedule, Plug & Charge)
- RFID card management
- Charging reports & session history
- Telemetry & Analytics (Day/Month/Year energy curves, main vs guest users)
- Real-time live telemetry stream via SignalR (WebSockets / Long-Polling)

Uses standard Python library (no external pip dependencies required).
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time
from typing import Any, Callable, Generator
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

try:
    from websockets.sync.client import connect as ws_connect
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False



class NeoVacAPIError(Exception):
    """Raised when an API request fails with a non-2xx status code."""

    def __init__(
        self,
        status_code: int,
        message: str,
        endpoint: str,
        method: str,
        raw_body: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.endpoint = endpoint
        self.method = method
        self.raw_body = raw_body
        self.data = data or {}

        # Build helpful human-friendly explanation
        explanation = message
        if message == "Rejected":
            explanation = (
                "Command 'Rejected' by the charging station.\n"
                "Common reasons:\n"
                "  • Vehicle is not plugged in, or charging cable is disconnected.\n"
                "  • Station is already in an active transaction.\n"
                "  • Station/Vehicle is in a suspended state (SuspendedEV / SuspendedEVSE).\n"
                "  • The station has not finished reporting the previous session."
            )
        elif "UserChargePoint not found" in message:
            explanation = "UserChargePoint not found. Check if the chargePointId is correct."
        elif self.data.get("errors"):
            field_errors = []
            for field, errs in self.data["errors"].items():
                err_str = ", ".join(errs) if isinstance(errs, list) else str(errs)
                field_errors.append(f"{field}: {err_str}")
            explanation = f"{message} ({'; '.join(field_errors)})"

        self.explanation = explanation
        super().__init__(f"[{method} {endpoint}] HTTP {status_code}: {explanation}")


class NeoVacClient:
    """Client for NeoVac myCharge V REST API, Telemetry, and SignalR Hubs."""

    AUTH_BASE = "https://auth.neovac.ch"
    API_BASE = "https://mycharge.neovac.ch"
    CLIENT_ID = "ocpp.interactive"
    REDIRECT_URI = "neovacmychargev:///signin-oidc"
    DEFAULT_SCOPES = "openid offline_access email profile"

    def __init__(self, config_path: str | Path = "neovac_auth.json") -> None:
        self.config_path = Path(config_path)
        self.config: dict[str, Any] = {}
        if self.config_path.exists():
            self.load_config()

    # -------------------------------------------------------------------------
    # Authentication & Token Management
    # -------------------------------------------------------------------------

    def load_config(self) -> None:
        """Loads credentials and tokens from configuration file."""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found at {self.config_path}."
            )
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

    def save_config(self) -> None:
        """Saves active configuration and tokens back to disk."""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    @property
    def is_authenticated(self) -> bool:
        """Checks if an access token is currently stored."""
        return bool(self.access_token)

    @property
    def access_token(self) -> str:
        return self.config.get("access_token", "")

    @property
    def refresh_token(self) -> str:
        return self.config.get("refresh_token", "")

    @property
    def default_charge_point_id(self) -> int:
        return int(self.config.get("charge_point_id", 0))

    @property
    def default_site_id(self) -> str:
        return str(self.config.get("site_id", ""))

    def refresh_access_token(self) -> str:
        """Refreshes the OAuth2 access token using the stored refresh token."""
        url = f"{self.AUTH_BASE}/connect/token"
        payload = urllib.parse.urlencode(
            {
                "client_id": self.CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if "access_token" in data:
            self.config["access_token"] = data["access_token"]
            if "refresh_token" in data:
                self.config["refresh_token"] = data["refresh_token"]
            self.save_config()
            return data["access_token"]
        raise RuntimeError(f"Token refresh failed: {data}")

    @staticmethod
    def generate_pkce_pair() -> tuple[str, str]:
        """Generates (code_verifier, code_challenge) using SHA-256 for PKCE."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return verifier, challenge

    def initiate_login(
        self, state: str | None = None, nonce: str | None = None
    ) -> tuple[str, str]:
        """Initiates an OAuth2 PKCE authorization flow.

        Returns:
            tuple[str, str]: (authorization_url, code_verifier)
        """
        verifier, challenge = self.generate_pkce_pair()
        st = state or secrets.token_urlsafe(16)
        nc = nonce or secrets.token_urlsafe(16)

        params = {
            "client_id": self.CLIENT_ID,
            "response_type": "code",
            "scope": self.DEFAULT_SCOPES,
            "redirect_uri": self.REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": st,
            "nonce": nc,
        }
        auth_url = f"{self.AUTH_BASE}/connect/authorize?{urllib.parse.urlencode(params)}"
        return auth_url, verifier

    def complete_login(
        self, redirect_url_or_code: str, code_verifier: str
    ) -> dict[str, Any]:
        """Exchanges authorization code for access & refresh tokens and discovers account info.

        Args:
            redirect_url_or_code: The full redirected URL (e.g. neovacmychargev:///signin-oidc?code=...)
                                  or the raw authorization code string.
            code_verifier: The code_verifier string generated during initiate_login().

        Returns:
            dict[str, Any]: The updated account configuration.
        """
        code = redirect_url_or_code.strip()
        if "code=" in code:
            parsed = urllib.parse.urlparse(code)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                code = qs["code"][0]
            else:
                qs_frag = urllib.parse.parse_qs(parsed.fragment)
                if "code" in qs_frag:
                    code = qs_frag["code"][0]

        url = f"{self.AUTH_BASE}/connect/token"
        payload = urllib.parse.urlencode(
            {
                "client_id": self.CLIENT_ID,
                "grant_type": "authorization_code",
                "redirect_uri": self.REDIRECT_URI,
                "code_verifier": code_verifier,
                "code": code,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if "access_token" not in data:
            raise RuntimeError(f"Authentication failed: {data}")

        self.config["access_token"] = data["access_token"]
        if "refresh_token" in data:
            self.config["refresh_token"] = data["refresh_token"]
        if "expires_in" in data:
            self.config["expires_in"] = data["expires_in"]

        # Auto-discover user info and primary charge point
        try:
            info = self.get_user_info()
            self.config["account_id"] = info.get("accountId")
            sites = info.get("sites", [])
            if sites:
                self.config["site_id"] = sites[0].get("siteId")
                cps = sites[0].get("userChargePoints", [])
                if cps:
                    self.config["charge_point_id"] = cps[0].get("chargePointId")
                    self.config["serial_number"] = cps[0].get("serialNumber")
                    self.config["position"] = cps[0].get("position")
        except Exception:
            pass

        self.save_config()
        return self.config

    def interactive_login(self, open_browser: bool = True) -> dict[str, Any]:
        """Runs the interactive PKCE login workflow in terminal."""
        auth_url, verifier = self.initiate_login()
        print("\n" + "=" * 70)
        print("NEOVAC MYCHARGE V - INITIAL AUTHENTICATION")
        print("=" * 70)
        print("\n1. Login URL:")
        print(f"   {auth_url}\n")
        if open_browser:
            print("   Opening login URL in your default browser...")
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass
        print("2. Log in with your NeoVac account credentials.")
        print("3. After login, your browser will redirect to a page starting with:")
        print("   neovacmychargev:///signin-oidc?code=...")
        print("\n4. Copy and paste the entire redirected URL (or just the code):")
        redirect_input = input("\nRedirect URL or code: ").strip()
        if not redirect_input:
            raise ValueError("No authorization code provided.")

        print("\nExchanging authorization code for tokens and discovering stations...")
        config = self.complete_login(redirect_input, verifier)
        print("\n[SUCCESS] Authentication complete!")
        print(f"  Account ID:      {config.get('account_id')}")
        print(f"  Site ID:         {config.get('site_id')}")
        print(f"  Charge Point ID: {config.get('charge_point_id')} ({config.get('position', '')})")
        print(f"  Config saved to: {self.config_path}\n")
        return config

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        retry_on_401: bool = True,
    ) -> Any:
        """Internal helper for executing authenticated HTTP requests."""
        if not self.access_token:
            raise RuntimeError(
                "Not authenticated. Please run 'python3 neovac_client.py login' first."
            )
        url = f"{self.API_BASE}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            if err.code == 401 and retry_on_401 and self.refresh_token:
                try:
                    self.refresh_access_token()
                    return self._request(
                        endpoint, method=method, data=data, retry_on_401=False
                    )
                except Exception:
                    pass

            error_body = err.read().decode("utf-8", errors="ignore")
            parsed_data = {}
            error_msg = f"HTTP {err.code}"
            try:
                parsed = json.loads(error_body)
                if isinstance(parsed, dict):
                    parsed_data = parsed
                    error_msg = (
                        parsed.get("message")
                        or parsed.get("error_description")
                        or parsed.get("error")
                        or str(parsed)
                    )
            except Exception:
                error_msg = error_body.strip() or f"HTTP {err.code}"

            raise NeoVacAPIError(
                status_code=err.code,
                message=error_msg,
                endpoint=endpoint,
                method=method,
                raw_body=error_body,
                data=parsed_data,
            ) from err

    # -------------------------------------------------------------------------
    # Charging Status & Control
    # -------------------------------------------------------------------------

    def get_user_info(self) -> dict[str, Any]:
        """Fetches full user account profile, sites, and assigned charge points."""
        return self._request("/mobile/api/user/info", method="GET")

    def get_station_status(self, charge_point_id: int | None = None) -> dict[str, Any]:
        """Returns details and live status for a specific charge point."""
        cpid = charge_point_id or self.default_charge_point_id
        info = self.get_user_info()
        for site in info.get("sites", []):
            for cp in site.get("userChargePoints", []):
                if cp.get("chargePointId") == cpid:
                    return cp
        raise ValueError(f"Charge point {cpid} not found in user account.")

    def start_charging(
        self,
        charge_point_id: int | None = None,
        rfid_tag: str | None = None,
    ) -> dict[str, Any]:
        """Sends a remote start transaction command to the charging station."""
        cpid = charge_point_id or self.default_charge_point_id
        payload: dict[str, Any] = {"chargePointId": cpid}
        if rfid_tag:
            payload["rfidTag"] = rfid_tag
        return self._request(
            "/mobile/api/charge-point/commands/remote-start-transaction",
            method="POST",
            data=payload,
        )

    def stop_charging(
        self,
        charge_point_id: int | None = None,
    ) -> dict[str, Any]:
        """Sends a remote stop transaction command to the charging station."""
        cpid = charge_point_id or self.default_charge_point_id
        return self._request(
            "/mobile/api/charge-point/commands/remote-stop-transaction",
            method="POST",
            data={"chargePointId": cpid},
        )

    # -------------------------------------------------------------------------
    # Hardware & Feature Configuration
    # -------------------------------------------------------------------------

    def set_permanent_cable_lock(
        self,
        enabled: bool,
        charge_point_id: int | None = None,
    ) -> dict[str, Any]:
        """Enables or disables permanent cable locking on the station."""
        cpid = charge_point_id or self.default_charge_point_id
        return self._request(
            f"/mobile/api/charge-point/{cpid}/lock-cable",
            method="POST",
            data={"enabled": enabled},
        )

    def set_night_charging(
        self,
        enabled: bool,
        charge_point_id: int | None = None,
    ) -> dict[str, Any]:
        """Toggles night-tariff charging schedule (22:00 - 06:00)."""
        cpid = charge_point_id or self.default_charge_point_id
        return self._request(
            f"/mobile/api/charge-point/{cpid}/night-charging",
            method="POST",
            data={"enabled": enabled},
        )

    def set_plug_and_charge(
        self,
        enabled: bool,
        charge_point_id: int | None = None,
    ) -> dict[str, Any]:
        """Toggles plug-and-charge (charging starts automatically on plug-in)."""
        cpid = charge_point_id or self.default_charge_point_id
        return self._request(
            f"/mobile/api/charge-point/{cpid}/plug-and-charge",
            method="POST",
            data={"enabled": enabled},
        )

    def rename_station(
        self,
        new_name: str,
        charge_point_id: int | None = None,
    ) -> dict[str, Any]:
        """Sets a custom user-defined display name for the charging station."""
        cpid = charge_point_id or self.default_charge_point_id
        return self._request(
            f"/mobile/api/charge-point/{cpid}/update-name",
            method="POST",
            data={"newName": new_name},
        )

    # -------------------------------------------------------------------------
    # RFID Badge Management
    # -------------------------------------------------------------------------

    def list_rfids(self) -> list[dict[str, Any]]:
        """Lists all registered RFID cards/badges for the account."""
        resp = self._request("/mobile/api/user/rfid", method="GET")
        return resp.get("rfids", []) if resp else []

    def add_rfid(self, rfid_tag: str, rfid_name: str) -> dict[str, Any]:
        """Registers a new physical RFID card/badge."""
        return self._request(
            "/mobile/api/user/rfid",
            method="POST",
            data={"rfidTag": rfid_tag, "rfidName": rfid_name},
        )

    def update_rfid(self, rfid_id: int, rfid_name: str) -> dict[str, Any]:
        """Renames an existing RFID badge."""
        return self._request(
            f"/mobile/api/user/rfid/{rfid_id}",
            method="PATCH",
            data={"rfidName": rfid_name},
        )

    def delete_rfid(self, rfid_id: int) -> dict[str, Any]:
        """Deletes an RFID badge by ID."""
        return self._request(f"/mobile/api/user/rfid/{rfid_id}", method="DELETE")

    # -------------------------------------------------------------------------
    # Charging Reports & Session History
    # -------------------------------------------------------------------------

    def get_charge_report(
        self,
        charge_point_id: int | None = None,
        days: int = 30,
        earliest_date: datetime | None = None,
        latest_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Retrieves session history, total energy (kWh), costs, and process counts."""
        cpid = charge_point_id or self.default_charge_point_id
        now = datetime.now(timezone.utc)
        start = (earliest_date or (now - timedelta(days=days))).strftime("%Y-%m-%d")
        end = (latest_date or now).strftime("%Y-%m-%d")
        query = urllib.parse.urlencode({"earliestDate": start, "latestDate": end})
        return self._request(
            f"/mobile/api/charge-point/{cpid}/report?{query}", method="GET"
        )

    def get_site_report(
        self,
        site_id: str | None = None,
        days: int = 30,
        earliest_date: datetime | None = None,
        latest_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Retrieves aggregate site-wide charging session reports."""
        sid = site_id or self.default_site_id
        now = datetime.now(timezone.utc)
        start = (earliest_date or (now - timedelta(days=days))).strftime("%Y-%m-%d")
        end = (latest_date or now).strftime("%Y-%m-%d")
        query = urllib.parse.urlencode({"earliestDate": start, "latestDate": end})
        return self._request(
            f"/mobile/api/sites/{sid}/report?{query}", method="GET"
        )

    def request_email_report(
        self,
        charge_point_id: int | None = None,
        earliest_date: datetime | None = None,
        latest_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Triggers generating and emailing a detailed PDF report for the timespan."""
        cpid = charge_point_id or self.default_charge_point_id
        now = datetime.now(timezone.utc)
        start = (earliest_date or (now - timedelta(days=30))).strftime("%Y-%m-%d")
        end = (latest_date or now).strftime("%Y-%m-%d")
        payload = {
            "chargePointId": cpid,
            "earliestDate": start,
            "latestDate": end,
        }
        return self._request(
            "/mobile/api/charge-reports/create-charge-point-report",
            method="POST",
            data=payload,
        )

    # -------------------------------------------------------------------------
    # Analytics & Energy Curves
    # -------------------------------------------------------------------------

    def get_analytics(
        self,
        charge_point_id: int | None = None,
        resolution: str = "Month",
        days: int = 30,
        earliest_date: datetime | None = None,
        latest_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Fetches energy telemetry data points.

        Resolution options:
        - 'Day': Hourly charging power in kW (telemetry curves for 24h)
        - 'Month': Daily energy consumption in kWh
        - 'Year': Monthly energy consumption in kWh
        """
        cpid = charge_point_id or self.default_charge_point_id
        now = datetime.now(timezone.utc)
        start = (earliest_date or (now - timedelta(days=days))).strftime("%Y-%m-%d")
        end = (latest_date or now).strftime("%Y-%m-%d")
        query = urllib.parse.urlencode({
            "earliestDate": start,
            "latestDate": end,
            "resolution": resolution,
        })
        return self._request(
            f"/mobile/api/charge-point/{cpid}/analytics?{query}", method="GET"
        )

    def get_site_analytics(
        self,
        site_id: str | None = None,
        resolution: str = "Month",
        days: int = 30,
        earliest_date: datetime | None = None,
        latest_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Fetches site-wide aggregated analytics curve."""
        sid = site_id or self.default_site_id
        now = datetime.now(timezone.utc)
        start = (earliest_date or (now - timedelta(days=days))).strftime("%Y-%m-%d")
        end = (latest_date or now).strftime("%Y-%m-%d")
        query = urllib.parse.urlencode({
            "earliestDate": start,
            "latestDate": end,
            "resolution": resolution,
        })
        return self._request(
            f"/mobile/api/sites/{sid}/analytics?{query}", method="GET"
        )

    # -------------------------------------------------------------------------
    # Real-Time Telemetry Stream (SignalR Hub)
    # -------------------------------------------------------------------------

    def _negotiate_signalr(self, hub_name: str) -> dict[str, Any]:
        """Performs ASP.NET Core SignalR negotiation."""
        negotiate_url = f"{self.API_BASE}/mobile/hubs/{hub_name}/negotiate?negotiateVersion=1"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "text/plain;charset=UTF-8",
        }
        neg_req = urllib.request.Request(negotiate_url, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(neg_req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 401:
                self.refresh_access_token()
                headers["Authorization"] = f"Bearer {self.access_token}"
                neg_req = urllib.request.Request(negotiate_url, headers=headers, method="POST")
                with urllib.request.urlopen(neg_req) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            raise

    def _stream_telemetry_websocket(
        self,
        hub_name: str,
        connection_token: str,
        duration_seconds: int,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Streams telemetry over a persistent WebSocket connection using SignalR protocol."""
        if not HAS_WEBSOCKETS:
            raise RuntimeError(
                "The 'websockets' package is required for WebSocket transport. "
                "Install it with 'pip install websockets' or 'sudo apt install python3-websockets'."
            )

        base_ws = self.API_BASE.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = (
            f"{base_ws}/mobile/hubs/{hub_name}?"
            f"id={urllib.parse.quote(connection_token)}&access_token={urllib.parse.quote(self.access_token)}"
        )
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }

        # Step 1: Connect WebSocket
        with ws_connect(ws_url, additional_headers=headers) as ws:
            # Step 2: SignalR Handshake
            handshake = json.dumps({"protocol": "json", "version": 1}) + "\x1e"
            ws.send(handshake)
            raw_handshake = ws.recv(timeout=10)
            if isinstance(raw_handshake, bytes):
                raw_handshake = raw_handshake.decode("utf-8", errors="ignore")
            for chunk in raw_handshake.split("\x1e"):
                if chunk.strip():
                    try:
                        h_obj = json.loads(chunk)
                        if h_obj.get("error"):
                            raise RuntimeError(f"SignalR handshake error: {h_obj['error']}")
                    except json.JSONDecodeError:
                        pass

            # Step 3: Subscription
            if hub_name == "connector":
                sub_target = "JoinConnectorAsync"
                sub_args: list[Any] = [self.default_charge_point_id, 1]
            else:
                sub_target = "SubscribeToTransactionsFromAllSites"
                sub_args = []

            sub_msg = (
                json.dumps({
                    "type": 1,
                    "target": sub_target,
                    "arguments": sub_args,
                })
                + "\x1e"
            )
            ws.send(sub_msg)

            # Step 4: Stream incoming telemetry
            end_time = time.time() + duration_seconds
            buffer = ""
            while time.time() < end_time:
                remaining = end_time - time.time()
                if remaining <= 0:
                    break
                try:
                    recv_timeout = min(remaining, 5.0)
                    data = ws.recv(timeout=recv_timeout)
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="ignore")
                    buffer += data
                    while "\x1e" in buffer:
                        chunk, buffer = buffer.split("\x1e", 1)
                        if not chunk.strip():
                            continue
                        try:
                            msg = json.loads(chunk)
                            msg_type = msg.get("type")
                            if msg_type == 1:  # Invocation message
                                event = {
                                    "target": msg.get("target"),
                                    "arguments": msg.get("arguments", []),
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "transport": "websocket",
                                }
                                if callback:
                                    callback(event)
                                yield event
                            elif msg_type == 6:  # Ping
                                try:
                                    ws.send(json.dumps({"type": 6}) + "\x1e")
                                except Exception:
                                    pass
                            elif msg_type == 7:  # Close message
                                return
                        except json.JSONDecodeError:
                            pass
                except TimeoutError:
                    continue
                except Exception:
                    break

    def _stream_telemetry_long_polling(
        self,
        hub_name: str,
        connection_token: str,
        duration_seconds: int,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Streams telemetry using SignalR HTTP Long-Polling fallback."""
        connect_url = f"{self.API_BASE}/mobile/hubs/{hub_name}?id={urllib.parse.quote(connection_token)}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "text/plain;charset=UTF-8",
        }

        # Step 1: Handshake
        handshake_payload = (json.dumps({"protocol": "json", "version": 1}) + "\x1e").encode("utf-8")
        post_req = urllib.request.Request(connect_url, data=handshake_payload, headers=headers, method="POST")
        with urllib.request.urlopen(post_req) as resp:
            _ = resp.read()

        # Step 2: Subscription
        if hub_name == "connector":
            sub_target = "JoinConnectorAsync"
            sub_args: list[Any] = [self.default_charge_point_id, 1]
        else:
            sub_target = "SubscribeToTransactionsFromAllSites"
            sub_args = []

        sub_msg = (
            json.dumps({
                "type": 1,
                "target": sub_target,
                "arguments": sub_args,
            })
            + "\x1e"
        ).encode("utf-8")
        sub_req = urllib.request.Request(connect_url, data=sub_msg, headers=headers, method="POST")
        with urllib.request.urlopen(sub_req) as resp:
            _ = resp.read()

        # Step 3: Poll incoming telemetry stream
        end_time = time.time() + duration_seconds
        while time.time() < end_time:
            poll_req = urllib.request.Request(connect_url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(poll_req) as resp:
                    raw_messages = resp.read().decode("utf-8")
                    if raw_messages:
                        for chunk in raw_messages.split("\x1e"):
                            if not chunk.strip():
                                continue
                            try:
                                msg = json.loads(chunk)
                                if msg.get("type") == 1:  # Invocation message
                                    event = {
                                        "target": msg.get("target"),
                                        "arguments": msg.get("arguments", []),
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                        "transport": "long-polling",
                                    }
                                    if callback:
                                        callback(event)
                                    yield event
                            except json.JSONDecodeError:
                                pass
            except urllib.error.HTTPError as err:
                if err.code in (204, 503):
                    time.sleep(1)
                    continue
                break
            except Exception:
                time.sleep(1)

    def stream_telemetry(
        self,
        hub_name: str = "events",
        duration_seconds: int = 60,
        callback: Callable[[dict[str, Any]], None] | None = None,
        transport: str = "auto",
    ) -> Generator[dict[str, Any], None, None]:
        """Connects to NeoVac's ASP.NET Core SignalR Event/Connector Hub.

        Listens for real-time live charging events:
        - 'OnTransactionReport' (live charging kW, kWh, active session state)
        - 'ReceiveConnectorTransactionUpdate'
        - 'StatusHub' state transitions

        Args:
            hub_name: Hub to connect to ('events' or 'connector').
            duration_seconds: Stream listening duration in seconds.
            callback: Optional callback invoked for each received event.
            transport: Transport protocol: 'auto' (WebSocket if available, else Long-Polling),
                       'websocket' (requires websockets package), or 'long-polling' (standard library).
        """
        if transport not in ("auto", "websocket", "long-polling"):
            raise ValueError(
                f"Invalid transport: '{transport}'. Choose from 'auto', 'websocket', or 'long-polling'."
            )

        selected_transport = transport
        if selected_transport == "auto":
            selected_transport = "websocket" if HAS_WEBSOCKETS else "long-polling"

        neg_data = self._negotiate_signalr(hub_name)
        connection_token = neg_data.get("connectionToken") or neg_data.get("connectionId")
        if not connection_token:
            raise RuntimeError(f"SignalR negotiation failed: {neg_data}")

        if selected_transport == "websocket":
            yield from self._stream_telemetry_websocket(
                hub_name=hub_name,
                connection_token=connection_token,
                duration_seconds=duration_seconds,
                callback=callback,
            )
        else:
            yield from self._stream_telemetry_long_polling(
                hub_name=hub_name,
                connection_token=connection_token,
                duration_seconds=duration_seconds,
                callback=callback,
            )

    # -------------------------------------------------------------------------
    # Pricing & Invoices
    # -------------------------------------------------------------------------

    def get_pricing(
        self,
        charge_point_id: int | None = None,
        connector_number: int = 1,
    ) -> dict[str, Any]:
        """Retrieves active electricity tariffs and connector pricing."""
        cpid = charge_point_id or self.default_charge_point_id
        return self._request(
            f"/mobile/api/pricing/{cpid}/{connector_number}", method="GET"
        )

    def list_invoices(self) -> list[dict[str, Any]]:
        """Lists billing records and monthly charging invoices."""
        return self._request("/mobile/api/billing/invoices", method="GET")

    def download_invoice_pdf(
        self, invoice_id: int | str, target_file: str | Path
    ) -> None:
        """Downloads a PDF invoice to the given file path.

        Handles both JSON responses containing base64-encoded ``pdfBytes``
        (as used by the mobile app's BillingService) and raw PDF binary streams.
        """
        url = f"{self.API_BASE}/mobile/api/billing/invoices/{invoice_id}/pdf"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json, application/pdf",
            },
        )
        with urllib.request.urlopen(req) as resp:
            raw_bytes = resp.read()

        pdf_bytes = raw_bytes
        if not raw_bytes.startswith(b"%PDF"):
            try:
                payload = json.loads(raw_bytes.decode("utf-8"))
                if isinstance(payload, dict) and payload.get("pdfBytes"):
                    pdf_bytes = base64.b64decode(payload["pdfBytes"])
            except Exception:
                pass

        with open(target_file, "wb") as f:
            f.write(pdf_bytes)

    def get_guest_invitations(
        self, charge_point_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Fetches active guest user invitations for a charge point."""
        cpid = charge_point_id or self.default_charge_point_id
        resp = self._request(
            f"/mobile/api/charge-point/{cpid}/guest-invitation", method="GET"
        )
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict):
            return resp.get("invitations", [])
        return []

    def get_notification_settings(self) -> dict[str, Any]:
        """Fetches user email report and push notification settings."""
        resp = self._request("/mobile/api/user/notification/settings", method="GET")
        return resp if isinstance(resp, dict) else {}


# -----------------------------------------------------------------------------
# Command Line Interface (CLI)
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="NeoVac myCharge V CLI Client")
    subparsers = parser.add_subparsers(dest="subcommand", help="Command to run")

    # Status
    status_p = subparsers.add_parser("status", help="Get station status")
    status_p.add_argument("--id", type=int, help="Charge Point ID override")
    status_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Account / User
    account_p = subparsers.add_parser("account", aliases=["user", "info"], help="Get user account and site overview")
    account_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Start / Stop
    start_p = subparsers.add_parser("start", help="Start charging session")
    start_p.add_argument("--rfid", help="RFID Tag UID (optional)")
    start_p.add_argument("--id", type=int, help="Charge Point ID override")

    stop_p = subparsers.add_parser("stop", help="Stop charging session")
    stop_p.add_argument("--id", type=int, help="Charge Point ID override")

    # Cable lock & Night mode
    lock_p = subparsers.add_parser("cable-lock", help="Configure permanent cable lock")
    lock_p.add_argument("state", choices=["on", "off"], help="Lock state")
    lock_p.add_argument("--id", type=int, help="Charge Point ID override")

    night_p = subparsers.add_parser("night-charging", help="Configure night charging")
    night_p.add_argument("state", choices=["on", "off"], help="Schedule state")
    night_p.add_argument("--id", type=int, help="Charge Point ID override")

    plug_p = subparsers.add_parser("plug-and-charge", help="Configure plug-and-charge mode")
    plug_p.add_argument("state", choices=["on", "off"], help="Mode state (on/off)")
    plug_p.add_argument("--id", type=int, help="Charge Point ID override")

    rename_p = subparsers.add_parser("rename", help="Set custom name for station")
    rename_p.add_argument("name", help="New station name")
    rename_p.add_argument("--id", type=int, help="Charge Point ID override")

    # RFID commands
    rfid_p = subparsers.add_parser("rfid", aliases=["rfids"], help="Manage registered RFID badges (show, add, rename, delete)")
    rfid_sub = rfid_p.add_subparsers(dest="rfid_action", help="RFID action")

    # rfid show / list
    rfid_show_p = rfid_sub.add_parser("show", aliases=["list"], help="Show/list registered RFID badges (default)")
    rfid_show_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # rfid add <tag> <name>
    rfid_add_p = rfid_sub.add_parser("add", help="Register a new RFID badge")
    rfid_add_p.add_argument("tag", help="Hardware UID hex string (e.g. 01020304050607 or 01020304)")
    rfid_add_p.add_argument("name", help="Display name for the badge")

    # rfid rename <id> <new_name>
    rfid_rename_p = rfid_sub.add_parser("rename", help="Rename an existing RFID badge")
    rfid_rename_p.add_argument("id", type=int, help="RFID badge database ID")
    rfid_rename_p.add_argument("name", help="New display name")

    # rfid delete <id>
    rfid_del_p = rfid_sub.add_parser("delete", aliases=["remove", "rm"], help="Delete an RFID badge")
    rfid_del_p.add_argument("id", type=int, help="RFID badge database ID to delete")

    # Pricing & Tariffs
    pricing_p = subparsers.add_parser("pricing", aliases=["tariff", "tariffs"], help="Get active electricity tariffs and connector pricing")
    pricing_p.add_argument("--id", type=int, help="Charge Point ID override")
    pricing_p.add_argument("--connector", type=int, default=1, help="Connector number (default: 1)")
    pricing_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Invoices & Billing
    inv_p = subparsers.add_parser("invoices", aliases=["billing"], help="Manage billing invoices and download PDFs")
    inv_sub = inv_p.add_subparsers(dest="invoice_action", help="Invoice action")

    inv_list_p = inv_sub.add_parser("list", aliases=["show"], help="List billing records and invoices (default)")
    inv_list_p.add_argument("--json", action="store_true", help="Output raw JSON")

    inv_down_p = inv_sub.add_parser("download", help="Download invoice PDF")
    inv_down_p.add_argument("id", help="Invoice ID")
    inv_down_p.add_argument("--output", "-o", help="Target PDF file path (default: invoice_<id>.pdf)")

    # Guest Invitations
    guest_p = subparsers.add_parser("guests", aliases=["invitations", "guest-invitations"], help="List active guest invitations")
    guest_p.add_argument("--id", type=int, help="Charge Point ID override")
    guest_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Notifications
    notif_p = subparsers.add_parser("notifications", aliases=["notification-settings"], help="View notification and email report preferences")
    notif_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Reports
    report_p = subparsers.add_parser("report", help="View charging history report")
    report_p.add_argument("--days", type=int, default=30, help="Days to look back (default: 30)")
    report_p.add_argument("--id", type=int, help="Charge Point ID override")
    report_p.add_argument("--site", help="Site ID override for aggregate site report")
    report_p.add_argument("--email", action="store_true", help="Send report as PDF via email")
    report_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Analytics
    analytics_p = subparsers.add_parser("analytics", help="View energy consumption curves")
    analytics_p.add_argument("--resolution", choices=["Day", "Month", "Year"], default="Month",
                             help="Telemetry resolution: Day (hourly kW), Month (daily kWh), Year (monthly kWh)")
    analytics_p.add_argument("--days", type=int, default=30, help="Days to look back (default: 30)")
    analytics_p.add_argument("--id", type=int, help="Charge Point ID override")
    analytics_p.add_argument("--site", help="Site ID override for aggregate site analytics")
    analytics_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Live Telemetry Stream
    telemetry_p = subparsers.add_parser("telemetry", help="Stream live real-time charging telemetry")
    telemetry_p.add_argument("--duration", type=int, default=60, help="Stream duration in seconds (default: 60)")
    telemetry_p.add_argument("--hub", choices=["events", "connector"], default="events",
                             help="SignalR hub ('events' for StatusHub, 'connector' for ConnectorHub)")
    telemetry_p.add_argument("--transport", choices=["auto", "websocket", "long-polling"], default="auto",
                             help="Transport protocol: 'websocket' (requires websockets package), 'long-polling' (standard library), or 'auto' (default: auto)")

    # Login
    login_p = subparsers.add_parser("login", help="Initial OAuth2 interactive login")
    login_p.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")

    # Refresh
    subparsers.add_parser("refresh", help="Refresh OAuth2 access token")

    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        return

    try:
        client = NeoVacClient()

        if args.subcommand == "login":
            client.interactive_login(open_browser=not args.no_browser)

        elif args.subcommand in ("account", "user", "info"):
            user_info = client.get_user_info()
            if getattr(args, "json", False):
                print(json.dumps(user_info, indent=2))
            else:
                user_id = user_info.get("userId") or user_info.get("id")
                email = user_info.get("email", "N/A")
                name = f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip() or "N/A"
                print(f"User:        {name} ({email})")
                print(f"User ID:     {user_id}")
                sites = user_info.get("sites", [])
                print(f"Sites ({len(sites)}):")
                for s in sites:
                    sid = s.get("siteId") or s.get("id")
                    sname = s.get("name") or s.get("siteName", "Site")
                    cps = s.get("userChargePoints", [])
                    print(f"  • {sname} (ID: {sid}) - {len(cps)} station(s)")
                    for cp in cps:
                        cpid = cp.get("chargePointId")
                        cpname = cp.get("position") or cp.get("neoVacId")
                        status = cp.get("status", "Unknown")
                        print(f"      - [{cpid}] {cpname}: {status}")

        elif args.subcommand == "status":
            info = client.get_station_status(charge_point_id=args.id)
            if getattr(args, "json", False):
                print(json.dumps(info, indent=2))
            else:
                print(f"Station:     {info.get('position')} ({info.get('neoVacId')})")
                print(f"ID:          {info.get('chargePointId')}")
                print(f"Serial:      {info.get('serialNumber')}")
                print(f"Status:      {info.get('status')}")
                print(f"Connector:   {info.get('connectorZeroStatus')}")
                print(f"Online:      {info.get('isOnline')}")
                print(f"Cable Lock:     {info.get('chargePointSettings', {}).get('permanentCableLockEnabled')}")
                print(f"Night Mode:     {info.get('chargePointSettings', {}).get('nightChargingEnabled')}")
                print(f"Plug & Charge:  {info.get('chargePointSettings', {}).get('plugAndChargeEnabled')}")

        elif args.subcommand == "start":
            res = client.start_charging(charge_point_id=args.id, rfid_tag=args.rfid)
            print(json.dumps(res, indent=2))

        elif args.subcommand == "stop":
            res = client.stop_charging(charge_point_id=args.id)
            print(json.dumps(res, indent=2))

        elif args.subcommand == "cable-lock":
            res = client.set_permanent_cable_lock(args.state == "on", charge_point_id=args.id)
            print(json.dumps(res, indent=2))

        elif args.subcommand == "night-charging":
            res = client.set_night_charging(args.state == "on", charge_point_id=args.id)
            print(json.dumps(res, indent=2))

        elif args.subcommand == "plug-and-charge":
            res = client.set_plug_and_charge(args.state == "on", charge_point_id=args.id)
            print(json.dumps(res, indent=2))

        elif args.subcommand == "rename":
            res = client.rename_station(args.name, charge_point_id=args.id)
            print(json.dumps(res, indent=2))

        elif args.subcommand in ("rfid", "rfids"):
            action = getattr(args, "rfid_action", None) or "show"
            if action in ("show", "list"):
                rfids = client.list_rfids()
                if getattr(args, "json", False):
                    print(json.dumps(rfids, indent=2))
                else:
                    if not rfids:
                        print("No RFID badges registered.")
                    for r in rfids:
                        print(f"- {r.get('rfidName')} (Tag: {r.get('rfidTag')}, ID: {r.get('id')})")
            elif action == "add":
                clean_tag = args.tag.replace(":", "").replace("-", "").replace(" ", "").upper()
                res = client.add_rfid(rfid_tag=clean_tag, rfid_name=args.name)
                print(f"Added RFID badge '{args.name}' ({clean_tag}):")
                print(json.dumps(res, indent=2) if res else "Success.")
            elif action == "rename":
                res = client.update_rfid(rfid_id=args.id, rfid_name=args.name)
                print(f"Renamed RFID badge ID {args.id} to '{args.name}':")
                print(json.dumps(res, indent=2) if res else "Success.")
            elif action in ("delete", "remove", "rm"):
                res = client.delete_rfid(rfid_id=args.id)
                print(f"Deleted RFID badge ID {args.id}:")
                print(json.dumps(res, indent=2) if res else "Success.")

        elif args.subcommand in ("pricing", "tariff", "tariffs"):
            pricing = client.get_pricing(charge_point_id=args.id, connector_number=args.connector)
            if getattr(args, "json", False):
                print(json.dumps(pricing, indent=2))
            else:
                name = pricing.get("pricingPlanName") or pricing.get("name") or "Standard"
                curr = pricing.get("currency", "CHF")
                vat = pricing.get("vatPercent")
                print(f"Tariff Plan:   {name}")
                if "pricePerKwh" in pricing:
                    print(f"Energy Price:  {pricing['pricePerKwh']:.4f} {curr}/kWh")
                if "pricePerMinute" in pricing:
                    print(f"Time Price:    {pricing['pricePerMinute']:.4f} {curr}/min")
                if "startFee" in pricing:
                    print(f"Starting Fee:  {pricing['startFee']:.2f} {curr}")
                if vat is not None:
                    print(f"VAT:           {vat}%")

        elif args.subcommand in ("invoices", "billing"):
            action = getattr(args, "invoice_action", None) or "list"
            if action in ("list", "show"):
                invoices = client.list_invoices()
                if getattr(args, "json", False):
                    print(json.dumps(invoices, indent=2))
                else:
                    if not invoices:
                        print("No invoices found.")
                    for inv in invoices:
                        inv_id = inv.get("id") or inv.get("invoiceId")
                        num = inv.get("invoiceNumber", "N/A")
                        date = inv.get("date") or inv.get("createdDate") or inv.get("invoiceDate", "N/A")
                        amount = inv.get("amount") if "amount" in inv else inv.get("totalAmount", 0.0)
                        curr = inv.get("currency", "CHF")
                        status = inv.get("status", "N/A")
                        amt_str = f"{amount:.2f} {curr}" if isinstance(amount, (int, float)) else f"{amount} {curr}"
                        print(f"- Invoice #{num} (ID: {inv_id}): {amt_str} | Date: {date} | Status: {status}")
            elif action == "download":
                out_path = args.output or f"invoice_{args.id}.pdf"
                client.download_invoice_pdf(args.id, out_path)
                print(f"Downloaded invoice {args.id} to {out_path}")

        elif args.subcommand in ("guests", "invitations", "guest-invitations"):
            invitations = client.get_guest_invitations(charge_point_id=args.id)
            if getattr(args, "json", False):
                print(json.dumps(invitations, indent=2))
            else:
                if not invitations:
                    print("No active guest invitations.")
                for inv in invitations:
                    guest_id = inv.get("id") or inv.get("invitationId")
                    recipient = inv.get("email") or inv.get("recipient") or inv.get("phoneNumber", "N/A")
                    role = inv.get("role") or inv.get("invitationRole", "Guest")
                    state = inv.get("status") or inv.get("state", "Active")
                    print(f"- Invitation ID {guest_id}: {recipient} ({role}) - {state}")

        elif args.subcommand in ("notifications", "notification-settings"):
            notif = client.get_notification_settings()
            if getattr(args, "json", False):
                print(json.dumps(notif, indent=2))
            else:
                print("Notification Settings:")
                for k, v in notif.items():
                    print(f"  {k}: {v}")

        elif args.subcommand == "report":
            if args.email:
                res = client.request_email_report(charge_point_id=args.id)
                print("Email report requested:", json.dumps(res, indent=2))
            elif getattr(args, "site", None):
                rep = client.get_site_report(site_id=args.site, days=args.days)
                print(json.dumps(rep, indent=2))
            else:
                rep = client.get_charge_report(charge_point_id=args.id, days=args.days)
                print(json.dumps(rep, indent=2))

        elif args.subcommand == "analytics":
            if getattr(args, "site", None):
                an = client.get_site_analytics(site_id=args.site, resolution=args.resolution, days=args.days)
            else:
                an = client.get_analytics(charge_point_id=args.id, resolution=args.resolution, days=args.days)
            print(json.dumps(an, indent=2))

        elif args.subcommand == "telemetry":
            transport_mode = args.transport
            if transport_mode == "auto":
                transport_mode = f"auto ({'websocket' if HAS_WEBSOCKETS else 'long-polling'})"
            print(f"Starting real-time telemetry stream on hub '{args.hub}' via {transport_mode} for {args.duration}s... (Ctrl+C to stop)")
            for event in client.stream_telemetry(hub_name=args.hub, duration_seconds=args.duration, transport=args.transport):
                print(f"[{event['timestamp']}] Event: {event['target']} (via {event.get('transport', 'unknown')})")
                print(json.dumps(event['arguments'], indent=2))

        elif args.subcommand == "refresh":
            client.refresh_access_token()
            print("Access token refreshed successfully.")

    except NeoVacAPIError as err:
        print(f"\n[API Error {err.status_code}] {err.explanation}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(130)
    except Exception as err:
        print(f"\n[Error] {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
