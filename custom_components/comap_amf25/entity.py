"""Shared helpers for ComAp AMF 25 entities."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_HOST, DOMAIN


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared by every entity of a config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"ComAp AMF 25 ({entry.data[CONF_HOST]})",
        manufacturer="ComAp",
        model="AMF 25",
    )
