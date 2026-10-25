# PJLink Advance — Design

Date: 2026-10-25
Status: approved

## 1. Goal

Replace the Home Assistant core `pjlink` integration with a custom component
(same `pjlink` domain) that uses the full PJLink Class 1 and Class 2 protocol,
stays compatible with existing config entries, and is ready for a later
upstream PR.

Reference hardware for real-world testing: **Epson EB-L735U** (laser, Class 2).

## 2. Current state (HA core, `dev` branch)

- A single `media_player` entity: power on/off, audio mute, input selection.
- Synchronous `pypjlink2` library; it opens a new TCP connection for every
  query and command, with no coordinator.
- `warm-up` is reported as `on` and `cooling` as `off`; there are no
  transitional states.
- Errors are detected by comparing strings (`"projector unavailable"`).
- `CONF_ENCODING` is defined but unused.
- No device info (manufacturer, model, serial, firmware).
- Config flow `VERSION = 1`, `data = {host, port, password?}`, title = projector
  name, entity `unique_id` = `entry.entry_id`. No config entry `unique_id`.
  No reauth, reconfigure or options.
- `codeowners: []`.

## 3. Decisions

| Topic | Decision | Reason |
|---|---|---|
| Domain | `pjlink` | Overrides core; upstream becomes an improvement of the existing integration, not a duplicate. |
| Protocol | Own async library, published to PyPI separately | HA core requires protocol code outside the integration. |
| Why not `aiopjlink`? | It exists (MIT, Class 1+2) but its roadmap leaves Search (§3.2) and Status Notification (§3.3) pending. | We need push, discovery, transparent reconnect and an emulator under our control. Its code is a useful reference. |
| Package name | `pjlink-advance` (import `pjlink_advance`); free on PyPI as of 2026-10-25 | `aiopjlink` and `aiopjlink2` are taken. |
| Update model | Polling (baseline) + Class 2 UDP push (optional) | Push depends on the network (same subnet, host networking). |
| `iot_class` | `local_polling` | Polling always works; push is an enhancement. |

## 4. Repository layout

```
PJLink Advance/
├── lib/                         # PyPI package pjlink-advance
│   ├── pyproject.toml
│   ├── src/pjlink_advance/
│   │   ├── __init__.py
│   │   ├── protocol.py          # pure encode/decode, no I/O
│   │   ├── client.py            # PJLinkClient (async TCP)
│   │   ├── listener.py          # UDP: search + notifications
│   │   ├── models.py            # dataclasses and enums
│   │   ├── errors.py
│   │   └── emulator.py          # fake TCP+UDP projector for tests
│   └── tests/
├── custom_components/pjlink/    # HA integration
├── tests/                       # integration tests
├── hacs.json
└── docs/
```

During development the integration installs the library from the repo; on
release `requirements: ["pjlink-advance==X.Y.Z"]` is pinned.

## 5. Library `pjlink_advance`

No Home Assistant dependency. Python ≥ 3.13, typed, plain `asyncio`.

### 5.1 `protocol.py` (pure)

- Builds requests: `%1POWR ?\r`, `%2INPT 31\r`, etc.
- Parses responses: `%1POWR=1`, `ERR1..ERR4`, `ERRA`.
- Authentication: greeting `PJLINK 0` (no password) or `PJLINK 1 <random>`;
  digest = `md5(random + password)` prepended to the first command.
- Decodes with the configured `encoding` (default `utf-8`, `latin-1`
  fallback).

### 5.2 `client.py` — `PJLinkClient`

- `PJLinkClient(host, port=4352, password=None, encoding="utf-8", timeout=5.0)`.
- One persistent TCP connection guarded by an `asyncio.Lock`; commands are
  serialized. The projector closes the session after ~30 s of inactivity: the
  client reconnects and re-authenticates transparently (one retry), and
  reconnects proactively after 25 s idle.
- High-level API:

| Class | Method | Command |
|---|---|---|
| 1 | `get_power()` / `set_power(bool)` | POWR |
| 1 | `get_input()` / `set_input(InputSource)` / `get_inputs()` | INPT / INST |
| 1 | `get_av_mute()` / `set_av_mute(MuteTarget, bool)` | AVMT |
| 1 | `get_errors()` | ERST |
| 1 | `get_lamps()` | LAMP |
| 1 | `get_name()`, `get_manufacturer()`, `get_model()`, `get_other_info()`, `get_class()` | NAME, INF1, INF2, INFO, CLSS |
| 2 | `get_serial()`, `get_software_version()` | SNUM, SVER |
| 2 | `get_input_name(InputSource)` | INNM |
| 2 | `get_input_resolution()`, `get_recommended_resolution()` | IRES, RRES |
| 2 | `get_filter_hours()` | FILT |
| 2 | `get_lamp_model()`, `get_filter_model()` | RLMP, RFIL |
| 2 | `speaker_volume(up: bool)`, `mic_volume(up: bool)` | SVOL, MVOL |
| 2 | `get_freeze()` / `set_freeze(bool)` | FREZ |
| — | `send_raw(command, param, pj_class)` | any |

