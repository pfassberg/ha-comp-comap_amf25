"""Sensors for the ComAp AMF 25 genset controller."""
from __future__ import annotations

import math
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MODES
from .coordinator import ComapAmf25Coordinator, ComapAmf25SetpointsCoordinator
from .entity import device_info as _device_info


def _build_entity_name(prefix: str, name: str) -> str:
    """Combine a group prefix with a panel field name, without
    duplicating a word the field name already starts with.

    The Mains group's own field names already read "Mains V L1-N",
    "Mains Freq" etc., so prefixing "Mains " again produced entities
    named "Mains Mains V L1-N". This only skips the prefix on an exact
    (case-insensitive) word match, so it doesn't accidentally affect
    abbreviated cases like Generator's "Gen kW" (kept as "Generator Gen
    kW" - "Gen" isn't the same word as "Generator").
    """
    if name.lower().startswith(prefix.lower()):
        return name
    return f"{prefix} {name}"


def _infer_precision(raw_value: str | None, scale: float = 1.0) -> int | None:
    """Derive a display precision matching the panel's own formatting.

    The panel is consistent per-field about how many decimals it shows
    (e.g. Battery Volts is always "27.2"-style, Engine Temp is always a
    bare integer) - counting digits after the decimal point in the raw
    string, once at entity creation, reproduces that in the HA frontend
    via suggested_display_precision. This only affects display: the
    actual stored/graphed value is untouched, so statistics stay exact
    even if a future reading happens to carry more decimals.

    `scale` accounts for sensors whose value is multiplied before
    display (e.g. kVA -> VA, scale=1000): "45.2" has 1 raw decimal, but
    45.2 * 1000 = 45200.0 has none worth showing, so the raw count is
    shifted down by the power of ten in `scale`.

    Computed once at entity creation, not on every update - fields
    where a single first-seen sample isn't reliable (e.g. the panel
    sometimes omits the decimal point for a round-number reading, like
    "403" instead of "403.2") should go in _PRECISION_OVERRIDES below
    instead of relying on this.

    Returns None (no opinion) for anything that isn't a plain number.
    """
    if raw_value is None:
        return None
    raw_value = raw_value.strip()
    try:
        float(raw_value)
    except ValueError:
        return None
    precision = len(raw_value.split(".")[1]) if "." in raw_value else 0
    if scale != 1.0:
        precision = max(0, precision - round(math.log10(scale)))
    return precision


# Explicit precision overrides, keyed by the panel's own field name.
# Wins over _infer_precision's single-sample guess - use this for any
# field where that guess turns out wrong (e.g. it happened to catch a
# round-number reading at startup). Extend as needed.
_PRECISION_OVERRIDES: dict[str, int] = {
    "Mains V L1-N": 1,
    "Mains V L2-N": 1,
    "Mains V L3-N": 1,
    "Mains V L1-L2": 1,
    "Mains V L2-L3": 1,
    "Mains V L3-L1": 1,
    # Shared by both the Control page's own "Run Hours" and the
    # Statistics group's "Statistics Run Hours" - both read this same
    # raw field name, so one entry covers both.
    "Run Hours": 1,
    # Statistics group counters - always whole numbers.
    "Num Starts": 0,
    "Num E-Stops": 0,
    "Shutdowns": 0,
}


def _get_precision(name: str, raw_value: str | None, scale: float = 1.0) -> int | None:
    """Look up an explicit override for `name`, else fall back to
    inferring from `raw_value`."""
    if name in _PRECISION_OVERRIDES:
        return _PRECISION_OVERRIDES[name]
    return _infer_precision(raw_value, scale)


# Display prefix for each Setpoints group slug (see coordinator.py's
# ComapAmf25SetpointsCoordinator, which keys data.groups by these same
# slugs). Extended as more groups get wired up.
_SETPOINTS_GROUP_LABELS: dict[str, str] = {
    "basic": "Setpoint Basic",
    "engine_params": "Setpoint Engine",
    "engine_protect": "Setpoint Engine Protect",
    "gener_protect": "Setpoint Generator Protect",
    "amf_settings": "Setpoint AMF",
    "extension_io": "Setpoint Extension I/O",
    "date_time": "Setpoint Date/Time",
    "sensors_spec": "Setpoint Sensor Spec",
    "sms_email": "Setpoint SMS/E-Mail",
}

