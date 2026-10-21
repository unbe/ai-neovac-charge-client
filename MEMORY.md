# MEMORY.md — NeoVac myCharge V Project Memory

## Project Overview

`ai-chargeV` is a standalone Python client and CLI for monitoring and controlling NeoVac myCharge V EV charging stations in Switzerland. It reverse-engineers and reproduces functionality from the official Android mobile app (`ch.neovac.mychargev.mobile`, built on Microsoft .NET MAUI / MauiReactor).

### Core Principles
- **Standard Library Only**: The Python client (`neovac_client.py`) operates with zero third-party dependencies (`urllib`, `json`, `argparse`, `secrets`, `hashlib`, etc.).
- **Source of Truth**: This `MEMORY.md` file tracks all reverse-engineered architecture, API endpoints, DTO contracts, system constraints, and subtle edge cases.
- **Privacy & Security**: **Never commit any identifiers (tokens, station IDs, serial numbers, account names/IDs, RFID badges, private configurations) to Git.** Always use generic dummy placeholders in tracked files and documentation. Keep credentials in untracked files (`neovac_auth.json`).
- **Safety Policy**: **Never execute authenticated external requests using the user's credentials without explicit user confirmation.** Generic external requests are permitted, but anything sending authentication headers or accessing station hardware requires confirmation.
- **Git Commit Convention**: After each task, create a git commit with author set to `<model-name> <antigravity@local>` (e.g. `Gemini 3.8 Flash <antigravity@local>`).
- **CLI & Library Parity**: Whenever adding functionality to the client library (`neovac_client.py`), always expand the CLI parser and subcommands to expose that functionality as well. Use a single canonical name for each command (no command aliases); short option aliases for long flags (e.g. -o, -d, -s, -r) are allowed.

---

## System Architecture

```
                    +---------------------------+
                    |  https://auth.neovac.ch   |
                    |   (IdentityServer4/OIDC)  |
                    +-------------+-------------+
                                  |
                   OAuth2 / PKCE  | Access & Refresh Tokens
                                  v
+------------------+     HTTP REST / SignalR     +-------------------------------+
| neovac_client.py | <-------------------------> |  https://mycharge.neovac.ch   |
| (Python Client)  |                             |   (ASP.NET Core REST API)     |
+------------------+                             +-------------------------------+
        ^                                                        ^
        |                                                        | OCPP 1.6 / 2.0
 Local Auth Config                                               v
(`neovac_auth.json`)                             +-------------------------------+
                                                 |  Physical Charging Stations   |
                                                 |  (e.g. Station / ID <CP_ID>)  |
                                                 +-------------------------------+
```

### Endpoints Overview
- **Authentication Base**: `https://auth.neovac.ch`
  - Token Endpoint: `POST /connect/token`
  - Authorize Endpoint: `GET /connect/authorize`
  - Profile: `GET /profile`
- **Application API Base**: `https://mycharge.neovac.ch`
  - Primary Base Route: `/mobile/api/`
  - Real-time Hubs: `/mobile/hubs/events` and `/mobile/hubs/connector`

### Mobile App Reverse-Engineering Context
Extracted from `neovac.apk` / `neovac.xapk`:
- Assemblies analyzed:
  - `NeoVac.MyCharge.Mobile.dll`: Core mobile application logic, pages, managers, and REST client.
  - `NeoVac.Ocpp.Common.MobileDtos.dll`: DTO models and data contracts for API responses and SignalR payloads.
  - `MauiReactor.dll`: Declarative UI framework.
  - `IdentityModel.OidcClient.dll`: OpenID Connect client implementation for OAuth2 PKCE.
  - `Plugin.NFC.dll`: Hardware RFID badge scanning via smartphone NFC.
