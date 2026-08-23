"""Binary sensors for the ComAp AMF 25: alarm state, plus Engine and
Generator digital I/O.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ComapAmf25Coordinator, ComapAmf25SetpointsCoordinator
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the alarm binary sensor and the Engine group's digital I/O."""
    coordinator: ComapAmf25Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = [
        ComapAmf25AlarmBinarySensor(coordinator, entry)
    ]
    for name in coordinator.data.engine_binary:
        entities.append(ComapAmf25EngineBinarySensor(coordinator, entry, name))
    for name in coordinator.data.generator_binary:
        entities.append(ComapAmf25GeneratorBinarySensor(coordinator, entry, name))
    for name in coordinator.data.mains_binary:
        entities.append(ComapAmf25MainsBinarySensor(coordinator, entry, name))
    for name in coordinator.data.controller_io_binary:
        entities.append(
            ComapAmf25ControllerIOBinarySensor(coordinator, entry, name)
        )
    for name in coordinator.data.extension_io_binary:
        entities.append(
            ComapAmf25ExtensionIOBinarySensor(coordinator, entry, name)
        )
    for name in coordinator.data.statistics_binary:
        entities.append(ComapAmf25StatisticsBinarySensor(coordinator, entry, name))
    for name in coordinator.data.il_info_binary:
        entities.append(ComapAmf25ILInfoBinarySensor(coordinator, entry, name))
    # Date/Time lives on the slow Setpoints coordinator, not the fast one -
    # see the note in coordinator.py's ComapAmf25SetpointsCoordinator.
    for name in coordinator.setpoints.data.date_time_binary:
        entities.append(
            ComapAmf25DateTimeBinarySensor(coordinator.setpoints, entry, name)
        )

    async_add_entities(entities)


class ComapAmf25AlarmBinarySensor(
    CoordinatorEntity[ComapAmf25Coordinator], BinarySensorEntity
):
    """On when the panel's AlarmList contains at least one active alarm."""

    _attr_has_entity_name = True
    _attr_name = "Alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info(entry)
        self._attr_unique_id = f"{entry.entry_id}_alarm"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.alarm_active

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        return {"alarms": self.coordinator.data.alarms}


def _engine_device_class(name: str) -> BinarySensorDeviceClass | None:
    """Best-effort device_class for a digital I/O channel from any group.

    Only applied where the channel's own name makes the on=problem
    polarity unambiguous ("Low ..." warnings, anything ending in
    "Alarm"). Channels like "Emergency Stop" or breaker feedback are
    left unclassified since their 0/1 polarity isn't confirmed from
    the page markup alone - worth checking against the real panel
    before automating on them.
    """
    lowered = name.lower()
    if lowered.startswith("low ") or lowered == "alarm" or lowered.endswith(" alarm"):
        return BinarySensorDeviceClass.PROBLEM
    return None


class ComapAmf25EngineBinarySensor(
    CoordinatorEntity[ComapAmf25Coordinator], BinarySensorEntity
):
    """A single digital I/O channel from the Engine Measurement group page."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info(entry)
        self._attr_name = f"Engine {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_engine_bin_{name.lower().replace(' ', '_').replace('/', '_')}"
        )
        self._attr_device_class = _engine_device_class(name)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.engine_binary.get(self._name)
        if value is None:
            return None
        return value == "1"


class ComapAmf25GeneratorBinarySensor(
    CoordinatorEntity[ComapAmf25Coordinator], BinarySensorEntity
):
    """A single digital I/O channel from the Generator Measurement group page."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info(entry)
        self._attr_name = f"Generator {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_generator_bin_"
            f"{name.lower().replace(' ', '_').replace('/', '_')}"
        )
        self._attr_device_class = _engine_device_class(name)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.generator_binary.get(self._name)
        if value is None:
            return None
        return value == "1"


class ComapAmf25MainsBinarySensor(
    CoordinatorEntity[ComapAmf25Coordinator], BinarySensorEntity
):
    """A single digital I/O channel from the Mains Measurement group page."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info(entry)
        self._attr_name = f"Mains {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_mains_bin_"
            f"{name.lower().replace(' ', '_').replace('/', '_')}"
        )
        self._attr_device_class = _engine_device_class(name)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.mains_binary.get(self._name)
        if value is None:
            return None
        return value == "1"


class ComapAmf25ControllerIOBinarySensor(
    CoordinatorEntity[ComapAmf25Coordinator], BinarySensorEntity
):
    """A single digital I/O channel from the Controller I/O group page."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info(entry)
        self._attr_name = f"Controller I/O {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_controller_io_bin_"
            f"{name.lower().replace(' ', '_').replace('/', '_')}"
        )
        self._attr_device_class = _engine_device_class(name)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.controller_io_binary.get(self._name)
        if value is None:
            return None
        return value == "1"


class ComapAmf25ExtensionIOBinarySensor(
    CoordinatorEntity[ComapAmf25Coordinator], BinarySensorEntity
):
    """A single digital I/O channel from the Extension I/O group page."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info(entry)
        self._attr_name = f"Extension I/O {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_extension_io_bin_"
            f"{name.lower().replace(' ', '_').replace('/', '_')}"
        )
        self._attr_device_class = _engine_device_class(name)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.extension_io_binary.get(self._name)
        if value is None:
            return None
        return value == "1"


class ComapAmf25StatisticsBinarySensor(
    CoordinatorEntity[ComapAmf25Coordinator], BinarySensorEntity
):
    """A single digital I/O channel from the Statistics group page."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info(entry)
        self._attr_name = f"Statistics {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_statistics_bin_"
            f"{name.lower().replace(' ', '_').replace('/', '_')}"
        )
        self._attr_device_class = _engine_device_class(name)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.statistics_binary.get(self._name)
        if value is None:
            return None
        return value == "1"


class ComapAmf25ILInfoBinarySensor(
    CoordinatorEntity[ComapAmf25Coordinator], BinarySensorEntity
):
    """A single digital I/O channel from the IL Info group page."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ComapAmf25Coordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info(entry)
        self._attr_name = f"IL Info {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_il_info_bin_"
            f"{name.lower().replace(' ', '_').replace('/', '_')}"
        )
        self._attr_device_class = _engine_device_class(name)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.il_info_binary.get(self._name)
        if value is None:
            return None
        return value == "1"


class ComapAmf25DateTimeBinarySensor(
    CoordinatorEntity[ComapAmf25SetpointsCoordinator], BinarySensorEntity
):
    """A single digital I/O channel from the Date/Time group page.

    Bound to the slow Setpoints coordinator, not the fast one - see the
    note in coordinator.py's ComapAmf25SetpointsCoordinator.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ComapAmf25SetpointsCoordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info(entry)
        self._attr_name = f"Panel {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_date_time_bin_"
            f"{name.lower().replace(' ', '_').replace('/', '_')}"
        )
        self._attr_device_class = _engine_device_class(name)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.date_time_binary.get(self._name)
        if value is None:
            return None
        return value == "1"
