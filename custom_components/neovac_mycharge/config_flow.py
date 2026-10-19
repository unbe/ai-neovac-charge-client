"""Config flow for the NeoVac myCharge V integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NeoVacApiError, NeoVacAsyncClient, NeoVacAuthError
from .const import (
    AUTH_METHOD_PKCE,
    AUTH_METHOD_TOKEN,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_AUTH_METHOD,
    CONF_ENABLE_WEBSOCKET,
    CONF_REDIRECT_URL,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import _get_ci

_LOGGER = logging.getLogger(__name__)


class NeoVacConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NeoVac myCharge V."""

    VERSION = 1

    def __init__(self) -> None:
        self._pkce_verifier: str | None = None
        self._auth_url: str | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NeoVacOptionsFlowHandler:
        """Get the options flow for this handler."""
        return NeoVacOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Initial step: choose between OAuth2 PKCE browser login or Refresh Token."""
        if user_input is not None:
            method = user_input.get(CONF_AUTH_METHOD, AUTH_METHOD_PKCE)
            if method == AUTH_METHOD_TOKEN:
                return await self.async_step_token()
            return await self.async_step_pkce()

        schema = vol.Schema(
            {
                vol.Required(CONF_AUTH_METHOD, default=AUTH_METHOD_PKCE): vol.In(
                    {
                        AUTH_METHOD_PKCE: "OAuth2 Browser Login (PKCE Redirect URL / Code)",
                        AUTH_METHOD_TOKEN: "Existing Refresh Token",
                    }
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_pkce(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2a: Perform OAuth2 PKCE authorization."""
        errors: dict[str, str] = {}
        session = async_get_clientsession(self.hass)
        client = NeoVacAsyncClient(session=session)

        if not self._pkce_verifier or not self._auth_url:
            verifier, challenge = client.generate_pkce_pair()
            self._pkce_verifier = verifier
            self._auth_url = client.build_authorize_url(challenge)

        if user_input is not None:
            redirect_val = user_input.get(CONF_REDIRECT_URL, "").strip()
            if not redirect_val:
                errors["base"] = "invalid_code"
            else:
                try:
                    await client.async_exchange_code(redirect_val, self._pkce_verifier)
                    return await self._async_validate_and_create(client)
                except NeoVacAuthError:
                    errors["base"] = "invalid_auth"
                except (NeoVacApiError, Exception) as err:
                    _LOGGER.exception("Unexpected error during PKCE exchange: %s", err)
                    errors["base"] = "cannot_connect"

        schema = vol.Schema({vol.Required(CONF_REDIRECT_URL): str})
        return self.async_show_form(
            step_id="pkce",
            data_schema=schema,
            errors=errors,
            description_placeholders={"auth_url": self._auth_url or ""},
        )

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2b: Authenticate using an existing refresh token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            refresh_token = user_input.get(CONF_REFRESH_TOKEN, "").strip()
            access_token = user_input.get(CONF_ACCESS_TOKEN, "").strip()
            session = async_get_clientsession(self.hass)
            client = NeoVacAsyncClient(
                session=session,
                access_token=access_token,
                refresh_token=refresh_token,
            )
            try:
                if refresh_token and not access_token:
                    await client.async_refresh_access_token()
                return await self._async_validate_and_create(client)
            except NeoVacAuthError:
                errors["base"] = "invalid_auth"
            except (NeoVacApiError, Exception) as err:
                _LOGGER.exception("Unexpected error during token validation: %s", err)
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_REFRESH_TOKEN): str,
                vol.Optional(CONF_ACCESS_TOKEN, default=""): str,
            }
        )
        return self.async_show_form(step_id="token", data_schema=schema, errors=errors)

    async def _async_validate_and_create(
        self, client: NeoVacAsyncClient
    ) -> FlowResult:
        """Validates account info and creates or updates the config entry."""
        user_info = await client.async_get_user_info()
        account_id = str(_get_ci(user_info, "accountId", default="neovac_account"))

        data = {
            CONF_ACCESS_TOKEN: client.access_token,
            CONF_REFRESH_TOKEN: client.refresh_token,
            CONF_ACCOUNT_ID: account_id,
        }

        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry, data={**self._reauth_entry.data, **data}
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        await self.async_set_unique_id(account_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"NeoVac myCharge V ({account_id})",
            data=data,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """Handle re-authentication when tokens expire."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm re-authentication."""
        if user_input is not None:
            return await self.async_step_user(user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_AUTH_METHOD, default=AUTH_METHOD_PKCE): vol.In(
                    {
                        AUTH_METHOD_PKCE: "OAuth2 Browser Login (PKCE Redirect URL / Code)",
                        AUTH_METHOD_TOKEN: "Existing Refresh Token",
                    }
                )
            }
        )
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema)


class NeoVacOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for NeoVac myCharge V."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self._config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        current_ws = self._config_entry.options.get(
            CONF_ENABLE_WEBSOCKET,
            self._config_entry.data.get(CONF_ENABLE_WEBSOCKET, True),
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=int(current_interval)): vol.All(
                    vol.Coerce(int), vol.Range(min=15, max=3600)
                ),
                vol.Required(CONF_ENABLE_WEBSOCKET, default=bool(current_ws)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