- Decompiled internal service structure:
  - `RestBackendService`: Primary REST API wrapper (`/mobile/api/...`).
  - `WebsocketBackendService` / `ConnectorHubService`: Real-time WebSocket / SignalR hub listeners.
  - `ChargingService` / `StopChargingService`: Charging session lifecycle and remote commands.
  - `RfidManager` / `RfidService`: Badge registration, renaming, and removal.
  - `BillingService` / `PricingService`: Invoices and tariff lookups.
  - `ReportService`: Historical energy aggregation and PDF creation.
  - `NotificationSettingsManager`: Push notification tokens and user email report preferences.
  - `GuestUserInvitationManager`: Sharing station access with secondary guests.

---

## Authentication & Tokens

### OAuth2 PKCE Flow
- **Client ID**: `ocpp.interactive`
- **Redirect URI**: `neovacmychargev:///signin-oidc`
- **Scopes**: `openid offline_access email profile`
- **PKCE**: Code verifier (64 URL-safe random bytes), code challenge computed as `Base64URL(SHA256(verifier))`.
- **Authorization URL**:
  `https://auth.neovac.ch/connect/authorize?client_id=ocpp.interactive&response_type=code&scope=openid+offline_access+email+profile&redirect_uri=neovacmychargev:///signin-oidc&code_challenge=<challenge>&code_challenge_method=S256&state=<state>&nonce=<nonce>`

### Token Refresh Flow
- Refresh requests are sent to `POST https://auth.neovac.ch/connect/token`:
  - `client_id=ocpp.interactive`
  - `grant_type=refresh_token`
  - `refresh_token=<stored_refresh_token>`
- Token lifespans:
  - Access token: short-lived (~1 hour).
  - Refresh token: typically 30 days (`expires_in: 2592000`).
- Automatic retry on `401 Unauthorized`:
  - The client catches `401`, triggers `refresh_access_token()`, updates `neovac_auth.json`, and retries the original request once.

### Local Configuration (`neovac_auth.json`)
Structure (untracked, local only):
```json
{
  "access_token": "<jwt>",
  "refresh_token": "<token>",
  "expires_in": 2592000,
  "account_id": "<ACCOUNT_ID>",
  "site_id": "<SITE_ID>",
  "charge_point_id": 0,
  "serial_number": "<SERIAL_NUMBER>",
  "position": "<STATION_NAME>"
}
```
*Note: This file and any file with personal identifiers must strictly remain untracked by Git.*

---

## Domain Models & API Contracts

### 1. User & Station Information
- `GET /mobile/api/user/info`: Returns user profile, account ID, and sites array.
  - `sites[]`: Contains `siteId`, site name, address, and `userChargePoints[]`.
  - `userChargePoints[]`:
    - `chargePointId`: Integer primary ID (e.g. `<CP_ID>`).
    - `neoVacId`: String identifier.
    - `serialNumber`: Station hardware serial string.
    - `position`: Human-readable label (e.g. station name or parking slot).
    - `status`: String state (e.g. `Available`, `Charging`, `SuspendedEVSE`, `SuspendedEV`, `Unavailable`, `Faulted`).
    - `connectorZeroStatus`: Connector state.
    - `isOnline`: Boolean indicating network connectivity.
    - `chargePointSettings`:
      - `permanentCableLockEnabled`: Boolean.
      - `nightChargingEnabled`: Boolean (restricts to 22:00–06:00).
      - `plugAndChargeEnabled`: Boolean (auto-starts on insertion).

### 2. Charging Control Commands
All remote commands are dispatched under `/mobile/api/charge-point/`:
- **Start Charging**: `POST /mobile/api/charge-point/commands/remote-start-transaction`
  - Payload: `{"chargePointId": <CP_ID>, "rfidTag": "<RFID_TAG>"}` (RFID tag optional).
- **Stop Charging**: `POST /mobile/api/charge-point/commands/remote-stop-transaction`
  - Payload: `{"chargePointId": <CP_ID>}`
- **Cable Lock**: `POST /mobile/api/charge-point/{chargePointId}/lock-cable`
  - Payload: `{"enabled": true|false}`
