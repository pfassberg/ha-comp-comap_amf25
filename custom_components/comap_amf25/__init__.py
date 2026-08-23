"""The ComAp AMF 25 genset controller integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ComapAmf25Coordinator, ComapAmf25SetpointsCoordinator

PLATFORMS = ["sensor", "binary_sensor", "select", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ComAp AMF 25 from a config entry."""
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
    # which handles that automatically.
    entry.async_on_unload(coordinator.session.close)
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
