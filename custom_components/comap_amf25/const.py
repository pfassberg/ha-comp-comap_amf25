"""Constants for the ComAp AMF 25 genset controller integration."""
from __future__ import annotations

DOMAIN = "comap_amf25"

CONF_HOST = "host"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SETPOINTS_SCAN_INTERVAL = "setpoints_scan_interval"

DEFAULT_SCAN_INTERVAL = 10  # seconds - the panel itself auto-refreshes every 5s
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 3600

# Setpoints are configuration parameters, not live telemetry - they only
# change when someone deliberately reconfigures the genset. Polling them
# on the same fast cycle as Measurement would double the request load
# for essentially static data, so they get their own, much slower,
# separately configurable interval.
DEFAULT_SETPOINTS_SCAN_INTERVAL = 1800  # 30 minutes
MIN_SETPOINTS_SCAN_INTERVAL = 60
MAX_SETPOINTS_SCAN_INTERVAL = 86400

# Modes exposed by the "OFF / MAN / AUT / TEST" selector on CONTROL.HTM.
# Index matches the numeric prefix used in the "<n>.MOD" links, e.g. 2.MOD = AUT.
MODES = {
    "0": "OFF",
    "1": "MAN",
    "2": "AUT",
    "3": "TEST",
}
MODE_TO_INDEX = {v: k for k, v in MODES.items()}

# Maps the gauge icon filename (without .gif) seen on CONTROL.HTM to a
# human-friendly sensor name, device_class and unit.
GAUGE_MAP = {
    "ico_oil": {"name": "Oil Pressure", "device_class": "pressure"},
    "ico_temp": {"name": "Engine Temperature", "device_class": "temperature"},
    "ico_fuel": {"name": "Fuel Level", "device_class": None},
    "ico_bat": {"name": "Battery Voltage", "device_class": "voltage"},
}

# Simple command endpoints available on the panel (relative URLs, GET requests).
# These are exposed as buttons/select options rather than hardcoded here, but
# kept as a reference of what the controller supports.
CMD_START = "start.CMD"
CMD_STOP = "stop.CMD"
CMD_MAINS_BREAKER = "mcb.CMD"
CMD_GEN_BREAKER = "gcb.CMD"
CMD_RESET_ALARMS = "flt_r.CMD"
CMD_LOGOUT = "exit.htm"

# Measurement group pages (values<n>.htm). All eight groups the panel
# offers are wired up: Engine, Generator, Mains, Controller I/O,
# Extension I/O, Statistics, IL Info, and Date/Time.
PATH_ENGINE_VALUES = "values2.htm"
PATH_GENERATOR_VALUES = "values5.htm"
PATH_MAINS_VALUES = "values7.htm"
PATH_CONTROLLER_IO_VALUES = "values8.htm"
PATH_EXTENSION_IO_VALUES = "values9.htm"
PATH_STATISTICS_VALUES = "values11.htm"
PATH_IL_INFO_VALUES = "values12.htm"
PATH_DATE_TIME_VALUES = "values13.htm"

# Setpoints group pages (params<n>.htm) - read-only display only, per
# explicit request. Nine groups total; wired up one at a time.
PATH_SETPOINTS_BASIC = "params0.htm"
PATH_SETPOINTS_ENGINE_PARAMS = "params1.htm"
PATH_SETPOINTS_ENGINE_PROTECT = "params3.htm"
PATH_SETPOINTS_GENER_PROTECT = "params4.htm"
PATH_SETPOINTS_AMF_SETTINGS = "params6.htm"
PATH_SETPOINTS_EXTENSION_IO = "params9.htm"
PATH_SETPOINTS_DATE_TIME = "params13.htm"
PATH_SETPOINTS_SENSORS_SPEC = "params14.htm"
PATH_SETPOINTS_SMS_EMAIL = "params15.htm"

# History log page - a fixed-column event table, polled on the same
# slower cycle as Setpoints since new rows only appear on genuine
# events (start/stop, breaker changes, alarms), not continuously.
PATH_HISTORY = "history.htm"