# Map the unit string as printed on the panel to
# (device_class, state_class, ha_unit, scale_to_ha_unit).
# HA's apparent/reactive power device classes only recognize the base
# unit (VA / var) in this version - no kilo variant - so kVA/kVAr are
# converted (*1000) into those rather than tagged with an invalid unit,
# which would otherwise log a warning on every entity that uses them.
_UNIT_MAP: dict[
    str, tuple[SensorDeviceClass | None, SensorStateClass | None, str, float]
] = {
    "kW": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "kW", 1.0),
    "kVA": (SensorDeviceClass.APPARENT_POWER, SensorStateClass.MEASUREMENT, "VA", 1000.0),
    "kVAr": (SensorDeviceClass.REACTIVE_POWER, SensorStateClass.MEASUREMENT, "var", 1000.0),
    "Hz": (SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT, "Hz", 1.0),
    "V": (SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "V", 1.0),
    "A": (SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "A", 1.0),
    "h": (SensorDeviceClass.DURATION, SensorStateClass.TOTAL_INCREASING, "h", 1.0),
    "min": (SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "min", 1.0),
    "s": (SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "s", 1.0),
    "Bar": (SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, "bar", 1.0),
    "°C": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "°C", 1.0),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors based on whatever values the panel reported on first refresh."""
    coordinator: ComapAmf25Coordinator = hass.data[DOMAIN][entry.entry_id]
    device_info = _device_info(entry)

    entities: list[SensorEntity] = []

    for name in coordinator.data.values:
        entities.append(ComapAmf25ValueSensor(coordinator, entry, device_info, name))

    for icon in coordinator.data.gauges:
        entities.append(ComapAmf25GaugeSensor(coordinator, entry, device_info, icon))

    entities.append(ComapAmf25StatusSensor(coordinator, entry, device_info))
    entities.append(ComapAmf25OperationSensor(coordinator, entry, device_info))
    entities.append(ComapAmf25TimerSensor(coordinator, entry, device_info))
    entities.append(ComapAmf25AlarmCountSensor(coordinator, entry, device_info))

    for name in coordinator.data.engine_values:
        entities.append(
            ComapAmf25EngineValueSensor(coordinator, entry, device_info, name)
        )

    # Exact duplicates of Control page sensors with identical names
    # (present since the original version of this integration) -
    # confirmed against the actual field lists: Gen kW, Gen kVA, Gen
    # Freq, and the Gen V/A L1/L2/L3 values are all on the Scada page
    # under the exact same names. Energy kWh and Run Hours also overlap
    # with the Statistics group, but are kept there deliberately.
    _generator_duplicates = {
        "Gen kW", "Gen kVA", "Gen Freq",
        "Gen V L1-N", "Gen V L2-N", "Gen V L3-N",
        "Gen A L1", "Gen A L2", "Gen A L3",
    }
    for name in coordinator.data.generator_values:
        if name in _generator_duplicates:
            continue
        entities.append(
            ComapAmf25GeneratorValueSensor(coordinator, entry, device_info, name)
        )

    # Exact duplicates of Control page sensors with identical names
    # (present since the original version of this integration) - after
    # the "Mains Mains" naming fix, both copies would display under the
    # identical name, which is confusing rather than useful. Kept the
    # Control page's originals rather than these newer copies.
    _mains_duplicates = {"Mains Freq", "Mains V L1-N", "Mains V L2-N", "Mains V L3-N"}
    for name in coordinator.data.mains_values:
        if name in _mains_duplicates:
            continue
        entities.append(
            ComapAmf25MainsValueSensor(coordinator, entry, device_info, name)
        )

    for name in coordinator.data.controller_io_values:
        entities.append(
            ComapAmf25ControllerIOValueSensor(coordinator, entry, device_info, name)
        )

    for name in coordinator.data.extension_io_values:
        entities.append(
            ComapAmf25ExtensionIOValueSensor(coordinator, entry, device_info, name)
        )

    for name in coordinator.data.statistics_values:
        entities.append(
            ComapAmf25StatisticsValueSensor(coordinator, entry, device_info, name)
        )

    for name in coordinator.data.il_info_values:
        entities.append(
            ComapAmf25ILInfoValueSensor(coordinator, entry, device_info, name)
        )

    for group_slug, params in coordinator.setpoints.data.groups.items():
        for name in params:
            entities.append(
                ComapAmf25SetpointsValueSensor(
                    coordinator.setpoints, entry, device_info, group_slug, name
                )
            )

    # Date/Time lives on the slow Setpoints coordinator, not the fast
    # one - see the note in coordinator.py's ComapAmf25SetpointsCoordinator.
    for name in coordinator.setpoints.data.date_time_values:
        entities.append(
            ComapAmf25DateTimeValueSensor(coordinator.setpoints, entry, device_info, name)
        )
    entities.append(ComapAmf25TimeDiffSensor(coordinator.setpoints, entry, device_info))

    entities.append(ComapAmf25HistoryLastEventSensor(coordinator.setpoints, entry, device_info))

    async_add_entities(entities)


class _ComapAmf25BaseSensor(CoordinatorEntity[ComapAmf25Coordinator], SensorEntity):
    """Common bits shared by all ComAp AMF 25 sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"


