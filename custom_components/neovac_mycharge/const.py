"""Constants for the NeoVac myCharge V Home Assistant integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "neovac_mycharge"
MANUFACTURER = "NeoVac"
MODEL = "myCharge V"

AUTH_BASE = "https://auth.neovac.ch"
API_BASE = "https://mycharge.neovac.ch"
CLIENT_ID = "ocpp.interactive"
REDIRECT_URI = "neovacmychargev:///signin-oidc"
DEFAULT_SCOPES = "openid offline_access email profile"

# Configuration keys
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCOUNT_ID = "account_id"
CONF_SITE_ID = "site_id"
CONF_CHARGE_POINT_ID = "charge_point_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ENABLE_WEBSOCKET = "enable_websocket"
CONF_AUTH_METHOD = "auth_method"
CONF_REDIRECT_URL = "redirect_url"

AUTH_METHOD_PKCE = "pkce"
AUTH_METHOD_TOKEN = "token"

DEFAULT_SCAN_INTERVAL = 900  # seconds (15 minutes)
DEFAULT_REPORT_DAYS = 30
UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

# SignalR Hubs
HUB_EVENTS = "events"
HUB_CONNECTOR = "connector"
RECORD_SEPARATOR = "\x1e"

# OCPP & Station Status Values
STATUS_AVAILABLE = "Available"
STATUS_PREPARING = "Preparing"
STATUS_CHARGING = "Charging"
STATUS_SUSPENDED_EV = "SuspendedEV"
STATUS_SUSPENDED_EVSE = "SuspendedEVSE"
STATUS_FINISHING = "Finishing"
STATUS_RESERVED = "Reserved"
STATUS_UNAVAILABLE = "Unavailable"
STATUS_FAULTED = "Faulted"
STATUS_UNKNOWN = "Unknown"

ALL_STATION_STATUSES = [
    STATUS_AVAILABLE,
    STATUS_PREPARING,
    STATUS_CHARGING,
    STATUS_SUSPENDED_EV,
    STATUS_SUSPENDED_EVSE,
    STATUS_FINISHING,
    STATUS_RESERVED,
    STATUS_UNAVAILABLE,
    STATUS_FAULTED,
    STATUS_UNKNOWN,
]

PLUGGED_IN_STATUSES = {
    STATUS_PREPARING,
    STATUS_CHARGING,
    STATUS_SUSPENDED_EV,
    STATUS_SUSPENDED_EVSE,
    STATUS_FINISHING,
}

PAUSED_STATUSES = {
    STATUS_SUSPENDED_EV,
    STATUS_SUSPENDED_EVSE,
}

# Event Bus & EventEntity Types
EVENT_BUS_NAME = f"{DOMAIN}_event"

EVENT_CHARGING_STARTED = "charging_started"
EVENT_CHARGING_STOPPED = "charging_stopped"
EVENT_CHARGING_PAUSED_EV = "charging_paused_ev"
EVENT_CHARGING_PAUSED_EVSE = "charging_paused_evse"
EVENT_CHARGING_RESUMED = "charging_resumed"
EVENT_CABLE_PLUGGED_IN = "cable_plugged_in"
EVENT_CABLE_UNPLUGGED = "cable_unplugged"
EVENT_TRANSACTION_REPORT = "transaction_report"
EVENT_PRICE_UPDATED = "price_updated"
EVENT_SETTINGS_CHANGED = "settings_changed"
EVENT_ERROR = "error"

ALL_CHARGING_EVENT_TYPES = [
    EVENT_CHARGING_STARTED,
    EVENT_CHARGING_STOPPED,
    EVENT_CHARGING_PAUSED_EV,
    EVENT_CHARGING_PAUSED_EVSE,
    EVENT_CHARGING_RESUMED,
    EVENT_CABLE_PLUGGED_IN,
    EVENT_CABLE_UNPLUGGED,
    EVENT_TRANSACTION_REPORT,
    EVENT_PRICE_UPDATED,
    EVENT_SETTINGS_CHANGED,
    EVENT_ERROR,
]

# Custom Domain Services
SERVICE_START_CHARGING = "start_charging"
SERVICE_STOP_CHARGING = "stop_charging"
SERVICE_ADD_RFID = "add_rfid"
SERVICE_RENAME_RFID = "rename_rfid"
SERVICE_DELETE_RFID = "delete_rfid"
SERVICE_REQUEST_EMAIL_REPORT = "request_email_report"
SERVICE_DOWNLOAD_INVOICE_PDF = "download_invoice_pdf"

# Default RFID selector option
DEFAULT_RFID_OPTION = "Default (Account)"
