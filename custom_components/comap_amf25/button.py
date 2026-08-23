"""Command buttons (start/stop/breakers/reset alarms) for the ComAp AMF 25.

These map directly to the icon buttons on the Scada page. The panel
exposes them as single toggle-style links (e.g. clicking "mcb.CMD"
again closes the mains breaker if it was open, and vice versa) rather
than separate open/close commands, so that's what's reproduced here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CMD_GEN_BREAKER,
    CMD_MAINS_BREAKER,
    CMD_RESET_ALARMS,
    CMD_START,
    CMD_STOP,
    DOMAIN,
)
from .coordinator import ComapAmf25Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ComapAmf25ButtonDescription(ButtonEntityDescription):
    """Adds the command path to a regular ButtonEntityDescription."""

    command: str = ""


BUTTON_TYPES: tuple[ComapAmf25ButtonDescription, ...] = (
    ComapAmf25ButtonDescription(
        key="start", name="Start", icon="mdi:engine", command=CMD_START
    ),
    ComapAmf25ButtonDescription(
        key="stop", name="Stop", icon="mdi:engine-off", command=CMD_STOP
    ),
    ComapAmf25ButtonDescription(
        key="mains_breaker",
        name="Toggle Mains Breaker",
        icon="mdi:electric-switch",
        command=CMD_MAINS_BREAKER,
    ),
    ComapAmf25ButtonDescription(
        key="gen_breaker",
        name="Toggle Generator Breaker",
        icon="mdi:electric-switch",
        command=CMD_GEN_BREAKER,
    ),
    ComapAmf25ButtonDescription(
        key="reset_alarms",
        name="Reset Alarms",
        icon="mdi:bell-cancel",
        command=CMD_RESET_ALARMS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the command buttons."""
    coordinator: ComapAmf25Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ComapAmf25CommandButton(coordinator, entry, description)
        for description in BUTTON_TYPES
    )


class ComapAmf25CommandButton(CoordinatorEntity[ComapAmf25Coordinator], ButtonEntity):
    """A single GET-request command exposed as a button."""

    _attr_has_entity_name = True
    entity_description: ComapAmf25ButtonDescription

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        description: ComapAmf25ButtonDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = device_info(entry)
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    async def async_press(self) -> None:
        _LOGGER.debug(
            "Sending ComAp AMF 25 command %s", self.entity_description.command
        )
        await self.coordinator.client.async_send_command(
            self.entity_description.command
        )
        await self.coordinator.async_request_refresh()