class ComapAmf25ValueSensor(_ComapAmf25BaseSensor):
    """A single labelled value from the Scada page (e.g. 'Gen kW')."""

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(coordinator, entry, device_info, name.lower().replace(" ", "_"))
        self._name = name
        self._attr_name = name
        self._scale = 1.0

        unit = coordinator.data.values.get(name, {}).get("unit", "")
        if "kWh" in name:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = "kWh"
        elif unit in _UNIT_MAP:
            device_class, state_class, ha_unit, scale = _UNIT_MAP[unit]
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = ha_unit
            self._scale = scale
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.values.get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw, self._scale)

    @property
    def native_value(self) -> str | float | None:
        raw = self.coordinator.data.values.get(self._name, {}).get("value")
        if raw is None or raw == "":
            return None
        try:
            return float(raw) * self._scale
        except ValueError:
            return raw


class ComapAmf25GaugeSensor(_ComapAmf25BaseSensor):
    """A gauge value (oil pressure, temperature, fuel level, battery voltage)."""

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        icon: str,
    ) -> None:
        super().__init__(coordinator, entry, device_info, icon)
        self._icon_key = icon
        gauge = coordinator.data.gauges[icon]
        self._attr_name = gauge["name"]
        if gauge["device_class"]:
            self._attr_device_class = SensorDeviceClass(gauge["device_class"])
            self._attr_state_class = SensorStateClass.MEASUREMENT

        raw_unit = gauge["unit"]
        if raw_unit in _UNIT_MAP:
            # Reuse the already-correct casing from _UNIT_MAP (e.g. the
            # panel's "Bar" needs to become lowercase "bar" - HA's
            # pressure device class only recognizes the latter).
            self._attr_native_unit_of_measurement = _UNIT_MAP[raw_unit][2]
        else:
            unit = raw_unit.replace("&deg;", "°")
            if unit:
                self._attr_native_unit_of_measurement = unit

        self._attr_suggested_display_precision = _get_precision(
            gauge["name"], gauge["value"]
        )

    @property
    def native_value(self) -> float | None:
        gauge = self.coordinator.data.gauges.get(self._icon_key)
        if not gauge or gauge["fault"]:
            return None
        try:
            return float(gauge["value"])
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        gauge = self.coordinator.data.gauges.get(self._icon_key, {})
        return {
            "low_limit": gauge.get("low"),
            "high_limit": gauge.get("high"),
            "sensor_fault": gauge.get("fault", False),
        }


