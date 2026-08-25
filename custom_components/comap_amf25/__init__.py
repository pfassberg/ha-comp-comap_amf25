"""The ComAp AMF 25 genset controller integration."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, HomeAssistant

from .const import DOMAIN
from .coordinator import ComapAmf25Coordinator, ComapAmf25SetpointsCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "select", "button"]

# How long to wait before the very first login attempt, but only when
# Home Assistant is still in its own startup phase (hass.state ==
# CoreState.starting) - not on a later manual reload of this entry.
#
# This integration has repeatedly failed its first login attempt right
# at cold boot with a garbled response from the panel, self-resolving
# after a few of Home Assistant's own automatic retries. Several
# distinct, confirmed code-level bugs on our side have already been
# found and fixed this way (a cookie-jar issue, a race between two
# coordinators, connection pooling against a session shared with every
# other integration, and a leaked session on failed setup) - but the
# exact same failure still happens on a clean first attempt after all
# of those fixes. That points to something outside this integration's
# code entirely: most likely the host's own network stack not yet
# being fully routable to the local LAN in the first few seconds after
# boot, which a short, startup-only delay is a reasonable mitigation
# for even without a fully confirmed root cause.
_STARTUP_GRACE_PERIOD_SECONDS = 15
# hass.state stays CoreState.starting across Home Assistant's own
# automatic retries too, not just the very first attempt - this flag
# keeps the delay to once per boot rather than stacking it onto every
# retry within the startup window.
_STARTUP_DELAY_APPLIED_KEY = f"{DOMAIN}_startup_delay_applied"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ComAp AMF 25 from a config entry."""
    if hass.state == CoreState.starting and not hass.data.get(
        _STARTUP_DELAY_APPLIED_KEY
    ):
        hass.data[_STARTUP_DELAY_APPLIED_KEY] = True
        _LOGGER.debug(
            "Home Assistant is still starting - waiting %s seconds before the "
            "first login attempt to let the network stack settle",
            _STARTUP_GRACE_PERIOD_SECONDS,
        )
        await asyncio.sleep(_STARTUP_GRACE_PERIOD_SECONDS)

    coordinator = ComapAmf25Coordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()

        # Setpoints share the main coordinator's already-logged-in client but
        # poll on their own, much slower cycle - see ComapAmf25SetpointsCoordinator.
        coordinator.setpoints = ComapAmf25SetpointsCoordinator(
            hass, entry, coordinator.client
        )
        await coordinator.setpoints.async_config_entry_first_refresh()
    except Exception:
        # If either first refresh fails (e.g. the panel is briefly
        # unreachable - the same condition that triggers a retry), this
        # coordinator's private session would otherwise never get
        # registered for cleanup, since that only happens below on
        # success. Every failed attempt would leak a session holding an
        # open connection to the panel - and a pile of those building up
        # across retries is a very plausible way to confuse a small
        # embedded device's own connection handling on the next attempt.
        await coordinator.session.close()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # We own this session directly now (not HA's shared one - see the
    # comment in coordinator.py for why), so we're responsible for
    # closing it, unlike a session from async_create_clientsession
    # which handles that automatically. Registered *before* the logout
    # hooks below: entry.async_on_unload runs its callbacks in reverse
    # (LIFO) order, so registering this first means it actually runs
    # *last* - after logout has had a chance to use the still-open
    # session, not after it's already been closed out from under it.
    entry.async_on_unload(coordinator.session.close)

    async def _async_logout(*_args: object) -> None:
        # Packet capture confirmed the panel enforces a small limit on
        # concurrent logged-in clients, and returns "Too many other
        # clients connected" instead of the login page once that limit
        # is hit - which is exactly what happens on the next login
        # attempt if the previous session was never explicitly ended.
        # A Home Assistant restart closes the TCP connection but never
        # tells the panel we're actually done, so without this the
        # panel holds our slot until its own internal timeout clears
        # it - which is what's behind the "takes a few retries to come
        # up" pattern this integration has had since the beginning.
        await coordinator.client.async_logout()

    # A plain restart never calls async_unload_entry at all (config
    # entries stay "loaded" across a restart - that's why they resume
    # automatically without reconfiguring) - it only fires
    # EVENT_HOMEASSISTANT_STOP, so that's the hook that actually
    # matters for logging out before a restart specifically.
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_logout)
    )
    # Also log out on a plain entry unload/reload (e.g. changing the
    # poll interval, or removing the integration) - a separate, rarer
    # case from a full Home Assistant restart, but worth the same
    # courtesy to the panel.
    entry.async_on_unload(_async_logout)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change (e.g. a new poll interval)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