- `async fetch_static()` → `DeviceInfo` (name, manufacturer, model, info,
  class, serial, software, inputs with names).
- `async fetch_state(full: bool)` → `ProjectorState` (power, input, mute,
  freeze, resolutions; `full=True` also reads errors, lamps and filter).

### 5.3 Models (`models.py`)

- `PowerState`: `OFF`, `ON`, `COOLING`, `WARMING`.
- `InputSource(type: InputType, index: str)` with `InputType` RGB=1, VIDEO=2,
  DIGITAL=3, STORAGE=4, NETWORK=5, INTERNAL=6 (Class 2); index `1-9`
  (Class 1) or `1-9A-Z` (Class 2).
- `MuteState(video: bool, audio: bool)`; `MuteTarget`: `VIDEO`=1, `AUDIO`=2,
  `BOTH`=3 (AVMT `11/10`, `21/20`, `31/30`).
- `ErrorStatus`: `fan`, `lamp`, `temperature`, `cover`, `filter`, `other`,
  each `OK | WARNING | ERROR`.
- `Lamp(hours: int, on: bool)`; a list of 1 to 8.
- `Resolution(width, height)` | `NO_SIGNAL` | `UNKNOWN`.

### 5.4 Errors (`errors.py`)

`PJLinkError` base and subclasses: `PJLinkConnectionError`, `PJLinkAuthError`
(`ERRA`), `PJLinkUndefinedCommand` (`ERR1`), `PJLinkOutOfParameter` (`ERR2`),
`PJLinkUnavailableTime` (`ERR3`), `PJLinkProjectorFailure` (`ERR4`),
`PJLinkProtocolError` (malformed response).

### 5.5 `listener.py` — Class 2 UDP

- `PJLinkListener`: one UDP socket on port 4352 shared by every projector.
- `async discover(duration=5)` → broadcasts `%2SRCH` and returns
  `[(ip, mac)]` from `%2ACKN=<mac>` replies.
- Notifications: `%2LKUP`, `%2ERST`, `%2POWR`, `%2INPT`, `%2AVMT`, delivered to
  callbacks registered per source IP.

### 5.6 `emulator.py`

Fake projector (TCP + UDP) with configurable state, class 1 or 2, optional
password, and fault injection (`ERR3` during warm-up, disconnects, silent
responses) plus notification sending. Used by the library and integration
tests, and for development without hardware.

## 6. Integration `custom_components/pjlink`

### 6.1 Config flow

- **user**: menu with "Search the network" (Class 2, uses `discover()`) and
  "Enter the address manually" (host, port 4352, optional password). Both
  validate by connecting and reading the static information.
- Config entry `unique_id`: serial number (Class 2), else MAC if known, else
  `host:port`.
- **reauth**: on `PJLinkAuthError` at runtime.
- **reconfigure**: change host, port or password without removing the entry.
- **options**: polling interval (default 30 s), lamp and filter hour thresholds
  for repair issues, enable push.

### 6.2 Migration from core

- The config flow moves to `VERSION = 2`.
- `async_migrate_entry` (1 → 2): keeps `data` and title, adds
  `encoding = "utf-8"`; the entry `unique_id` is set on the first successful
  setup.
- The `media_player` keeps `unique_id = entry.entry_id` so automations and
  `entity_id`s keep working. New entities use `f"{entry.entry_id}_{key}"`.
- YAML import (`async_step_import`) stays while core keeps it.

### 6.3 Coordinator

- `PJLinkCoordinator(DataUpdateCoordinator[ProjectorState])`, one client per
  entry.
- Static information is read at setup, and again on an off → on transition if
  no inputs were listed.
- Adaptive polling: 5 s during `WARMING`/`COOLING`; the configured interval
  otherwise. Errors, lamps and filter are read every 5 min or after an `ERST`
  notification.
- `PJLinkUnavailableTime` (`ERR3`) is not a failure: the last values are kept
  and entities stay available.
- `PJLinkConnectionError` → `UpdateFailed` (entities unavailable).
- `PJLinkAuthError` → `ConfigEntryAuthFailed` (starts reauth).
- `PJLinkUndefinedCommand` on probed optional commands → marked unsupported
  and the entity is not created.
