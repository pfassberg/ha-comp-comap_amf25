# ComAp AMF 25 Genset Controller for Home Assistant

A custom Home Assistant integration for the ComAp AMF 25 genset (SCADA)
controller's built-in web GUI (the "IB-Lite" panel). It logs in, polls
the panel's own status pages, and exposes everything as Home Assistant
entities — so you don't need to open the panel's web GUI to check on
your genset.

## What it exposes

- **Scada page**: generator/mains electrical values, the four gauges
  (oil pressure, engine temperature, fuel level, battery voltage),
  status/operation/timer, alarm state, a mode select (OFF/MAN/AUT/TEST),
  and command buttons (start/stop, breaker toggles, alarm reset).
- **Measurement** (all 8 groups): Engine, Generator, Mains,
  Controller I/O, Extension I/O, Statistics, IL Info, Date/Time.
- **Setpoints** (all 9 groups), exposed **read-only** — this
  integration never writes configuration back to the panel.
- **History**: the panel's event log, as a single "History Last Event"
  sensor with the full log kept as an attribute.
- A calculated **Time Diff** sensor comparing the panel's clock against
  Home Assistant's own.

Two independent polling cycles, both configurable from the integration's
**Configure** screen:
- Scada + Measurement: fast cycle, default 10s.
- Setpoints + History + Date/Time: slow cycle, default 30 min — these
  don't change often enough to justify fast polling, and Date/Time in
  particular changes on every poll by definition, which would otherwise
  flood Home Assistant's history/logbook.

## A few things worth knowing

- **`PasswordDecode`** (on the IL Info Measurement page) is deliberately
  excluded — its name and raw value look like they could encode
  credential-related information for a password-protected panel.
- A handful of binary sensors (Emergency Stop, breaker feedback,
  breaker open/close outputs) are left without a `problem`/`safety`
  device class, since their 0/1 polarity isn't confirmed from the
  panel's HTML alone — worth checking against your actual panel state
  before automating on them.
- The panel's embedded web server appears to be fairly primitive
  (no reliable connection reuse), so this integration uses its own
  private, non-keep-alive HTTP session rather than Home Assistant's
  shared one.

## Installation

### Via HACS (custom repository)

1. HACS → the "⋮" menu (top right) → **Custom repositories**.
2. Add this repository's URL, category **Integration**.
3. Find **ComAp AMF 25 Genset Controller** in HACS and install it.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/comap_amf25` into your Home Assistant
   config's `custom_components/` folder.
2. Restart Home Assistant.

### Setup

Settings → Devices & Services → Add Integration → search
**ComAp AMF 25**. Enter the panel's IP address and its access code
(a numeric password, `0` by default on some configurations).

## Configuration

Settings → Devices & Services → ComAp AMF 25 → **Configure** lets you
adjust both polling intervals without re-adding the integration.

## License

No license file included yet — add one appropriate for how you'd like
to share this (e.g. MIT) before publishing publicly.