- **Night Charging**: `POST /mobile/api/charge-point/{chargePointId}/night-charging`
  - Payload: `{"enabled": true|false}`
- **Plug & Charge**: `POST /mobile/api/charge-point/{chargePointId}/plug-and-charge`
  - Payload: `{"enabled": true|false}`
- **Rename Station**: `POST /mobile/api/charge-point/{chargePointId}/update-name`
  - Payload: `{"newName": "Custom Name"}`

### 3. RFID Badge Management
- **List RFID Badges**: `GET /mobile/api/user/rfid` -> returns `{"rfids": [{"id": int, "rfidTag": str, "rfidName": str}]}`
- **Register RFID**: `POST /mobile/api/user/rfid` -> payload `{"rfidTag": "<TAG>", "rfidName": "<NAME>"}`
- **Rename RFID**: `PATCH /mobile/api/user/rfid/{id}` -> payload `{"rfidName": "<NAME>"}`
- **Delete RFID**: `DELETE /mobile/api/user/rfid/{id}`

### 4. Reports & Analytics
- **Session History Report**: `GET /mobile/api/charge-point/{id}/report?earliestDate=YYYY-MM-DD&latestDate=YYYY-MM-DD`
  - Returns aggregation of sessions, total kWh, total price (CHF), and individual process records.
- **Site-level Report**: `GET /mobile/api/sites/{siteId}/report?...`
- **Email PDF Report**: `POST /mobile/api/charge-reports/create-charge-point-report`
  - Payload: `{"chargePointId": int, "earliestDate": "YYYY-MM-DD", "latestDate": "YYYY-MM-DD"}`
- **Analytics Curves**: `GET /mobile/api/charge-point/{id}/analytics?earliestDate=...&latestDate=...&resolution=Day|Month|Year`
  - `Day`: Hourly charging power curve in kW (24-hour breakdown).
  - `Month`: Daily energy consumption in kWh.
  - `Year`: Monthly energy consumption in kWh.

### 5. Pricing & Invoices
- **Connector Pricing**: `GET /mobile/api/charge-point/{chargePointId}/pricing-details`
  - Returns `PricingDetailsDto` wrapped under `pricingDetails`: `defaultTariffName`, `defaultTariffPrice`, `tariffTimeDetails`.
  - Client merges `pricingDetails` into the root dictionary so `defaultTariffName` and `defaultTariffPrice` are immediately accessible.
- **List Invoices**: `GET /mobile/api/billing/invoices`
- **Download Invoice PDF**: `GET /mobile/api/billing/invoices/{id}/pdf`

### 6. Additional App Capabilities (Unimplemented in Client CLI)
- **Push Notification Settings**:
  - `GET /mobile/api/user/notification/settings` (`UserNotificationSettingsDto`):
    - `afterEveryChargeReport`: Bool
    - `dailyReport`: Bool
    - `monthlyReport`: Bool
    - `userEmailAddress`: String
  - `POST /mobile/api/user/notification/key/create`: FCM / push registration token.
- **Guest Invitations**:
  - `GET /mobile/api/charge-point/{id}/guest-invitation`
  - `POST /mobile/api/charge-point/{id}/guest-invitation/create`
  - `DELETE /mobile/api/charge-point/{id}/guest-invitation/{invitationId}`
- **User Language**:
  - `PUT /mobile/api/user/communicationlanguage` (`de`, `fr`, `it`, `en`).

---

## Real-Time Telemetry & SignalR Protocol

The backend uses ASP.NET Core SignalR with two distinct hubs:

