"""DataUpdateCoordinator for the ComAp AMF 25 integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    ComapAmf25AuthError,
    ComapAmf25Client,
    ComapAmf25ConnectionError,
    ComapAmf25Data,
    ComapAmf25SetpointsData,
)
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SETPOINTS_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SETPOINTS_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class ComapAmf25Coordinator(DataUpdateCoordinator[ComapAmf25Data]):
    """Polls the panel on an interval and hands parsed data to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Two problems with using Home Assistant's shared client session
        # (async_create_clientsession) for this panel specifically:
        #
        # 1. It's addressed by bare IP, and aiohttp's default cookie jar
        #    silently drops cookies from IP-address hosts (a built-in
        #    safety default) - this panel needs its session cookie to
        #    work, so an "unsafe" jar is required regardless.
        #
        # 2. Home Assistant's shared session pools and reuses connections
        #    (keep-alive) across *every* integration in the whole system.
        #    This panel's embedded web server appears to be a primitive
        #    one - the original browser capture this integration was
        #    built from shows a real browser opening a fresh TCP
        #    connection for every single request, never reusing one -
        #    strongly suggesting it doesn't handle connection reuse
        #    gracefully. Sharing HA's pooled connector risks a stale,
        #    already-dropped connection being reused for a request,
        #    especially right at HA startup when connection churn across
        #    the shared pool is highest (many integrations initializing
        #    at once) - which matches "fails for a few minutes right
        #    after a Home Assistant restart, then resolves on its own."
        #
        # So this gets its own private session with keep-alive disabled
        # (force_close=True - every request opens a genuinely fresh
        # connection, matching what a real browser does against this
        # panel and what config_flow's own validation session already
        # does successfully).
        session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            connector=aiohttp.TCPConnector(force_close=True),
        )
        self.session = session
        self.client = ComapAmf25Client(
            session,
            entry.data[CONF_HOST],
            entry.data[CONF_PASSWORD],
        )
        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> ComapAmf25Data:
        try:
            data = await self.client.async_fetch()
            data.engine_values, data.engine_binary = (
                await self.client.async_fetch_engine_values()
            )
            data.generator_values, data.generator_binary = (
                await self.client.async_fetch_generator_values()
            )
            data.mains_values, data.mains_binary = (
                await self.client.async_fetch_mains_values()
            )
            data.controller_io_values, data.controller_io_binary = (
                await self.client.async_fetch_controller_io_values()
            )
            data.extension_io_values, data.extension_io_binary = (
                await self.client.async_fetch_extension_io_values()
            )
            data.statistics_values, data.statistics_binary = (
                await self.client.async_fetch_statistics_values()
            )
            data.il_info_values, data.il_info_binary = (
                await self.client.async_fetch_il_info_values()
            )
            return data
        except ComapAmf25AuthError as err:
            raise UpdateFailed(f"Login rejected: {err}") from err
        except ComapAmf25ConnectionError as err:
            raise UpdateFailed(f"Could not reach panel: {err}") from err


class ComapAmf25SetpointsCoordinator(DataUpdateCoordinator[ComapAmf25SetpointsData]):
    """Polls the read-only Setpoints pages, History, and the panel's own
    Date/Time Measurement group on their own, much slower cycle.

    Date/Time lives here rather than on the fast coordinator because
    "Time" changes on essentially every poll by definition - polling it
    every ~10s floods Home Assistant's history/logbook for a value
    that's only useful as an occasional "has the panel's clock
    drifted?" check, not something worth tracking in near-real-time.

    Shares the main coordinator's already-authenticated client rather
    than opening a second session, since these are just more pages
    behind the same login.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ComapAmf25Client
    ) -> None:
        self.client = client
        scan_interval = entry.options.get(
            CONF_SETPOINTS_SCAN_INTERVAL,
            entry.data.get(
                CONF_SETPOINTS_SCAN_INTERVAL, DEFAULT_SETPOINTS_SCAN_INTERVAL
            ),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_setpoints",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> ComapAmf25SetpointsData:
        try:
            data = ComapAmf25SetpointsData()
            data.groups["basic"] = await self.client.async_fetch_setpoints_basic()
            data.groups["engine_params"] = (
                await self.client.async_fetch_setpoints_engine_params()
            )
            data.groups["engine_protect"] = (
                await self.client.async_fetch_setpoints_engine_protect()
            )
            data.groups["gener_protect"] = (
                await self.client.async_fetch_setpoints_gener_protect()
            )
            data.groups["amf_settings"] = (
                await self.client.async_fetch_setpoints_amf_settings()
            )
            data.groups["extension_io"] = (
                await self.client.async_fetch_setpoints_extension_io()
            )
            data.groups["date_time"] = (
                await self.client.async_fetch_setpoints_date_time()
            )
            data.groups["sensors_spec"] = (
                await self.client.async_fetch_setpoints_sensors_spec()
            )
            data.groups["sms_email"] = (
                await self.client.async_fetch_setpoints_sms_email()
            )
            data.history = await self.client.async_fetch_history()
            data.date_time_values, data.date_time_binary = (
                await self.client.async_fetch_date_time_values()
            )
            return data
        except ComapAmf25AuthError as err:
            raise UpdateFailed(f"Login rejected: {err}") from err
        except ComapAmf25ConnectionError as err:
            raise UpdateFailed(f"Could not reach panel: {err}") from err