class ComapAmf25StatusSensor(_ComapAmf25BaseSensor):
    """Top-left status line, e.g. 'Ready', 'Starting', 'Running'."""

    _attr_name = "Status"

    def __init__(self, coordinator, entry, device_info) -> None:
        super().__init__(coordinator, entry, device_info, "status")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.status


class ComapAmf25OperationSensor(_ComapAmf25BaseSensor):
    """Operation line, e.g. 'MainsOper', 'GenOper'."""

    _attr_name = "Operation"

    def __init__(self, coordinator, entry, device_info) -> None:
        super().__init__(coordinator, entry, device_info, "operation")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.operation


class ComapAmf25TimerSensor(_ComapAmf25BaseSensor):
    """Countdown timer shown next to the status line (0 when idle)."""

    _attr_name = "Timer"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "s"

    def __init__(self, coordinator, entry, device_info) -> None:
        super().__init__(coordinator, entry, device_info, "timer")
        self._attr_suggested_display_precision = _get_precision(
            "Timer", coordinator.data.timer_value
        )

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.timer_value
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"label": self.coordinator.data.timer_label}


class ComapAmf25AlarmCountSensor(_ComapAmf25BaseSensor):
    """Number of active alarms, with the alarm text list as an attribute."""

    _attr_name = "Active Alarms"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, device_info) -> None:
        super().__init__(coordinator, entry, device_info, "active_alarms")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.alarms)

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        return {"alarms": self.coordinator.data.alarms}


class ComapAmf25EngineValueSensor(_ComapAmf25BaseSensor):
    """A single labelled value from the Engine Measurement group page.

    Some of these (Battery Volts, Oil Pressure, Engine Temp) overlap
    with the gauges already shown on the Scada page - both are exposed
    since they may not always agree exactly, and it's easy to disable
    whichever copy you don't want from the entity list.
    """

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(
            coordinator, entry, device_info, f"engine_{name.lower().replace(' ', '_')}"
        )
        self._name = name
        self._attr_name = _build_entity_name("Engine", name)
        self._scale = 1.0

        unit = coordinator.data.engine_values.get(name, {}).get("unit", "")
        unit = unit.replace("&deg;", "°")
        if unit in _UNIT_MAP:
            device_class, state_class, ha_unit, scale = _UNIT_MAP[unit]
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = ha_unit
            self._scale = scale
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.engine_values.get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw, self._scale)

    @property
    def native_value(self) -> str | float | None:
        entry = self.coordinator.data.engine_values.get(self._name)
        if not entry or entry.get("fault") or entry.get("value") in (None, ""):
            return None
        try:
            return float(entry["value"]) * self._scale
        except ValueError:
            return entry["value"]

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        entry = self.coordinator.data.engine_values.get(self._name, {})
        return {"sensor_fault": entry.get("fault", False)}


class ComapAmf25GeneratorValueSensor(_ComapAmf25BaseSensor):
    """A single labelled value from the Generator Measurement group page.

    Gen kW, Gen kVA, Gen Freq, Gen V L1-N/L2-N/L3-N, and Gen A L1/L2/L3
    are skipped entirely (see the setup loop in async_setup_entry) -
    exact name collisions with the Control page's own sensors of the
    same name, kept there rather than here to preserve any existing
    history. Everything else here (per-phase kW/kVAr/kVA/PF, load
    character, and the line-to-line voltages) isn't available anywhere
    else in this integration.
    """

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            device_info,
            f"generator_{name.lower().replace(' ', '_')}",
        )
        self._name = name
        self._attr_name = _build_entity_name("Generator", name)
        self._scale = 1.0

        unit = coordinator.data.generator_values.get(name, {}).get("unit", "")
        if name.startswith("Gen PF"):
            # Power factor (Gen PF, Gen PF L1/L2/L3) has no unit in the
            # panel's own HTML - device_class alone (no unit set) is
            # exactly what HA's power_factor class expects for a plain
            # ratio like these.
            self._attr_device_class = SensorDeviceClass.POWER_FACTOR
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit in _UNIT_MAP:
            device_class, state_class, ha_unit, scale = _UNIT_MAP[unit]
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = ha_unit
            self._scale = scale
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.generator_values.get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw, self._scale)

    @property
    def native_value(self) -> str | float | None:
        entry = self.coordinator.data.generator_values.get(self._name)
        if not entry or entry.get("fault") or entry.get("value") in (None, ""):
            return None
        try:
            return float(entry["value"]) * self._scale
        except ValueError:
            return entry["value"]

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        entry = self.coordinator.data.generator_values.get(self._name, {})
        return {"sensor_fault": entry.get("fault", False)}


