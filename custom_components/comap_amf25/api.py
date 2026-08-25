"""Client for the ComAp AMF 25 genset controller web GUI.

The panel is a small embedded web server (no JSON API) that:
  * serves a login page at GET /  containing a hidden nonce field
    (name="pA") and sets a session cookie ("ID=...."),
  * expects the browser to compute MD5(nonce + password) in JS
    (see md5.js) and POST it back as field "pA" to /CONTROL.HTM,
  * on success, returns the CONTROL.HTM page directly (the POST
    response body *is* the status page),
  * on subsequent requests, GET /CONTROL.HTM returns the same page
    as long as the session cookie is still valid. When the session
    has expired, the login form is served again instead.

This client re-implements that login handshake and parses the
handful of well-structured HTML fragments the panel uses for its
labelled values (class="vsn"/"vsok"/"vsr"), gauges (bll/bhl/bvok or
bvsf/br), status messages (class="mlv"), mode buttons ("<n>.MOD"
links) and the alarm list (class="alie"/"alne").
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    CMD_LOGOUT,
    GAUGE_MAP,
    MODES,
    PATH_CONTROLLER_IO_VALUES,
    PATH_DATE_TIME_VALUES,
    PATH_ENGINE_VALUES,
    PATH_EXTENSION_IO_VALUES,
    PATH_GENERATOR_VALUES,
    PATH_HISTORY,
    PATH_IL_INFO_VALUES,
    PATH_MAINS_VALUES,
    PATH_SETPOINTS_BASIC,
    PATH_SETPOINTS_ENGINE_PARAMS,
    PATH_SETPOINTS_ENGINE_PROTECT,
    PATH_SETPOINTS_AMF_SETTINGS,
    PATH_SETPOINTS_DATE_TIME,
    PATH_SETPOINTS_EXTENSION_IO,
    PATH_SETPOINTS_GENER_PROTECT,
    PATH_SETPOINTS_SENSORS_SPEC,
    PATH_SETPOINTS_SMS_EMAIL,
    PATH_STATISTICS_VALUES,
)

_LOGGER = logging.getLogger(__name__)

_NONCE_RE = re.compile(r'id="hpsw_id"[^>]*value="([0-9A-Fa-f]+)"')
_LOGIN_MARKER = 'id="psw_id"'
# Any of these appearing means we're looking at the authenticated status
# page rather than the login form (used to detect an already-logged-in
# session so we don't mistake it for a parse failure).
_AUTHENTICATED_MARKERS = ('class="vsn"', 'class="mlv"', 'class="tsch"')
# The panel's own generic error page (red background, titled "IB-Lite
# error") - confirmed by packet capture to be shown for things like a
# concurrent-client limit ("Too many other clients connected. Try your
# request later.") instead of the login page. The actual message is in
# a `class="e"` div; extracting just that text turns a 600-character
# raw HTML dump into a one-line, directly actionable error message in
# both the HA log and the "why is this entry not ready" GUI text.
_IB_LITE_ERROR_RE = re.compile(
    r'<title>IB-Lite error</title>.*?class="e">([^<]*)</div>', re.S
)

_VAL_RE = re.compile(
    r'class="vsn">([^<]+)</td>.*?class="vsok">([^<]*)</td>'
    r'.*?class="vsr">([^<]*)</td>',
    re.S,
)
_GAUGE_RE = re.compile(
    r'src="(ico_\w+)\.gif".*?class="bll">([^<]*)</td>'
    r'<td class="bhl">([^<]*)</td>.*?class="(bvok|bvsf)">([^<]*)</td>'
    r'.*?class="br">([^<]*)</td>',
    re.S,
)
_MLV_RE = re.compile(r'class="mlv">([^<]*)</td>', re.S)
_TIMER_RE = re.compile(
    r'mlv">MainsOper</td>.*?class="vsok">([^<]*)</td>'
    r'.*?class="vsr">([^<]*)</td>',
    re.S,
)
_MODE_RE = re.compile(r'href="(\d)\.MOD" class="(r[su]d)">([^<]*)</a>')
_ALARM_RE = re.compile(
    r'class="alie">(.*?)</td><td class="alne">([^<]*)</td>', re.S
)

# Measurement group pages (values<n>.htm) use a different set of CSS
# classes for the same idea: a labelled analog/text value, or a labelled
# binary (0/1) value.
_V_ANALOG_RE = re.compile(
    r'class="v_n">([^<]+)</td><td class="(v_ok|v_sf)">([^<]*)</td>'
    r'<td class="v_d">([^<]*)</td>',
    re.S,
)
_V_BINARY_RE = re.compile(
    r'class="v_bn">([^<]+)</td><td class="v_bok">([^<]*)</td>', re.S
)
# Placeholder channels that just duplicate the individual binary rows
# immediately below them, or genuinely aren't wired to anything - plus
# "PasswordDecode" (seen on the IL Info page), which is deliberately
# excluded: its name and raw numeric value look like they could encode
# credential-related information for a password-protected panel, and
# that's not something to casually pipe into HA's history/logbook
# without knowing exactly what it represents.
_VALUES_SKIP_NAMES = {"not used", "passworddecode"}
# Raw bitmask "container" rows (e.g. "Bin Inputs" -> "0110100", "IOM Bin
# Inp" -> "00000000", "IL-NT-BIO8" -> "xxxxxxxx") repeat the exact same
# information as the individual v_br rows right below them, just packed
# into one string - and their container names vary between groups, so
# this is matched by shape (a run of only 0/1/x characters) rather than
# by name, to generalize across pages this integration hasn't seen yet.
_BITMASK_RE = re.compile(r"^[01xX]{6,}$")

# Setpoints group pages (params<n>.htm) use yet another set of CSS
# classes: a parameter name/value pair (class="p_na"/"p_va") followed
# by a unit column (class="p_d"), with the valid range or enumerated
# choices shown as free text in a following row (class="p_ln"). Some
# rows have a malformed <a "URL" ...> tag missing the href= attribute
# name entirely - harmless here since only the text content is used.
_P_PARAM_RE = re.compile(
    r'class="p_na">([^<]*)</a></td><td class="p_v"><a[^>]*class="p_va">([^<]*)</a></td>'
    r'<td class="p_d">([^<]*)</td></tr>'
    r"\s*<tr class=\"p_lr\"><td></td><td class=\"p_ln\">([^<]*)</td>",
    re.S,
)

# history.htm is a fixed-column event log table, unlike anything else
# on the panel: 3 labelled cells (class="h_") for Reason/Time/Date,
# followed by 22 plain, unlabeled <td> cells in a fixed column order
# matching the page's own header row. Most-recent-first.
_HISTORY_ROW_RE = re.compile(
    r'<tr><td class="h_">([^<]*)</td><td class="h_">([^<]*)</td>'
    r'<td class="h_">([^<]*)</td>((?:<td>[^<]*</td>){22})</tr>',
    re.S,
)
_HISTORY_TD_RE = re.compile(r"<td>([^<]*)</td>")
_HISTORY_COLUMNS = (
    "Mode", "RPM", "Pwr", "PF", "LChr", "Gfrq", "Vg1", "Vg2", "Vg3",
    "Ig1", "Ig2", "Ig3", "Vm1", "Vm2", "Vm3", "Mfrq", "UBat", "OilP",
    "EngT", "AI3", "BIN", "BOUT",
)


class ComapAmf25AuthError(Exception):
    """Raised when login fails (wrong access code)."""


class ComapAmf25ConnectionError(Exception):
    """Raised when the panel cannot be reached."""


@dataclass
class ComapAmf25SetpointsData:
    """Parsed snapshot of the Setpoints (params<n>.htm) pages, plus the
    History event log - grouped together since both change slowly and
    share the same slower polling cycle.

    Unlike Measurement, `groups` uses a single nested dict keyed by
    group slug (rather than one dataclass field per group) since there
    are nine groups and each holds a handful of name/value/unit/range
    parameters rather than a fixed known shape.
    """

    groups: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    date_time_values: dict[str, dict[str, str]] = field(default_factory=dict)
    date_time_binary: dict[str, str] = field(default_factory=dict)


@dataclass
class ComapAmf25Data:
    """Parsed snapshot of CONTROL.HTM, plus any Measurement group pages."""

    values: dict[str, dict[str, str]] = field(default_factory=dict)
    gauges: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: str | None = None
    timer_label: str | None = None
    operation: str | None = None
    timer_value: str | None = None
    timer_unit: str | None = None
    mode: str | None = None
    alarms: list[str] = field(default_factory=list)
    alarm_active: bool = False
    engine_values: dict[str, dict[str, str]] = field(default_factory=dict)
    engine_binary: dict[str, str] = field(default_factory=dict)
    generator_values: dict[str, dict[str, str]] = field(default_factory=dict)
    generator_binary: dict[str, str] = field(default_factory=dict)
    mains_values: dict[str, dict[str, str]] = field(default_factory=dict)
    mains_binary: dict[str, str] = field(default_factory=dict)
    controller_io_values: dict[str, dict[str, str]] = field(default_factory=dict)
    controller_io_binary: dict[str, str] = field(default_factory=dict)
    extension_io_values: dict[str, dict[str, str]] = field(default_factory=dict)
    extension_io_binary: dict[str, str] = field(default_factory=dict)
    statistics_values: dict[str, dict[str, str]] = field(default_factory=dict)
    statistics_binary: dict[str, str] = field(default_factory=dict)
    il_info_values: dict[str, dict[str, str]] = field(default_factory=dict)
    il_info_binary: dict[str, str] = field(default_factory=dict)


class ComapAmf25Client:
    """Handles login + polling for a single ComAp AMF 25 panel."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, password: str
    ) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._password = password
        self._logged_in = False
        # Two independently-scheduled coordinators (the fast Scada/
        # Measurement one and the slow Setpoints/History one) share this
        # one client, and thus its login state (_logged_in, _last_page).
        # Without serializing access, an overlapping pair of update
        # cycles - most likely right at HA startup, when both do their
        # first refresh close together - can interleave requests and
        # corrupt that shared state, producing exactly the kind of
        # garbled/unexpected response this integration has seen before.
        # This lock ensures only one logical "fetch a page, logging in
        # if needed" sequence runs at a time.
        self._lock = asyncio.Lock()

    @property
    def _base_url(self) -> str:
        return f"http://{self._host}"

    async def async_login(self) -> None:
        """Log in with a single, browser-like GET/POST sequence.

        No forced logout before or after a failed attempt. A real
        browser never sends one, but our own code used to force one on
        every failed nonce lookup - which, since that's exactly what
        happens on every single one of Home Assistant's automatic
        retries, meant every retry did a logout+relogin cycle rather
        than a single clean login. If this panel has any kind of
        repeated-login-attempt protection, that's a very plausible way
        to trip and keep renewing a lockout aimed at us specifically -
        which would explain a plain browser succeeding immediately
        while our own retries never recover even after many attempts.
        """
        nonce, already_authenticated = await self._async_check_login_page(
            raise_on_failure=True
        )
        if already_authenticated:
            return  # _async_check_login_page already set _logged_in/_last_page.

        assert nonce is not None  # raise_on_failure=True guarantees this
        digest = hashlib.md5((nonce + self._password).encode()).hexdigest()

        body = f"q=&q=&pA={digest}"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            async with self._session.post(
                f"{self._base_url}/CONTROL.HTM", data=body, headers=headers
            ) as resp:
                resp.raise_for_status()
                text = await resp.text(encoding="latin-1")
        except aiohttp.ClientError as err:
            raise ComapAmf25ConnectionError(str(err)) from err

        if _LOGIN_MARKER in text:
            self._logged_in = False
            raise ComapAmf25AuthError("Login rejected - wrong access code")

        self._logged_in = True
        self._last_page = text

    async def _async_check_login_page(
        self, raise_on_failure: bool = False
    ) -> tuple[str | None, bool]:
        """GET / and return (nonce, already_authenticated).

        If the page is the login form, returns (nonce, False).
        If it looks like the already-authenticated status page instead,
        sets _logged_in/_last_page and returns (None, True).
        Otherwise returns (None, False), or raises if raise_on_failure.
        """
        try:
            async with self._session.get(f"{self._base_url}/") as resp:
                resp.raise_for_status()
                text = await resp.text(encoding="latin-1")
        except aiohttp.ClientError as err:
            raise ComapAmf25ConnectionError(str(err)) from err

        match = _NONCE_RE.search(text)
        if match:
            return match.group(1), False

        if any(marker in text for marker in _AUTHENTICATED_MARKERS):
            self._logged_in = True
            self._last_page = text
            return None, True

        if raise_on_failure:
            error_match = _IB_LITE_ERROR_RE.search(text)
            if error_match:
                # The panel's own error text, e.g. "Too many other
                # clients connected. Try your request later." - shows
                # up cleanly in both the HA log and the "not ready"
                # reason text in the GUI, instead of a raw HTML dump.
                raise ComapAmf25ConnectionError(
                    f"Panel returned an error: {error_match.group(1).strip()}"
                )
            snippet = " ".join(text.split())[:600]
            raise ComapAmf25ConnectionError(
                "Could not find login nonce - unexpected page content. "
                f"First 600 chars of response: {snippet!r}"
            )
        return None, False

    async def async_fetch(self) -> ComapAmf25Data:
        """Return the current parsed status, logging in first if needed."""
        text = await self._async_get_authenticated("CONTROL.HTM")
        return self._parse(text)

    async def async_fetch_engine_values(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return (analog_values, binary_values) from the Engine group page."""
        text = await self._async_get_authenticated(PATH_ENGINE_VALUES)
        return self._parse_values_page(text)

    async def async_fetch_generator_values(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return (analog_values, binary_values) from the Generator group page."""
        text = await self._async_get_authenticated(PATH_GENERATOR_VALUES)
        return self._parse_values_page(text)

    async def async_fetch_mains_values(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return (analog_values, binary_values) from the Mains group page."""
        text = await self._async_get_authenticated(PATH_MAINS_VALUES)
        return self._parse_values_page(text)

    async def async_fetch_controller_io_values(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return (analog_values, binary_values) from the Controller I/O page."""
        text = await self._async_get_authenticated(PATH_CONTROLLER_IO_VALUES)
        return self._parse_values_page(text)

    async def async_fetch_extension_io_values(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return (analog_values, binary_values) from the Extension I/O page."""
        text = await self._async_get_authenticated(PATH_EXTENSION_IO_VALUES)
        return self._parse_values_page(text)

    async def async_fetch_statistics_values(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return (analog_values, binary_values) from the Statistics page."""
        text = await self._async_get_authenticated(PATH_STATISTICS_VALUES)
        return self._parse_values_page(text)

    async def async_fetch_il_info_values(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return (analog_values, binary_values) from the IL Info page."""
        text = await self._async_get_authenticated(PATH_IL_INFO_VALUES)
        return self._parse_values_page(text)

    async def async_fetch_date_time_values(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return (analog_values, binary_values) from the Date/Time page."""
        text = await self._async_get_authenticated(PATH_DATE_TIME_VALUES)
        return self._parse_values_page(text)

    async def async_fetch_setpoints_basic(self) -> dict[str, dict[str, str]]:
        """Return the Basic Settings Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_BASIC)
        return self._parse_params_page(text)

    async def async_fetch_setpoints_engine_params(self) -> dict[str, dict[str, str]]:
        """Return the Engine Params Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_ENGINE_PARAMS)
        return self._parse_params_page(text)

    async def async_fetch_setpoints_engine_protect(self) -> dict[str, dict[str, str]]:
        """Return the Engine Protect Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_ENGINE_PROTECT)
        return self._parse_params_page(text)

    async def async_fetch_setpoints_gener_protect(self) -> dict[str, dict[str, str]]:
        """Return the Gener Protect Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_GENER_PROTECT)
        return self._parse_params_page(text)

    async def async_fetch_setpoints_amf_settings(self) -> dict[str, dict[str, str]]:
        """Return the AMF Settings Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_AMF_SETTINGS)
        return self._parse_params_page(text)

    async def async_fetch_setpoints_extension_io(self) -> dict[str, dict[str, str]]:
        """Return the Extension I/O Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_EXTENSION_IO)
        return self._parse_params_page(text)

    async def async_fetch_setpoints_date_time(self) -> dict[str, dict[str, str]]:
        """Return the Date/Time Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_DATE_TIME)
        return self._parse_params_page(text)

    async def async_fetch_setpoints_sensors_spec(self) -> dict[str, dict[str, str]]:
        """Return the Sensors Spec Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_SENSORS_SPEC)
        return self._parse_params_page(text)

    async def async_fetch_setpoints_sms_email(self) -> dict[str, dict[str, str]]:
        """Return the SMS/E-Mail Setpoints group (read-only)."""
        text = await self._async_get_authenticated(PATH_SETPOINTS_SMS_EMAIL)
        return self._parse_params_page(text)

    async def async_fetch_history(self) -> list[dict[str, str]]:
        """Return the visible History event log, most recent first."""
        text = await self._async_get_authenticated(PATH_HISTORY)
        return self._parse_history_page(text)

    async def _async_get_authenticated(self, path: str) -> str:
        """Fetch `path`, logging in first / retrying once on session expiry.

        CONTROL.HTM is special-cased: a successful login's POST response
        body *is* the CONTROL.HTM page, so we reuse it instead of making
        a redundant extra request for that one path.

        Locked for the whole operation - see the note on self._lock in
        __init__ about why two coordinators can't be allowed to
        interleave requests here.
        """
        async with self._lock:
            is_control_page = path.upper() == "CONTROL.HTM"

            if not self._logged_in:
                await self.async_login()
                if is_control_page:
                    return self._last_page

            text = await self._async_get_raw(path)
            if _LOGIN_MARKER in text:
                # Session expired - log in again and use/retry accordingly.
                self._logged_in = False
                await self.async_login()
                if is_control_page:
                    return self._last_page
                text = await self._async_get_raw(path)

            return text

    async def _async_get_raw(self, path: str) -> str:
        try:
            async with self._session.get(f"{self._base_url}/{path}") as resp:
                resp.raise_for_status()
                return await resp.text(encoding="latin-1")
        except aiohttp.ClientError as err:
            raise ComapAmf25ConnectionError(str(err)) from err

    async def async_send_command(self, path: str) -> None:
        """Trigger a control endpoint, e.g. 'start.CMD' or '2.MOD'.

        Locked for the same reason as _async_get_authenticated.
        """
        async with self._lock:
            if not self._logged_in:
                await self.async_login()
            try:
                async with self._session.get(f"{self._base_url}/{path}") as resp:
                    resp.raise_for_status()
                    text = await resp.text(encoding="latin-1")
            except aiohttp.ClientError as err:
                raise ComapAmf25ConnectionError(str(err)) from err

            if _LOGIN_MARKER in text:
                # Session had expired - log in and retry once.
                self._logged_in = False
                await self.async_login()
                async with self._session.get(f"{self._base_url}/{path}") as resp:
                    resp.raise_for_status()

    async def async_logout(self) -> None:
        """Best-effort logout, so the panel doesn't hold onto our slot
        across a Home Assistant restart.

        Confirmed by packet capture: the panel enforces a small limit
        on concurrent logged-in clients, and returns "Too many other
        clients connected" instead of the login page when it's full -
        which is exactly what happens on the next login attempt if the
        previous session was never explicitly ended, since Home
        Assistant restarting closes the TCP connection but never tells
        the panel we're actually done. Errors are swallowed: this runs
        during shutdown, when there's nothing useful to do with a
        failure, and it shouldn't hold up or interrupt shutdown.
        """
        try:
            async with self._lock:
                async with self._session.get(f"{self._base_url}/{CMD_LOGOUT}"):
                    pass
        except aiohttp.ClientError:
            pass
        self._logged_in = False

    @staticmethod
    def _parse(html: str) -> ComapAmf25Data:
        data = ComapAmf25Data()

        for name, value, unit in _VAL_RE.findall(html):
            key = name.strip()
            data.values[key] = {"value": value.strip(), "unit": unit.strip()}

        for icon, low, high, val_class, value, unit in _GAUGE_RE.findall(html):
            info = GAUGE_MAP.get(icon, {"name": icon, "device_class": None})
            data.gauges[icon] = {
                "name": info["name"],
                "device_class": info["device_class"],
                "value": value.strip(),
                "unit": unit.strip(),
                "low": low.strip(),
                "high": high.strip(),
                "fault": val_class == "bvsf",
            }

        mlv = [m.strip() for m in _MLV_RE.findall(html)]
        if len(mlv) >= 3:
            data.status, data.timer_label, data.operation = mlv[0], mlv[1], mlv[2]

        timer_match = _TIMER_RE.search(html)
        if timer_match:
            data.timer_value, data.timer_unit = (
                timer_match.group(1).strip(),
                timer_match.group(2).strip(),
            )

        for idx, css_class, label in _MODE_RE.findall(html):
            if css_class == "rsd":
                data.mode = label.strip()

        alarms = [
            a.strip()
            for _, a in _ALARM_RE.findall(html)
            if a.strip() and a.strip() != "&nbsp;"
        ]
        data.alarms = alarms
        data.alarm_active = len(alarms) > 0

        return data

    @staticmethod
    def _parse_values_page(
        html: str,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Parse a values<n>.htm 'Measurement' group page.

        Returns (analog_values, binary_values). Placeholder "Not Used"
        channels and the raw Bin Inputs/Bin Outputs bitmask rows are
        skipped, since the individual binary rows right below them
        already cover the same information per-channel.
        """
        values: dict[str, dict[str, str]] = {}
        for name, val_class, value, unit in _V_ANALOG_RE.findall(html):
            key = name.strip()
            stripped_value = value.strip()
            if (
                key.lower() in _VALUES_SKIP_NAMES
                or key in values
                or _BITMASK_RE.match(stripped_value)
            ):
                continue
            values[key] = {
                "value": stripped_value,
                "unit": unit.replace("&nbsp;", "").strip(),
                "fault": val_class == "v_sf",
            }

        binary: dict[str, str] = {}
        for name, value in _V_BINARY_RE.findall(html):
            key = name.strip()
            if key.lower() in _VALUES_SKIP_NAMES or key in binary:
                continue
            binary[key] = value.strip()

        return values, binary

    @staticmethod
    def _parse_params_page(page_html: str) -> dict[str, dict[str, str]]:
        """Parse a params<n>.htm Setpoints group page.

        Returns {name: {value, unit, range}}. Unlike the Measurement
        pages, there's no separate binary-row variant seen on Setpoints
        pages so far, and no fault marker either - every value here is
        just a configured parameter, not a live sensor reading.

        Parameter names can contain HTML entities (e.g. "Gen &gt;V Sd"
        for "Gen >V Sd") - html.unescape() decodes those properly,
        rather than leaving literal "&gt;"/"&lt;" text in sensor names
        and unique_ids.
        """
        params: dict[str, dict[str, str]] = {}
        for name, value, unit, param_range in _P_PARAM_RE.findall(page_html):
            key = html.unescape(name.strip())
            if key in params:
                continue
            params[key] = {
                "value": html.unescape(value.strip()),
                "unit": unit.replace("&nbsp;", "").strip(),
                "range": html.unescape(param_range.strip()),
            }
        return params

    @staticmethod
    def _parse_history_page(page_html: str) -> list[dict[str, str]]:
        """Parse history.htm's event log table, most recent event first.

        Each row becomes a dict with keys "Reason", "Time", "Date",
        plus the panel's own abbreviated column names (Mode, RPM, Pwr,
        PF, LChr, Gfrq, Vg1-3, Ig1-3, Vm1-3, Mfrq, UBat, OilP, EngT,
        AI3, BIN, BOUT) - kept as raw strings/abbreviations rather than
        translated, so they can be cross-referenced directly against
        what the panel's own History page shows.

        Reason text can contain HTML entities (e.g. an alarm named
        "Mains &lt;Freq" for "Mains <Freq"), same as Setpoints names.
        """
        rows: list[dict[str, str]] = []
        for reason, time, date, rest_blob in _HISTORY_ROW_RE.findall(page_html):
            values = [v.strip() for v in _HISTORY_TD_RE.findall(rest_blob)]
            row = {
                "Reason": html.unescape(reason.strip()),
                "Time": time.strip(),
                "Date": date.strip(),
            }
            row.update(dict(zip(_HISTORY_COLUMNS, values)))
            rows.append(row)
        return rows
