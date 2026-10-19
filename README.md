# NeoVac myCharge V — Home Assistant Integration & Python Client

A complete Home Assistant custom integration (HACS-compatible) and standalone Python CLI client for monitoring, automating, and controlling **NeoVac myCharge V** EV charging stations.

---

## Features

- **Real-Time SignalR WebSocket Push (`cloud_push`)**:
  - Connects to both `StatusHub` (`/mobile/hubs/events`) and `ConnectorHub` (`/mobile/hubs/connector`) for sub-second updates of charging power (`kW`), delivered session energy (`kWh`), live session cost (`CHF`), elapsed duration, and connector state transitions.
- **Complete Station Control**:
  - Start and stop charging sessions (with optional RFID badge selection).
  - Configure hardware settings: **Permanent Cable Lock**, **Night Charging Schedule (22:00–06:00)**, **Plug & Charge**, and **Station Display Name**.
- **Energy Dashboard & Financial Analytics**:
  - Exposes live session energy (`total_increasing`), 30-day energy/cost totals, most recent session telemetry, monthly analytics breakdowns, active electricity tariffs (`CHF/kWh` + tariff schedule), and billing invoices.
- **Rich Lifecycle Events**:
  - Provides both a dedicated `event.<station>_charging_event` entity and Home Assistant Event Bus events (`neovac_mycharge_event` / `neovac_mycharge_<type>`) for `charging_started`, `charging_stopped`, `charging_paused_ev`, `charging_paused_evse`, `charging_resumed`, `cable_plugged_in`, `cable_unplugged`, `price_updated`, `settings_changed`, and `error`.
- **RFID Badge Management**:
  - Select which RFID badge authorizes remote-started sessions via a `select` entity, and register, rename, or remove badges via Home Assistant services or the CLI.

---

## Home Assistant Installation (HACS)

1. Open **HACS** in Home Assistant $\rightarrow$ **Integrations** $\rightarrow$ top-right menu ($\vdots$) $\rightarrow$ **Custom repositories**.
2. Add the repository URL and select category **Integration**.
3. Click **Download** on **NeoVac myCharge V** and restart Home Assistant.
4. Go to **Settings** $\rightarrow$ **Devices & Services** $\rightarrow$ **Add Integration** $\rightarrow$ search for **NeoVac myCharge V**.

### Authentication Options
During setup, you can choose between:
1. **OAuth2 Browser Login (PKCE)**: Open the displayed `https://auth.neovac.ch/connect/authorize?...` link in your browser, sign in, and paste the resulting `neovacmychargev:///signin-oidc?code=...` redirect URL (or authorization code).
2. **Existing Refresh Token**: Paste an existing `refresh_token` directly.

---

## Exposed Entities per Charging Station

### Sensors (`sensor`)
| Entity | Unit | Device Class | Description |
| :--- | :--- | :--- | :--- |
| **Station Status** | — | `enum` | `Available`, `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`, `Reserved`, `Unavailable`, `Faulted` |
| **Connector Status** | — | `enum` | Live OCPP connector status |
| **Charging Power** | `kW` | `power` | Real-time charging power via `ConnectorHub` (`0.0` when idle) |
| **Max Offered Power** | `kW` | `power` | Maximum power offered by the station |
| **Session Energy** | `kWh` | `energy` | Energy delivered in the current session (`total_increasing`, Energy Dashboard compatible) |
| **Session Cost** | `CHF` | `monetary` | Running cost of the active session |
| **Session Duration** | `s` | `duration` | Elapsed time of the active session |
| **Electricity Tariff** | `CHF/kWh` | `monetary` | Active tariff price; attributes include `default_tariff_name` and `tariff_schedule` |
| **Active RFID Badge** | — | — | RFID badge name & tag used for the active or most recent charge |
| **Active Transaction ID** | — | — | Active OCPP transaction ID (Diagnostic) |
| **30-Day Energy** | `kWh` | `energy` | Total energy charged over the last 30 days |
| **30-Day Cost** | `CHF` | `monetary` | Total charging cost over the last 30 days |
| **30-Day Sessions** | `sessions` | — | Number of charging sessions in the last 30 days |
| **Last Session Energy** | `kWh` | `energy` | Energy of the most recently completed session |
| **Last Session Cost** | `CHF` | `monetary` | Cost of the most recently completed session |
| **Last Session End** | — | `timestamp` | Completion timestamp of the last session |
| **Monthly Analytics Energy** | `kWh` | `energy` | Monthly analytics curve total & series data |
| **Registered RFID Badges** | `badges` | — | Count of registered badges; attributes list `{id, name, tag}` |
| **Latest Invoice** | `CHF` | `monetary` | Amount of the latest invoice; attributes include invoice metadata |
| **Guest Invitations** | `invitations` | — | Count & details of active guest invitations |