class ComapAmf25MainsValueSensor(_ComapAmf25BaseSensor):
    """A single labelled value from the Mains Measurement group page.

    Only the line-to-line voltages (L1-L2, L2-L3, L3-L1) end up as
    entities here - they aren't available anywhere else. Mains Freq
    and the L*-N voltages are skipped entirely (see the setup loop in
    async_setup_entry) since they're exact name collisions with the
    Control page's own sensors of the same name, not just a semantic
    overlap like the other groups have.
    """

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(
            coordinator, entry, device_info, f"mains_{name.lower().replace(' ', '_')}"
        )
        self._name = name
        self._attr_name = _build_entity_name("Mains", name)
        self._scale = 1.0

        unit = coordinator.data.mains_values.get(name, {}).get("unit", "")
        if unit in _UNIT_MAP:
            device_class, state_class, ha_unit, scale = _UNIT_MAP[unit]
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = ha_unit
            self._scale = scale
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.mains_values.get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw, self._scale)

    @property
    def native_value(self) -> str | float | None:
        entry = self.coordinator.data.mains_values.get(self._name)
        if not entry or entry.get("fault") or entry.get("value") in (None, ""):
            return None
        try:
            return float(entry["value"]) * self._scale
        except ValueError:
            return entry["value"]

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        entry = self.coordinator.data.mains_values.get(self._name, {})
        return {"sensor_fault": entry.get("fault", False)}


class ComapAmf25ControllerIOValueSensor(_ComapAmf25BaseSensor):
    """A single labelled value from the Controller I/O Measurement group page.

    Battery Volts/Oil Pressure/Engine Temp overlap with the Scada page
    gauges (and possibly with the Engine group too, depending what the
    real panel actually returns for that page) - kept for the same
    reason as the other groups' overlap. D+ is new.
    """

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            device_info,
            f"controller_io_{name.lower().replace(' ', '_')}",
        )
        self._name = name
        self._attr_name = _build_entity_name("Controller I/O", name)
        self._scale = 1.0

        unit = coordinator.data.controller_io_values.get(name, {}).get("unit", "")
        unit = unit.replace("&deg;", "°")
        if unit in _UNIT_MAP:
            device_class, state_class, ha_unit, scale = _UNIT_MAP[unit]
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = ha_unit
            self._scale = scale
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.controller_io_values.get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw, self._scale)

    @property
    def native_value(self) -> str | float | None:
        entry = self.coordinator.data.controller_io_values.get(self._name)
        if not entry or entry.get("fault") or entry.get("value") in (None, ""):
            return None
        try:
            return float(entry["value"]) * self._scale
        except ValueError:
            return entry["value"]

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        entry = self.coordinator.data.controller_io_values.get(self._name, {})
        return {"sensor_fault": entry.get("fault", False)}