### 1. Hubs
- **StatusHub**: `https://mycharge.neovac.ch/mobile/hubs/events`
  - Global site updates, connector status, and continuous transaction telemetry across all user stations.
  - Handshake: JSON protocol v1.
  - Subscriptions: `SubscribeToTransactionsFromAllSites` and `SubscribeToStatusFromAllSites`.
  - Target Events:
    - `ReceiveConnectorTransactionUpdate`: Primary live session stream carrying `accumulatedEnergyKwH`, `currentPowerKw`, `maxPowerKw`, `duration`, `start`, `end`, `transactionId`, `chargePointSerialNumber`, and `totalPrice`.
    - `ReceiveChargePointSiteStatusFullUpdate`: Array of connector status items (`ocppStatus` e.g. `SuspendedEV`, `status` e.g. `Connected`, `isOnline`).
    - `ReceiveChargePointSiteStatusUpdate`
    - `ConnectorChanged`
    - `ConnectorChangedPlugAndCharge`
    - `ConnectorChangedPermanentCableLock`
    - `ConnectorChangedNightCharging`
- **ConnectorHub**: `https://mycharge.neovac.ch/mobile/hubs/connector`
  - Specific connector monitoring during active charging transactions.
  - Invocation methods:
    - `JoinConnectorAsync(chargePointId, connectorNumber)`
    - `LeaveConnectorAsync(chargePointId, connectorNumber)`
  - Target Events:
    - `OnTransactionReport`: Real-time session report (`TransactionReportMobileDto`) with power (kW), delivered energy (kWh), duration, and end-of-transaction flags.
    - `StartTransactionNotification` / `StopTransactionNotification`

### 2. Transport Protocols (WebSockets & Long-Polling)
The client supports two interchangeable transport mechanisms:
1. **WebSockets (Native, High-Efficiency)**:
   - When the `websockets` Python package is installed (`websockets>=13.0` / `python3-websockets`), the client connects directly via `wss://mycharge.neovac.ch/mobile/hubs/{hub}?id={connectionToken}&access_token={access_token}` using `websockets.sync.client`.
   - Sends SignalR handshake `{"protocol":"json","version":1}\x1e`.
   - Sends hub subscription (`SubscribeToTransactionsFromAllSites` on `events`, or `JoinConnectorAsync` on `connector`).
   - Receives stream frames in real time and automatically handles SignalR ping/keepalive messages (`type: 6`).
2. **HTTP Long-Polling (Standard Library Fallback)**:
   - Zero-dependency fallback using built-in `urllib`.
   - Uses `POST /mobile/hubs/{hub}?id={connectionToken}` for handshake and subscriptions, then `GET /mobile/hubs/{hub}?id={connectionToken}` in a polling loop.
   - Handles HTTP 204 / 503 keepalive responses.
- **Auto-Detection (`--transport auto`)**:
  - Automatically selects `websocket` if the `websockets` module is installed, falling back to `long-polling` if unavailable.


---

## Tricky Edge Cases & Known Bugs

1. **"Rejected" Command Responses**:
   - `remote-start-transaction` returns HTTP 400/409 with message `"Rejected"` if:
     - The vehicle is not physically plugged in.
     - The station or vehicle is in `SuspendedEV` / `SuspendedEVSE` mode.
     - An active transaction is already ongoing.
     - The station is still finalizing and reporting the previous transaction (takes ~15-30s).
   - In the mobile app, there is an explicit 30-second wait window for `OnTransactionReport` after initiating a start command.

2. **Invoice PDF Endpoint Format**:
   - In the decompiled assemblies, `GET /mobile/api/billing/invoices/{id}/pdf` does **not** return raw binary `application/pdf`.
   - Instead, it returns a JSON object: `{"fileName": "...", "pdfBytes": "<base64_encoded_string>"}`.
   - Any client calling this endpoint must check whether the response is JSON before treating it as raw PDF bytes.

3. **RFID Tag Formatting**:
   - RFID hardware UIDs (from NFC or cards) are hex strings without delimiters.
   - Tags passed via CLI or user input may have colons, dashes, or lowercase letters. Always sanitize by stripping `[-: ]` and converting to uppercase.

