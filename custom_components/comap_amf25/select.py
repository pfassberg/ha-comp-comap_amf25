"""Mode select entity (OFF / MAN / AUT / TEST) for the ComAp AMF 25."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_TO_INDEX, MODES
from .coordinator import ComapAmf25Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the mode select entity."""
    coordinator: ComapAmf25Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ComapAmf25ModeSelect(coordinator, entry)])


class ComapAmf25ModeSelect(CoordinatorEntity[ComapAmf25Coordinator], SelectEntity):
    """Lets you switch the controller between OFF / MAN / AUT / TEST.

    This mirrors the four buttons at the bottom of the Scada page
    (links "0.MOD".."3.MOD"). Changing it sends the same GET request
    the panel's own web GUI sends when you click one of them.
    """

    _attr_has_entity_name = True
    _attr_name = "Mode"
    _attr_options = list(MODES.values())

    def __init__(self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info(entry)
        self._attr_unique_id = f"{entry.entry_id}_mode"

    @property
    def current_option(self) -> str | None:
        return self.coordinator.data.mode

    async def async_select_option(self, option: str) -> None:
        index = MODE_TO_INDEX[option]
        _LOGGER.debug("Setting ComAp AMF 25 mode to %s (%s.MOD)", option, index)
        await self.coordinator.client.async_send_command(f"{index}.MOD")
        await self.coordinator.async_request_refresh()
