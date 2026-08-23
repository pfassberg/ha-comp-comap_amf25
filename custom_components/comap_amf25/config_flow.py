"""Config flow for ComAp AMF 25."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .api import ComapAmf25AuthError, ComapAmf25Client, ComapAmf25ConnectionError
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SETPOINTS_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SETPOINTS_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MAX_SETPOINTS_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_SETPOINTS_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PASSWORD, default="0"): str,
    }
)


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Try to log in with the given host/password, raising on failure.

    Uses its own short-lived, non-shared session with an "unsafe" cookie
    jar and keep-alive disabled - see the note on this in coordinator.py
    for why: an "unsafe" jar because the panel is addressed by bare IP
    and aiohttp's default cookie jar drops cookies from IP hosts, and
    force_close because the panel's embedded web server doesn't seem to
    handle connection reuse gracefully.
    """
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        connector=aiohttp.TCPConnector(force_close=True),
    ) as session:
        client = ComapAmf25Client(session, data[CONF_HOST], data[CONF_PASSWORD])
        await client.async_login()


class ComapAmf25ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ComAp AMF 25."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()

            try:
                await _validate(self.hass, user_input)
            except ComapAmf25AuthError:
                errors["base"] = "invalid_auth"
            except ComapAmf25ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error validating ComAp AMF 25 panel")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"ComAp AMF 25 ({user_input[CONF_HOST]})", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ComapAmf25OptionsFlow:
        return ComapAmf25OptionsFlow()


class ComapAmf25OptionsFlow(config_entries.OptionsFlow):
    """Lets you change the polling intervals after setup, without
    re-adding the integration.

    Two separate intervals: the main Scada+Measurement cycle (9
    requests as of this writing) and the much-less-frequently-changing
    Setpoints pages, which poll on their own slower cycle by design -
    see ComapAmf25SetpointsCoordinator.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        current_setpoints = self.config_entry.options.get(
            CONF_SETPOINTS_SCAN_INTERVAL,
            self.config_entry.data.get(
                CONF_SETPOINTS_SCAN_INTERVAL, DEFAULT_SETPOINTS_SCAN_INTERVAL
            ),
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Required(
                    CONF_SETPOINTS_SCAN_INTERVAL, default=current_setpoints
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_SETPOINTS_SCAN_INTERVAL,
                        max=MAX_SETPOINTS_SCAN_INTERVAL,
                    ),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