4. **SignalR Connection Token vs Connection ID**:
   - In ASP.NET Core SignalR negotiate v1, `connectionToken` must be used as the `id` query parameter for subsequent requests if present, falling back to `connectionId`.

5. **Token Expiry During Background Telemetry**:
   - When streaming telemetry for long durations, access tokens can expire mid-stream, requiring transparent token refresh and re-negotiation.

---

## Home Assistant (HACS) Integration (`custom_components/neovac_mycharge/`)

Alongside `neovac_client.py`, the repository provides a HACS-compatible Home Assistant custom component (`domain: neovac_mycharge`, `iot_class: cloud_push`).

### Component Architecture
- **`api.py` (`NeoVacAsyncClient`)**:
  - Uses Home Assistant's shared `aiohttp.ClientSession` (`async_get_clientsession(hass)`).
  - Handles OAuth2 PKCE generation/exchange, automatic `401` token refresh with a callback to persist rotated tokens into `ConfigEntry.data`, all REST API calls, and `aiohttp` WebSocket SignalR streaming (`async_stream_signalr_hub`).
- **`coordinator.py` (`NeoVacDataUpdateCoordinator`)**:
  - Polls `/user/info`, `/user/rfid`, `/billing/invoices`, `/user/notification/settings`, and per-station `/report`, `/analytics`, `/charge-point/{id}/pricing-details`, and `/guest-invitation`.
  - Runs persistent background tasks for both `StatusHub` (`events`) and `ConnectorHub` (`connector`) with exponential backoff reconnect.
  - Merges real-time SignalR push frames (`OnTransactionReport`, `PriceUpdate`, `ReceiveConnectorTransactionUpdate`, `ReceiveChargePointSiteStatusUpdate`, `ConnectorChanged*`) directly into coordinator data (`async_set_updated_data`).
  - Automatically triggers a REST refresh when `OnTransactionReport` reports `end=True` or `StopTransactionNotification` arrives so 30-day reports and last-session sensors immediately update.
- **State Transition Engine (`evaluate_state_transitions`)**:
  - Translates OCPP state changes and SignalR notifications into semantic lifecycle events emitted on both `event.<station>_charging_event` (`EventEntity`) and the Home Assistant Event Bus (`neovac_mycharge_event` and `neovac_mycharge_<type>`):
    - `cable_plugged_in`, `cable_unplugged`
    - `charging_started`, `charging_stopped`
    - `charging_paused_ev` (`SuspendedEV`), `charging_paused_evse` (`SuspendedEVSE`), `charging_resumed`
    - `price_updated`, `settings_changed`, `error`