### Binary Sensors (`binary_sensor`)
- **Online** (`connectivity`): Station network connectivity to NeoVac backend.
- **Cable Plugged In** (`plug`): `on` when a vehicle cable is connected (`Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`).
- **Charging** (`battery_charging`): `on` when actively delivering power.
- **Problem** (`problem`): `on` when faulted or reporting an error code.
- **Maintenance Mode** (`diagnostic`): `on` if station is in maintenance mode.

### Controls (`switch`, `select`, `button`, `text`)
- **Charging** (`switch`): Start or stop charging (uses the badge selected in **Remote Start RFID Badge**).
- **Permanent Cable Lock** (`switch`): Lock the charging cable permanently to the station.
- **Night Charging Schedule** (`switch`): Restrict charging to the 22:00–06:00 tariff window.
- **Plug & Charge** (`switch`): Automatically start charging as soon as a cable is plugged in.
- **Remote Start RFID Badge** (`select`): Choose `"Default (Account)"` or any registered RFID badge for remote-started sessions.
- **Start Charging / Stop Charging** (`button`): Dedicated action buttons.
- **Send Email Report (30 Days)** (`button`): Emails a PDF report of the last 30 days.
- **Refresh Station Data** (`button`): Forces an immediate REST coordinator refresh.
- **Station Name** (`text`): View or rename the station's display name.

---

## Events & Automations

State transitions are emitted on both the **`event.<station>_charging_event`** entity and the Home Assistant Event Bus (`neovac_mycharge_event` and `neovac_mycharge_<event_type>`):

- `charging_started`
- `charging_stopped`
- `charging_paused_ev` (paused by vehicle, e.g. battery full)
- `charging_paused_evse` (paused by station, e.g. load management or night schedule)
- `charging_resumed`
- `cable_plugged_in`
- `cable_unplugged`
- `transaction_report` (live telemetry update)
- `price_updated`
- `settings_changed`
- `error`

### Example Automation
```yaml
automation:
  - alias: "Notify when EV finishes or pauses charging"
    trigger:
      - platform: event
        event_type: neovac_mycharge_charging_stopped
      - platform: event
        event_type: neovac_mycharge_charging_paused_ev
    action:
      - service: notify.mobile_app
        data:
          title: "EV Charging Update"
          message: >
            Station {{ trigger.event.data.charge_point_id }} event:
            {{ trigger.event.data.type }} ({{ trigger.event.data.new_status }})
```

---

## Custom Services

- `neovac_mycharge.start_charging`: Start charging (`charge_point_id`, optional `rfid_tag`).
- `neovac_mycharge.stop_charging`: Stop charging (`charge_point_id`).
- `neovac_mycharge.add_rfid`: Register a new RFID badge (`rfid_tag`, `rfid_name`).
- `neovac_mycharge.rename_rfid`: Rename an existing RFID badge (`rfid_id`, `rfid_name`).
- `neovac_mycharge.delete_rfid`: Delete an RFID badge (`rfid_id`).
- `neovac_mycharge.request_email_report`: Request a PDF report by email (`charge_point_id`, `days`, `earliest_date`, `latest_date`).
- `neovac_mycharge.download_invoice_pdf`: Download an invoice PDF to disk (`invoice_id`, `target_path`).

---

## Standalone Python CLI (`neovac_client.py`)

The repository also includes a zero-dependency standalone CLI client (`neovac_client.py`):

```bash
# Interactive OAuth2 PKCE Login
python3 neovac_client.py login

# Station status
python3 neovac_client.py status

# Start / stop charging
python3 neovac_client.py start --rfid 01020304050607
python3 neovac_client.py stop

# Configure station settings
python3 neovac_client.py cable-lock on
python3 neovac_client.py night-charging off
python3 neovac_client.py plug-and-charge on
python3 neovac_client.py rename "Station 1"

# Manage RFID badges
python3 neovac_client.py rfid list
python3 neovac_client.py rfid add 01020304050607 "Primary Badge"

# Stream real-time SignalR telemetry (WebSocket or Long-Polling)
python3 neovac_client.py telemetry --hub connector --transport auto --duration 60

# Electricity tariffs & pricing
python3 neovac_client.py pricing

# Invoices & billing PDF download
python3 neovac_client.py invoices list
python3 neovac_client.py invoices download <invoice_id> --output invoice.pdf

# View account profile, guest invitations, and notifications
python3 neovac_client.py account
python3 neovac_client.py guests
python3 neovac_client.py notifications

# Charging reports and energy analytics
python3 neovac_client.py report --days 30
python3 neovac_client.py report --site <site_id> --days 30
python3 neovac_client.py analytics --resolution Day --days 7
```