- With push active, each notification updates the state through
  `async_set_updated_data` and polling falls back to every 5 min.
- After each command the coordinator refreshes immediately.

### 6.4 Entities

All with `has_entity_name = True`, `translation_key` and icons in
`icons.json`. Class 2 entities are only created when `CLSS = 2` and the command
answers.

| Platform | Entity | Class | Notes |
|---|---|---|---|
| media_player | Projector | 1 | on/off, source (with INNM names when available), audio mute; `volume_up`/`volume_down` on Class 2 |
| sensor | Power status | 1 | enum off/warming/on/cooling |
| sensor | Lamp N hours | 1 | `UnitOfTime.HOURS`, `TOTAL_INCREASING`; one per lamp (on the laser Epson it is the light source) |
| sensor | Filter hours | 2 | `UnitOfTime.HOURS`, `TOTAL_INCREASING` |
| sensor | Fan / Lamp / Temperature / Cover / Filter / Other status | 1 | enum ok/warning/error |
| sensor | Input resolution | 2 | text `1920x1200`, `no_signal` |
| sensor | Recommended resolution | 2 | diagnostic, disabled by default |
| sensor | Replacement lamp / filter | 2 | diagnostic |
| binary_sensor | Problem | 1 | `PROBLEM`, `on` if any ERST category is not ok |
| switch | Video mute (blank screen) | 1 | AVMT 11/10 |
| switch | Freeze image | 2 | FREZ |
| button | Microphone volume +/− | 2 | disabled by default |

Device info: manufacturer (INF1), model (INF2), `serial_number` (SNUM),
`sw_version` (SVER), name (entry title), MAC connection when known.

### 6.5 Actions

- `pjlink.send_command`: `config_entry_id`, `command`, `parameter`,
  `pj_class` (1/2). Returns the response (`SupportsResponse.OPTIONAL`). For
  vendor features not covered elsewhere.

### 6.6 Repair issues

- Lamp hours ≥ threshold → warning, with the replacement model if RLMP has one.
- Filter hours ≥ threshold → warning, with RFIL.
- Any ERST category in `ERROR` → error issue until it clears.
- Issues are removed automatically when the condition goes away, and when the
  entry is removed.

### 6.7 Diagnostics and translations

- `diagnostics.py`: static information, last state and options; password,
  serial and unique id redacted.
- `strings.json` + `translations/en.json` + `translations/es.json`.
- Failed commands → `HomeAssistantError` with a `translation_key`.

## 7. Epson EB-L735U notes (to verify on hardware)

- To answer in standby it needs standby communication enabled (an option like
  "Standby Mode: Communication On"; the exact name depends on the
  menu/firmware). Otherwise it shows as unavailable while off.
- It is a laser projector: `LAMP` returns light source hours and lamp
  replacement does not apply (empty RLMP or `ERR1`).
- It answers `ERR3` to several commands during warm-up.
- Check which Class 2 commands it supports: INNM, IRES, FREZ, FILT, SVOL, and
  whether it sends notifications.
- Epson also offers ESC/VP.net (TCP 3629) with extra features (picture mode,
  lens). **Out of scope** for this version; possibly a separate integration.

## 8. Testing

- Library: `pytest` + `pytest-asyncio`. Unit tests for the codec with protocol
  vectors and integration tests against the emulator: auth, reconnect,
  timeouts, ERR1–ERR4, push and discovery.
- Integration: `pytest-homeassistant-custom-component` with the emulator as a
  fixture: config flow (user, search, reauth, reconfigure, options), v1→v2
  migration with a core-shaped entry, entities per class, repair issues and
  diagnostics.
- Manual: checklist on the Epson L735U, including push with HA on host
  networking.
- CI: GitHub Actions with ruff, pytest, hassfest and HACS validation.

## 9. Phases

1. **Library + emulator**: protocol, client, models, errors, emulator and
   Class 1 and 2 tests (no UDP).
2. **Base integration**: config flow, migration, coordinator, media_player,
   sensors, video mute switch, device info, diagnostics and translations.
3. **Class 2 extras**: input names, freeze, volume, resolutions, replacement
   parts, microphone buttons, `send_command` action and repair issues.
4. **UDP**: discovery in the config flow and push notifications.
5. **Distribution**: HACS, README, CI, PyPI publishing and upstream PR prep (in
   steps: first the library + parity, then platforms).

## 10. Out of scope

- ESC/VP.net or other proprietary protocols.
- Lens and shutter control (not part of PJLink).
- Custom scenes or Lovelace cards.