- **Platforms**:
  - `sensor.py`: 20 sensors per station (Status, Connector Status, Charging Power, Max Power, Session Energy, Session Cost, Session Duration, Active Transaction ID, Active RFID Badge, Electricity Tariff, 30-Day Energy/Cost/Sessions, Last Session Energy/Cost/End, Monthly Analytics Energy, Registered RFID Badges, Latest Invoice, Guest Invitations).
    - **Strict Data Integrity & Zero Editorial Modification Policy**:
      - **Zero Fake / Editorial Data**: Never fabricate, estimate, or synthesize data that is not provided by the API backend. It is always better to display no data (`None`) than fake data.
      - **Strict Active vs Completed Session Separation**: Active session sensors (`session_energy`, `session_cost`, `session_duration`, `active_rfid_badge`, `active_transaction_id`) read strictly from the active session (`live_transaction`). They never fall back to, pull from, or mix with historical/last session records (`report`).
      - **No Backend Data Dropping or Erasure**: If the backend returns data (such as the user account ID populated in `rfidTag` when no physical RFID card was scanned), the integration never drops or deletes it. Data may be formatted for presentation (e.g. `App (<ID>)`), and raw attributes always expose the exact unmodified backend value.
      - **Charging Power Integrity**: `charging_power` reflects real `currentPowerKw` reported by the API regardless of whether the station status string has transitioned to `"Charging"` (e.g. During `SuspendedEV`, `Preparing`, etc.).
      - **No Coordinator Payload Tampering**: SignalR event handlers never inject synthetic status strings (`cp_info["status"] = STATUS_CHARGING`) when the backend sent only power, nor do they mutate dictionaries with synthetic `currentPowerKw = 0.0`.
      - **No Faked Zero Defaults**: Incomplete or missing entities (such as `last_session_energy`, `last_session_cost`, `monthly_analytics_energy`, `latest_invoice`) return `None` rather than faking `0.0`.
    - `active_rfid_badge`: Strictly reads the active session's `rfidTag` from `live_transaction`. Registered badges are formatted as `Name (TAG)`; if the backend returned the user/account ID (e.g. app remote start / plug-and-charge), it formats it for presentation as `App (ACCOUNT_ID)` rather than dropping the data. Attributes always expose the raw unedited `rfid_tag` returned by the API. Never pulls an RFID badge from past sessions.
    - `electricity_tariff`: Uses `native_unit_of_measurement="CHF/kWh"` and `state_class=SensorStateClass.MEASUREMENT` with `device_class=None` to adhere strictly to Home Assistant core rules (HA forbids `SensorDeviceClass.MONETARY` with compound rate units like `CHF/kWh` and `measurement` state class). Returns `None` if no tariff is reported.
    - `session_duration`: Evaluates active session duration directly from API-provided `duration` if present, or from API start/end timestamps.
    - `session_cost`: Evaluates cost strictly from given `chargePriceInChf` / `totalPrice` in the active transaction without dynamic calculation or fallback to past reports.
    - `session_energy`: Reads live `accumulatedEnergyKwH` or `chargeQuantityInkWh` strictly from `live_transaction`.
    - `report_30d_energy` / `report_30d_cost` / `report_30d_sessions`: Extracts session records from the API's `chargeReport` list (or raw list) and aggregates totals if top-level aggregate fields are omitted.
  - `binary_sensor.py`: 5 binary sensors (Online, Cable Plugged In, Charging, Problem, Maintenance Mode).
  - `switch.py`: 4 switches (Charging Start/Stop [hidden by default via entity_registry_visible_default=False to avoid dashboard clutter with buttons], Permanent Cable Lock, Night Charging Schedule, Plug & Charge).
  - `select.py`: Dynamic RFID badge selector used when remote-starting a charge.
  - `button.py`: Start Charging, Stop Charging, Send Email Report, Refresh Station Data.
  - `text.py`: Station custom display name (`rename_station`).
  - `event.py`: `EventEntity` for charging lifecycle and configuration events.
  - `diagnostics.py`: Redacts tokens, account IDs, site IDs, serial numbers, emails, and RFID tags.

### Real-Time Transport & Polling Architecture
- **Transport**:
  - Uses ASP.NET Core SignalR JSON v1 over permanent **WebSocket** (`wss://api.mycharge.ch/mobile/hubs/events` and `wss://api.mycharge.ch/mobile/hubs/connector`).
  - Active detection of dead TCP connections / internet drops:
    - Transport-level WebSocket heartbeat (`heartbeat=55.0`, `autoping=True`) detects broken TCP sockets within seconds.
    - Application-level SignalR keepalive tracking: sends client pings (`type: 6`) every 55s of idle, and terminates the connection if no message is received within 120s (SignalR keepalive timeout).