class ComapAmf25ExtensionIOValueSensor(_ComapAmf25BaseSensor):
    """A single labelled value from the Extension I/O Measurement group page.

    The IOM AI1-4 analog inputs report units "U4"/"U5"/"U6"/"U7" on the
    panel rather than a physical unit like V or mA - these look like
    internal channel/scaling tags rather than real units, so they're
    passed through as-is (unmapped) rather than guessed at.
    """

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            device_info,
            f"extension_io_{name.lower().replace(' ', '_')}",
        )
        self._name = name
        self._attr_name = _build_entity_name("Extension I/O", name)
        self._scale = 1.0

        unit = coordinator.data.extension_io_values.get(name, {}).get("unit", "")
        if unit in _UNIT_MAP:
            device_class, state_class, ha_unit, scale = _UNIT_MAP[unit]
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = ha_unit
            self._scale = scale
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.extension_io_values.get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw, self._scale)

    @property
    def native_value(self) -> str | float | None:
        entry = self.coordinator.data.extension_io_values.get(self._name)
        if not entry or entry.get("fault") or entry.get("value") in (None, ""):
            return None
        try:
            return float(entry["value"]) * self._scale
        except ValueError:
            return entry["value"]

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        entry = self.coordinator.data.extension_io_values.get(self._name, {})
        return {"sensor_fault": entry.get("fault", False)}


class ComapAmf25StatisticsValueSensor(_ComapAmf25BaseSensor):
    """A single labelled value from the Statistics Measurement group page.

    A few of these need special handling beyond the usual unit lookup:
    - Energy kWh: same energy device_class treatment as the Scada page's
      copy (its unit column is blank - the unit is baked into the name).
    - Energy kVArh: HA has no reactive-energy device_class in this
      version, so this stays a plain value with unit "kVArh".
    - Maintenance: shares the "h" unit with Run Hours, but almost
      certainly counts down toward a service interval rather than up,
      so it's kept as a plain measurement rather than tagged
      total_increasing (which Run Hours correctly gets via the normal
      unit lookup, since an engine runtime counter only increases).
    - Num Starts / Num E-Stops / Shutdowns: lifetime counters with no
      unit on the panel - left as plain numbers rather than guessing
      at total_increasing, since these can typically be reset manually.
    """

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            device_info,
            f"statistics_{name.lower().replace(' ', '_')}",
        )
        self._name = name
        self._attr_name = _build_entity_name("Statistics", name)
        self._scale = 1.0

        unit = coordinator.data.statistics_values.get(name, {}).get("unit", "")
        if name == "Energy kWh":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = "kWh"
        elif name == "Energy kVArh":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = "kVArh"
        elif unit in _UNIT_MAP:
            device_class, state_class, ha_unit, scale = _UNIT_MAP[unit]
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = ha_unit
            self._scale = scale
            if name == "Maintenance":
                self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.statistics_values.get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw, self._scale)

    @property
    def native_value(self) -> str | float | None:
        entry = self.coordinator.data.statistics_values.get(self._name)
        if not entry or entry.get("fault") or entry.get("value") in (None, ""):
            return None
        try:
            return float(entry["value"]) * self._scale
        except ValueError:
            return entry["value"]

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        entry = self.coordinator.data.statistics_values.get(self._name, {})
        return {"sensor_fault": entry.get("fault", False)}


class ComapAmf25ILInfoValueSensor(_ComapAmf25BaseSensor):
    """A single labelled value from the IL Info Measurement group page.

    Engine State / Breaker State / Timer Text / Timer Value duplicate
    the Scada page's Status/Operation/Timer sensors in text form - kept
    for the same reason as the other groups' overlap. FW Version /
    Application / FW Branch / DiagData are new, and are handy for
    noticing a firmware change. "PasswordDecode" is deliberately
    excluded by the parser - see the note in api.py.
    """

    def __init__(
        self,
        coordinator: ComapAmf25Coordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            device_info,
            f"il_info_{name.lower().replace(' ', '_')}",
        )
        self._name = name
        self._attr_name = _build_entity_name("IL Info", name)
        self._scale = 1.0

        unit = coordinator.data.il_info_values.get(name, {}).get("unit", "")
        if unit in _UNIT_MAP:
            device_class, state_class, ha_unit, scale = _UNIT_MAP[unit]
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = ha_unit
            self._scale = scale
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.il_info_values.get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw, self._scale)

    @property
    def native_value(self) -> str | float | None:
        entry = self.coordinator.data.il_info_values.get(self._name)
        if not entry or entry.get("fault") or entry.get("value") in (None, ""):
            return None
        try:
            return float(entry["value"]) * self._scale
        except ValueError:
            return entry["value"]

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        entry = self.coordinator.data.il_info_values.get(self._name, {})
        return {"sensor_fault": entry.get("fault", False)}