- **Reconnection Strategy with Limited Exponential Backoff**:
  - Reconnect loop runs continuously in `_async_hub_loop`:
    - Initial delay: 2.0 seconds.
    - Backoff progression: doubles each failed attempt (2s -> 4s -> 8s -> 16s -> 32s -> 60s max).
    - Limited cap: maximum backoff is capped at 60 seconds (`MAX_BACKOFF = 60.0s`).
    - Random jitter: 0% to 20% random jitter added to sleep times to prevent thundering-herd reconnect spikes across hubs.
    - Stable connection reset: backoff resets to 2.0s and failure counter clears only after the connection has run stably for >= 30 seconds.
    - Automatic token refresh on 401: catches `WSServerHandshakeError` (status 401/403) and auth errors, automatically refreshing access tokens before retrying.
    - State resynchronization: automatically triggers `async_request_refresh()` upon successful reconnection after an outage so Home Assistant entities instantly catch up with missed charging state.
    - Auto-healing task manager: `async_start_websocket_listeners` prunes dead/finished tasks and restarts any missing hub listeners.
  - CLI parity (`neovac_client.py`): `stream_telemetry` reconnects with limited exponential backoff (capped at 30s) for the duration of the stream, and falls back to long-polling if WebSocket fails under `transport="auto"`.
- **REST Polling**:
  - `DataUpdateCoordinator` runs on a 900-second interval (`DEFAULT_SCAN_INTERVAL = 900`, 15 minutes) to refresh account-wide metadata, 30-day reports, monthly analytics, RFIDs, and invoices.
  - Live charging status, power, energy, and hardware settings update instantly upon arrival of SignalR messages.
- **Error Handling & Logging**:
  - Errors, malformed JSON frames, SignalR disconnects, authentication refreshes, and API failures are never swallowed; all exceptions and unexpected payloads are logged as `_LOGGER.warning` or `_LOGGER.error` to appear in Home Assistant logs.
  - **Failed Fetch -> Entity Unavailable (Item 1)**: When sub-resource queries fail during coordinator refresh (`async_get_charge_report`, `async_get_analytics`, `async_get_pricing`, `async_get_guest_invitations`, `async_list_rfids`, `async_list_invoices`), the coordinator assigns `None` rather than falling back to `{}` or `[]`. Sensors for 30-day reports, last session, analytics, tariff, invoices, RFID badges, and guest invitations evaluate `available_fn` and transition to `Unavailable` in Home Assistant so users are not misled by false `0 kWh` or `0 CHF` values. `NeoVacRfidSelectEntity` similarly marks itself `Unavailable` if RFID badge retrieval fails.
  - **WebSocket Reconnect Permanent Auth Failure -> Reauth Trigger (Item 2)**: In `_async_hub_loop`, if `async_refresh_access_token()` fails with `NeoVacAuthError` (e.g. Refresh token expired or revoked), the reconnect loop stops and invokes Home Assistant's `entry.async_start_reauth(hass)`.
  - **REST API Timeout & Connection Error Wrapping (Item 3)**: REST requests (`_request`, `async_refresh_access_token`, `async_exchange_code`) enforce a 30.0s client timeout (`DEFAULT_REQUEST_TIMEOUT = 30.0s`), and catch `aiohttp.ClientError`, `asyncio.TimeoutError`, and `OSError` to raise clean `NeoVacConnectionError` instances.
  - **CLI Stream Telemetry Error Logging & Fast Failure (Item 7)**: `stream_telemetry` logs connection drops and retry delays to `sys.stderr`, and re-raises unrecoverable errors (missing `websockets` dependency when explicitly requested, HTTP 401/403 auth errors) immediately instead of spinning in retry loops.
  - **CLI Long-Polling Robust Error Backoff (Item 8)**: `_stream_telemetry_long_polling` raises `NeoVacAPIError` on HTTP 401/403 and applies exponential backoff with `sys.stderr` logging on transient HTTP/network glitches.
  - **CLI Token Refresh Error Transparency (Item 9)**: `_request` in `neovac_client.py` catches failed token refreshes on 401 and raises a human-friendly `NeoVacAPIError` stating that the session has expired and re-login via `python3 neovac_client.py login` is required.
  - **CLI Station Auto-Discovery Failure Warning (Item 10)**: `complete_login` in `neovac_client.py` warns to `sys.stderr` if account/station discovery fails after code exchange instead of silently ignoring it.