class ComapAmf25DateTimeValueSensor(
    CoordinatorEntity[ComapAmf25SetpointsCoordinator], SensorEntity
):
    """The panel's own Time/Date, shown as plain text.

    Kept as-is (HH:MM:SS / DD/MM/YY strings) rather than parsed into a
    real datetime: the panel's own clock and locale format aren't
    guaranteed, and this is mainly useful as a way to notice if the
    panel's clock has drifted from reality, not as a live clock.

    Bound to the slow Setpoints coordinator rather than the fast one -
    "Time" changes on essentially every fast-cycle poll by definition,
    which floods Home Assistant's history/logbook for a value that's
    only useful checked occasionally, not tracked in near-real-time.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ComapAmf25SetpointsCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_device_info = device_info
        self._attr_name = f"Panel {name}"
        self._attr_unique_id = (
            f"{entry.entry_id}_date_time_{name.lower().replace(' ', '_')}"
        )

    @property
    def native_value(self) -> str | None:
        entry = self.coordinator.data.date_time_values.get(self._name)
        if not entry or entry.get("fault") or entry.get("value") in (None, ""):
            return None
        return entry["value"]

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        entry = self.coordinator.data.date_time_values.get(self._name, {})
        return {"sensor_fault": entry.get("fault", False)}


class ComapAmf25TimeDiffSensor(
    CoordinatorEntity[ComapAmf25SetpointsCoordinator], SensorEntity
):
    """Panel clock minus Home Assistant's own clock, in seconds.

    Positive means the panel's clock is ahead of Home Assistant's;
    negative means it's behind. Built from the same Panel Time/Date
    values already exposed as their own sensors, so it updates on the
    same ~30 min Setpoints cadence rather than continuously - fine for
    a clock-drift check, since the panel's clock isn't expected to
    drift meaningfully faster than that. The panel doesn't report a
    timezone, so its Date/Time is assumed to be in Home Assistant's own
    configured local time (reasonable for a device on the same
    premises).
    """

    _attr_has_entity_name = True
    _attr_name = "Time Diff"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: ComapAmf25SetpointsCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{entry.entry_id}_time_diff"

    @property
    def native_value(self) -> int | None:
        values = self.coordinator.data.date_time_values
        time_str = values.get("Time", {}).get("value")
        date_str = values.get("Date", {}).get("value")
        if not time_str or not date_str:
            return None
        try:
            panel_naive = datetime.strptime(
                f"{date_str} {time_str}", "%d/%m/%y %H:%M:%S"
            )
        except ValueError:
            return None
        panel_utc = dt_util.as_utc(panel_naive)
        return round((panel_utc - dt_util.now(dt_util.UTC)).total_seconds())



class ComapAmf25SetpointsValueSensor(
    CoordinatorEntity[ComapAmf25SetpointsCoordinator], SensorEntity
):
    """A single read-only configuration parameter from a Setpoints group.

    Gets a device_class where the unit maps to an unambiguous one (e.g.
    kW, V, °C - matching how the Measurement sensors resolve it), since
    that's about correctly labeling what kind of value this is, which
    holds just as well for a static setpoint as for a live reading.
    state_class is deliberately never set, though - that's specifically
    about whether a value makes sense to graph over time via HA's
    statistics, which a static configuration value doesn't. The valid
    range or enumerated choices the panel itself reports (e.g. "(1 -
    5000)" or "(OFF, MAN, AUT, TEST)") is kept as an attribute for
    reference.

    This one class covers every Setpoints group generically (there are
    nine total) rather than a dedicated class per group like
    Measurement uses, since groups here are just name/value/unit/range
    parameter lists with no group-specific special-casing needed so far.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ComapAmf25SetpointsCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        group_slug: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._group_slug = group_slug
        self._name = name
        self._attr_device_info = device_info
        label = _SETPOINTS_GROUP_LABELS.get(group_slug, group_slug.title())
        self._attr_name = _build_entity_name(label, name)
        self._attr_unique_id = (
            f"{entry.entry_id}_setpoint_{group_slug}_"
            f"{name.lower().replace(' ', '_')}"
        )

        unit = (
            coordinator.data.groups.get(group_slug, {})
            .get(name, {})
            .get("unit", "")
        )
        unit = unit.replace("&deg;", "°")
        if unit in _UNIT_MAP:
            device_class = _UNIT_MAP[unit][0]
            if device_class:
                self._attr_device_class = device_class
            self._attr_native_unit_of_measurement = _UNIT_MAP[unit][2]
        elif unit:
            self._attr_native_unit_of_measurement = unit

        raw = coordinator.data.groups.get(group_slug, {}).get(name, {}).get("value")
        self._attr_suggested_display_precision = _get_precision(name, raw)

    @property
    def _entry(self) -> dict[str, str]:
        return self.coordinator.data.groups.get(self._group_slug, {}).get(
            self._name, {}
        )

    @property
    def native_value(self) -> str | float | None:
        value = self._entry.get("value")
        if value is None or value == "":
            return None
        try:
            return float(value)
        except ValueError:
            return value

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"valid_range": self._entry.get("range", "")}


class ComapAmf25HistoryLastEventSensor(
    CoordinatorEntity[ComapAmf25SetpointsCoordinator], SensorEntity
):
    """The panel's History log: the most recent event as the state,
    the full snapshot at that moment plus the whole visible log as
    attributes.

    A log doesn't map cleanly to individual entities the way a gauge
    or a setpoint does, so this is deliberately one entity rather than
    one per column - the abbreviated column names (RPM, Pwr, PF, Vg1-3
    etc.) are decoded into readable attribute names for the most
    recent event, while `recent_events` keeps the panel's own raw
    abbreviations so it can be cross-referenced directly against the
    History page if needed.
    """

    _attr_has_entity_name = True
    _attr_name = "History Last Event"

    def __init__(
        self,
        coordinator: ComapAmf25SetpointsCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{entry.entry_id}_history_last_event"

    @property
    def _latest(self) -> dict[str, str] | None:
        history = self.coordinator.data.history
        return history[0] if history else None

    @property
    def native_value(self) -> str | None:
        latest = self._latest
        return latest["Reason"] if latest else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        latest = self._latest
        if not latest:
            return {}
        return {
            "time": latest.get("Time"),
            "date": latest.get("Date"),
            "mode": MODES.get(latest.get("Mode", ""), latest.get("Mode")),
            "engine_rpm": latest.get("RPM"),
            "generator_kw": latest.get("Pwr"),
            "power_factor": latest.get("PF"),
            "load_character": latest.get("LChr"),
            "generator_freq_hz": latest.get("Gfrq"),
            "generator_voltage_l1": latest.get("Vg1"),
            "generator_voltage_l2": latest.get("Vg2"),
            "generator_voltage_l3": latest.get("Vg3"),
            "generator_current_l1": latest.get("Ig1"),
            "generator_current_l2": latest.get("Ig2"),
            "generator_current_l3": latest.get("Ig3"),
            "mains_voltage_l1": latest.get("Vm1"),
            "mains_voltage_l2": latest.get("Vm2"),
            "mains_voltage_l3": latest.get("Vm3"),
            "mains_freq_hz": latest.get("Mfrq"),
            "battery_voltage": latest.get("UBat"),
            "oil_pressure_bar": latest.get("OilP"),
            "engine_temp_c": latest.get("EngT"),
            "ai3": latest.get("AI3"),
            "bin_inputs": latest.get("BIN"),
            "bin_outputs": latest.get("BOUT"),
            "recent_events": self.coordinator.data.history,
        }
