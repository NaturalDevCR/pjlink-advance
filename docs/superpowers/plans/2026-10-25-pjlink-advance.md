# PJLink Advance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `pjlink-advance` async PJLink library (Class 1 + 2, with emulator) and a `pjlink` Home Assistant custom component that replaces the core integration.

**Architecture:** Two units in one repo. `lib/` is a pure-asyncio library with no HA dependency: a pure codec (`protocol.py`), a persistent TCP client (`client.py`), a UDP listener (`listener.py`) and a projector emulator used by every test. `custom_components/pjlink/` consumes only the library's public API through a `DataUpdateCoordinator`.

**Tech Stack:** Python 3.14, uv, hatchling, pytest + pytest-asyncio, ruff, Home Assistant 2026.10, pytest-homeassistant-custom-component.

**Spec:** `docs/superpowers/specs/2026-10-25-pjlink-advance-design.md`

## Global Constraints

- Integration domain: `pjlink`. Library distribution: `pjlink-advance`, import `pjlink_advance`.
- Library has zero runtime dependencies and no Home Assistant import.
- Default port `4352`, default encoding `utf-8`, default timeout `5.0` s.
- Config flow `VERSION = 2`; core entries are `VERSION = 1` with `data = {host, port, password?}`.
- `media_player` keeps `unique_id = entry.entry_id`; other entities use `f"{entry.entry_id}_{key}"`.
- `iot_class: local_polling`. Default scan interval 30 s; transitional (warming/cooling) 5 s; slow data (errors, lamps, filter) every 300 s.
- Class 2 entities are created only when `CLSS` reports 2 and the query does not answer `ERR1`.
- UI strings in English (`strings.json`, `translations/en.json`) and Spanish (`translations/es.json`).
- Do not touch the user's Home Assistant instance. Work only inside this repository.

## File Map

```
lib/pyproject.toml
lib/src/pjlink_advance/__init__.py     public exports
lib/src/pjlink_advance/errors.py       exception hierarchy
lib/src/pjlink_advance/models.py       enums + dataclasses
lib/src/pjlink_advance/protocol.py     pure encode/decode + auth digest
lib/src/pjlink_advance/client.py       PJLinkClient
lib/src/pjlink_advance/listener.py     PJLinkListener (UDP search + notify)
lib/src/pjlink_advance/emulator.py     PJLinkEmulator (TCP + UDP)
lib/tests/test_models.py
lib/tests/test_protocol.py
lib/tests/test_emulator.py
lib/tests/test_client.py
lib/tests/test_listener.py
custom_components/pjlink/manifest.json
custom_components/pjlink/const.py
custom_components/pjlink/__init__.py        setup/unload/migrate, push wiring
custom_components/pjlink/coordinator.py     PJLinkCoordinator
custom_components/pjlink/entity.py          PJLinkEntity base (device info)
custom_components/pjlink/config_flow.py     user/search/import/reauth/reconfigure/options
custom_components/pjlink/media_player.py
custom_components/pjlink/sensor.py
custom_components/pjlink/binary_sensor.py
custom_components/pjlink/switch.py
custom_components/pjlink/button.py
custom_components/pjlink/services.py        pjlink.send_command
custom_components/pjlink/services.yaml
custom_components/pjlink/repairs.py         lamp/filter/error issues
custom_components/pjlink/diagnostics.py
custom_components/pjlink/strings.json
custom_components/pjlink/icons.json
custom_components/pjlink/translations/en.json
custom_components/pjlink/translations/es.json
tests/conftest.py                            emulator fixture, enable custom integrations
tests/test_config_flow.py
tests/test_init.py                           setup, migration, unload, auth failure
tests/test_media_player.py
tests/test_sensor.py
tests/test_binary_sensor.py
tests/test_switch_button.py
tests/test_services.py
tests/test_repairs.py
tests/test_diagnostics.py
pyproject.toml                               dev env for integration tests
hacs.json
README.md
.github/workflows/ci.yml
```

---

## Part A — Library

### Task 1: Library scaffold, errors and models

**Files:**
- Create: `lib/pyproject.toml`, `lib/src/pjlink_advance/{__init__,errors,models}.py`
- Test: `lib/tests/test_models.py`

**Interfaces — Produces:**
- `errors`: `PJLinkError(Exception)`; subclasses `PJLinkConnectionError`, `PJLinkAuthError`, `PJLinkProtocolError`, and `PJLinkCommandError(PJLinkError)` with attribute `code: str` and subclasses `PJLinkUndefinedCommand` (ERR1), `PJLinkOutOfParameter` (ERR2), `PJLinkUnavailableTime` (ERR3), `PJLinkProjectorFailure` (ERR4).
- `models`:
  - `PowerState(StrEnum)`: `OFF="off"`, `ON="on"`, `COOLING="cooling"`, `WARMING="warming"`.
  - `InputType(IntEnum)`: `RGB=1 VIDEO=2 DIGITAL=3 STORAGE=4 NETWORK=5 INTERNAL=6`.
  - `InputSource(frozen dataclass)`: `type: InputType`, `index: str`; property `code -> str` (e.g. `"31"`); classmethod `from_code(code: str) -> InputSource`; `__str__` → `"DIGITAL 1"`.
  - `MuteTarget(IntEnum)`: `VIDEO=1 AUDIO=2 BOTH=3`.
  - `MuteState(frozen dataclass)`: `video: bool`, `audio: bool`.
  - `ErrorLevel(StrEnum)`: `OK="ok" WARNING="warning" ERROR="error"`.
  - `ErrorStatus(frozen dataclass)`: `fan, lamp, temperature, cover, filter, other: ErrorLevel`; property `has_problem -> bool`; classmethod `ok()`.
  - `Lamp(frozen dataclass)`: `hours: int`, `on: bool`.
  - `Resolution(frozen dataclass)`: `width: int`, `height: int`; `__str__` → `"1920x1200"`.
  - Sentinels `NO_SIGNAL = "no_signal"`, `UNKNOWN_RESOLUTION = "unknown"`; type `ResolutionValue = Resolution | str | None`.
  - `DeviceInfo(dataclass)`: `name, manufacturer, model, other_info: str`, `pj_class: int`, `serial: str | None`, `software_version: str | None`, `inputs: list[InputSource]`, `input_names: dict[str, str]` (code → name), `lamp_model: str | None`, `filter_model: str | None`, `recommended_resolution: ResolutionValue`.
  - `ProjectorState(dataclass)`: `power: PowerState`, `input: InputSource | None`, `mute: MuteState | None`, `freeze: bool | None`, `input_resolution: ResolutionValue`, `errors: ErrorStatus | None`, `lamps: list[Lamp] | None`, `filter_hours: int | None`.

- [x] Step 1: write `test_models.py` covering `InputSource.from_code("3A")` → `InputSource(DIGITAL, "A")`, `.code`, `str()`, invalid code raises `ValueError`; `ErrorStatus.has_problem`; `Resolution` str.
- [x] Step 2: `cd lib && uv run --extra test pytest tests/test_models.py` → FAIL (import error).
- [x] Step 3: implement `errors.py`, `models.py`, `pyproject.toml` (hatchling, `requires-python >=3.13`, optional extra `test = [pytest, pytest-asyncio]`, `asyncio_mode = "auto"`).
- [x] Step 4: rerun → PASS.
- [x] Step 5: commit `feat(lib): add models and errors`.

### Task 2: Protocol codec

**Files:** Create `lib/src/pjlink_advance/protocol.py`; Test `lib/tests/test_protocol.py`.

**Interfaces — Produces:**
- `PORT = 4352`, `MAX_RESPONSE = 136`.
- `encode_command(pj_class: int, command: str, param: str = "?") -> bytes` → `b"%1POWR ?\r"`; validates 4-char uppercase command, class in {1,2}.
- `auth_digest(salt: str, password: str) -> str` → md5 hex of `salt + password`.
- `parse_greeting(line: str) -> str | None` → `None` for `"PJLINK 0"`, salt for `"PJLINK 1 498e4a67"`; raises `PJLinkAuthError` for `"PJLINK ERRA"`, `PJLinkProtocolError` otherwise.
- `parse_response(line: str, pj_class: int, command: str) -> str` → value after `=`; raises `PJLinkAuthError` for `PJLINK ERRA`, `PJLinkCommandError` subclass for `ERR1..ERR4`, `PJLinkProtocolError` for mismatched header.
- Value decoders: `decode_power(v) -> PowerState` (`0..3`), `decode_input(v) -> InputSource`, `decode_inputs(v) -> list[InputSource]` (space separated, empty → `[]`), `decode_av_mute(v) -> MuteState` (`11`, `21`, `31`, `30`, `10`, `20`), `decode_errors(v) -> ErrorStatus` (6 digits), `decode_lamps(v) -> list[Lamp]` (pairs), `decode_resolution(v) -> ResolutionValue` (`"-"` → `NO_SIGNAL`, `"*"` → `UNKNOWN_RESOLUTION`, `WxH`), `decode_bool(v) -> bool` (`"0"/"1"`), `decode_int(v) -> int`.
- `parse_notification(line: str) -> tuple[str, str] | None` → `("POWR", "1")` for `"%2POWR=1"`, `("LKUP", mac)`; `None` for non-notification.
- `parse_ack(line: str) -> str | None` → MAC for `"%2ACKN=00:11:22:33:44:55"`.

- [x] Step 1: tests with spec vectors for every decoder, `ERR1..ERR4` mapping, `PJLINK ERRA`, digest vector (`auth_digest("498e4a67", "JBMIAProjectorLink") == "5d8409bc1c3fa39749434aa3a5c38682"`), malformed lines.
- [x] Step 2: run → FAIL.
- [x] Step 3: implement.
- [x] Step 4: run → PASS.
- [x] Step 5: commit `feat(lib): add PJLink protocol codec`.

### Task 3: Emulator (TCP)

**Files:** Create `lib/src/pjlink_advance/emulator.py`; Test `lib/tests/test_emulator.py`.

**Interfaces — Produces:**
- `EmulatorState(dataclass)` with defaults: `power=PowerState.OFF`, `input=InputSource(DIGITAL,"1")`, `inputs=[11, 31, 32, 51 as InputSource]`, `input_names={"11": "Computer", "31": "HDMI1", "32": "HDMI2", "51": "LAN"}`, `mute=MuteState(False, False)`, `freeze=False`, `errors=ErrorStatus.ok()`, `lamps=[Lamp(1200, False)]`, `filter_hours=300`, `name="EB-L735U"`, `manufacturer="EPSON"`, `model="EB-L735U"`, `other_info=""`, `serial="X8JK0000001"`, `software_version="1.00"`, `input_resolution=Resolution(1920,1200)`, `recommended_resolution=Resolution(1920,1200)`, `lamp_model=""`, `filter_model="ELPAF60"`, `mac="00:26:ab:00:00:01"`.
- `PJLinkEmulator(state: EmulatorState | None = None, *, pj_class: int = 2, password: str | None = None, host="127.0.0.1", port=0, idle_timeout: float = 30.0)`:
  - `async start()`, `async stop()`, `async __aenter__/__aexit__`, property `port -> int`.
  - `unsupported: set[str]` commands answering `ERR1`.
  - `unavailable: set[str]` commands answering `ERR3`.
  - `fail_next: str | None` forces the next response (e.g. `"ERR4"`).
  - `drop_after_greeting: bool` closes connection after greeting (simulates network failure).
  - `connections: int` counter of accepted TCP connections.
  - `received: list[str]` log of commands.
  - Behaviour: `set POWR 1` when off → `WARMING`; `transition_delay` (default 0) after which → `ON`; `POWR 0` → `COOLING` then `OFF`. Class-1-only emulator answers `ERR1` to `%2` commands except it still answers `%1CLSS ?` with `1`.

- [x] Step 1: tests: greeting without password `PJLINK 0`; with password `PJLINK 1 <8 hex>`, wrong digest → `PJLINK ERRA`; `%1POWR ?` → `%1POWR=0`; set/query round trip for INPT, AVMT, FREZ; unsupported → ERR1; idle timeout closes socket.
- [x] Step 2: run → FAIL.
- [x] Step 3: implement with `asyncio.start_server`.
- [x] Step 4: run → PASS.
- [x] Step 5: commit `feat(lib): add PJLink projector emulator`.

### Task 4: Client

**Files:** Create `lib/src/pjlink_advance/client.py`; Test `lib/tests/test_client.py`.

**Interfaces — Consumes:** Task 1–3. **Produces:**
- `PJLinkClient(host: str, port: int = 4352, password: str | None = None, *, encoding: str = "utf-8", timeout: float = 5.0)`.
- `async connect()`, `async close()`, async context manager, property `connected -> bool`.
- `async command(pj_class: int, command: str, param: str = "?") -> str`: serialized by `asyncio.Lock`; lazily connects; on `ConnectionError`/EOF/timeout during an exchange reconnects once and retries; timeouts → `PJLinkConnectionError`.
- High level: `get_power() -> PowerState`, `set_power(on: bool)`, `get_input() -> InputSource`, `set_input(src: InputSource)` (uses class 2 when `src.index` is not a digit or type is INTERNAL), `get_inputs() -> list[InputSource]`, `get_av_mute() -> MuteState`, `set_av_mute(target: MuteTarget, on: bool)`, `get_errors() -> ErrorStatus`, `get_lamps() -> list[Lamp]`, `get_name/get_manufacturer/get_model/get_other_info() -> str`, `get_class() -> int`, `get_serial/get_software_version() -> str`, `get_input_name(src) -> str`, `get_input_resolution/get_recommended_resolution() -> ResolutionValue`, `get_filter_hours() -> int`, `get_lamp_model/get_filter_model() -> str`, `speaker_volume(up: bool)`, `mic_volume(up: bool)`, `get_freeze() -> bool`, `set_freeze(on: bool)`, `send_raw(command, param="?", pj_class=1) -> str`.
- `async fetch_static() -> DeviceInfo`: tolerant — optional Class 2 queries catch `PJLinkCommandError` and leave `None`/omit names.
- `async fetch_state(*, full: bool, pj_class: int) -> ProjectorState`: always power; when power is ON/WARMING also input, mute, freeze (class 2), input resolution (class 2); `full=True` adds errors, lamps, filter (class 2). `PJLinkUnavailableTime` on optional fields → `None`.

- [x] Step 1: tests against emulator: power round trip; auth success/failure (`PJLinkAuthError`); persistent connection (`emulator.connections == 1` after 5 commands); reconnect after emulator idle timeout; `ERR3` → `PJLinkUnavailableTime`; `fetch_static` on class 1 emulator leaves Class 2 fields `None`; `fetch_state(full=True)` populated; timeout against a server that never answers → `PJLinkConnectionError`; non-UTF-8 name decodes via `latin-1` fallback.
- [x] Step 2: run → FAIL.
- [x] Step 3: implement.
- [x] Step 4: run → PASS.
- [x] Step 5: commit `feat(lib): add async PJLink client`.

### Task 5: UDP listener (search + notifications)

**Files:** Create `lib/src/pjlink_advance/listener.py`; modify `emulator.py` (UDP: answer `%2SRCH` with `%2ACKN=<mac>`, `async notify(command, value, target)` sends `%2POWR=1` etc. to `target`); Test `lib/tests/test_listener.py`.

**Interfaces — Produces:**
- `PJLinkListener(*, host="0.0.0.0", port=4352)`: `async start()`, `async stop()`, `register(ip: str, callback: Callable[[str, str], None]) -> Callable[[], None]` (returns unregister), `async discover(timeout: float = 5.0, *, broadcast: str = "255.255.255.255", target_port: int = 4352) -> list[tuple[str, str]]` returning `(ip, mac)`.
- Emulator: `udp_port` property; `async notify(command: str, value: str, target: tuple[str, int])`.

- [x] Step 1: tests (listener on port 0, emulator UDP on port 0, unicast `broadcast="127.0.0.1"`): discover returns `[("127.0.0.1", mac)]`; notification `POWR=1` reaches registered callback; unregister stops delivery; messages from unknown IP ignored.
- [x] Step 2: run → FAIL.
- [x] Step 3: implement with `loop.create_datagram_endpoint` (`SO_BROADCAST`, `reuse_port` where available).
- [x] Step 4: run → PASS. Full lib suite + `ruff check`.
- [x] Step 5: commit `feat(lib): add UDP search and status notifications`.

---

## Part B — Integration

### Task 6: Integration scaffold + coordinator

**Files:** Create root `pyproject.toml` (dev deps: `pytest-homeassistant-custom-component`, editable `lib`), `custom_components/pjlink/{manifest.json,const.py,__init__.py,coordinator.py,entity.py}`, `tests/conftest.py`, `tests/test_init.py`.

**Interfaces — Produces:**
- `const.py`: `DOMAIN="pjlink"`, `CONF_ENCODING="encoding"`, `DEFAULT_PORT=4352`, `DEFAULT_ENCODING="utf-8"`, `CONF_SCAN_INTERVAL="scan_interval"`, `CONF_LAMP_WARNING_HOURS="lamp_warning_hours"`, `CONF_FILTER_WARNING_HOURS="filter_warning_hours"`, `CONF_PUSH="push"`, `DEFAULT_SCAN_INTERVAL=30`, `TRANSITION_INTERVAL=5`, `SLOW_INTERVAL=300`, `DEFAULT_LAMP_WARNING_HOURS=20000`, `DEFAULT_FILTER_WARNING_HOURS=0` (0 = disabled).
- `type PJLinkConfigEntry = ConfigEntry[PJLinkCoordinator]`.
- `PJLinkCoordinator(hass, entry, client)`: `device: DeviceInfo`, `unsupported: set[str]`, `async _async_setup()` (fetch static), `async _async_update_data() -> ProjectorState`, `async async_command(fn)` (wraps errors into `HomeAssistantError` with translation key `command_failed`, then requests refresh), `handle_notification(command, value)`.
- `PJLinkEntity(CoordinatorEntity[PJLinkCoordinator])`: `_attr_has_entity_name = True`, `device_info` from `coordinator.device`, `unique_id = f"{entry_id}_{key}"` unless overridden.
- `__init__.py`: `async_setup_entry`, `async_unload_entry`, `async_migrate_entry` (1 → 2: add `encoding`), platforms list.

- [x] Step 1: tests: setup with emulator → `ConfigEntryState.LOADED`; unreachable host → `SETUP_RETRY`; wrong password → `SETUP_ERROR` + reauth flow started; migration of a v1 entry keeps data and sets version 2; unload closes client.
- [x] Step 2: run → FAIL.
- [x] Step 3: implement.
- [x] Step 4: run → PASS.
- [x] Step 5: commit `feat: add integration scaffold and coordinator`.

### Task 7: Config flow

**Files:** Create `custom_components/pjlink/config_flow.py`, `strings.json`, `translations/en.json`, `translations/es.json`; Test `tests/test_config_flow.py`.

**Interfaces — Produces:** `PJLinkConfigFlow(VERSION=2)` steps: `user` (menu: `search`, `manual`), `search` (runs `PJLinkListener.discover`, select from results or abort `no_devices_found`), `manual` (host/port/password), `import` (YAML, kept for compatibility), `reauth_confirm`, `reconfigure`. Unique id: serial → mac → `host:port`. `PJLinkOptionsFlow`: scan interval (10–300), lamp warning hours, filter warning hours, push toggle. Errors `cannot_connect`, `invalid_auth`, `unknown`.

- [x] Step 1: tests: manual success creates entry titled with projector name and unique id = serial; duplicate aborts `already_configured`; cannot_connect; invalid_auth; search with patched discover; search empty aborts; reauth updates password; reconfigure updates host; options saved; import creates entry.
- [x] Step 2: run → FAIL.
- [x] Step 3: implement.
- [x] Step 4: run → PASS.
- [x] Step 5: commit `feat: add config, reauth, reconfigure and options flows`.

### Task 8: media_player

**Files:** Create `custom_components/pjlink/media_player.py`; Test `tests/test_media_player.py`.

- Features: TURN_ON, TURN_OFF, SELECT_SOURCE, VOLUME_MUTE; VOLUME_STEP when class 2 and `SVOL` not unsupported.
- State ON when power ON/WARMING, OFF otherwise. Source list uses Class 2 input names when present (`"HDMI1"`), else `str(InputSource)`; duplicates disambiguated with code suffix.
- `unique_id = entry.entry_id`, `name=None` (device name).

- [x] Steps: test (state, turn on/off reaches emulator, select source by friendly name, mute, volume up sends `%2SVOL 1`, command failure raises `HomeAssistantError`) → FAIL → implement → PASS → commit `feat: add media player`.

### Task 9: sensor + binary_sensor

**Files:** Create `sensor.py`, `binary_sensor.py`; Tests `tests/test_sensor.py`, `tests/test_binary_sensor.py`.

- Sensors (`SensorEntityDescription` with `value_fn`): `power_status` (enum), `lamp_{n}_hours` (one per lamp, `HOURS`, `TOTAL_INCREASING`), `filter_hours` (class 2), `error_{fan,lamp,temperature,cover,filter,other}` (enum, entity_category diagnostic), `input_resolution` (class 2), `recommended_resolution` (class 2, diagnostic, disabled), `lamp_model`, `filter_model` (class 2, diagnostic, only if non-empty).
- Binary sensor `problem` (`BinarySensorDeviceClass.PROBLEM`).

- [x] Steps: test values from emulator, class 1 emulator creates no class 2 sensors, problem turns on when emulator error set → FAIL → implement → PASS → commit `feat: add sensors and problem binary sensor`.

### Task 10: switch + button

**Files:** Create `switch.py`, `button.py`; Test `tests/test_switch_button.py`.

- Switches: `video_mute` (AVMT target VIDEO), `freeze` (class 2). Unavailable while projector is not ON.
- Buttons: `mic_volume_up`, `mic_volume_down` (class 2, disabled by default, skipped when `MVOL` unsupported).

- [x] Steps: test toggles reach emulator state, class 1 lacks freeze → FAIL → implement → PASS → commit `feat: add video mute, freeze and mic volume controls`.

### Task 11: send_command action, repairs, diagnostics

**Files:** Create `services.py`, `services.yaml`, `repairs.py`, `diagnostics.py`, `icons.json`; Tests `tests/test_services.py`, `tests/test_repairs.py`, `tests/test_diagnostics.py`.

- `pjlink.send_command` fields: `config_entry_id` (required, config_entry selector), `command` (4 letters), `parameter` (default `?`), `pj_class` (1|2, default 1). Returns `{"response": str}`. Registered in `async_setup`.
- Repairs (`issue_registry`, not fixable): `lamp_hours_{entry_id}` when max lamp hours ≥ option (if option > 0), `filter_hours_{entry_id}` likewise, `projector_error_{entry_id}` when any ERST category is ERROR. Deleted when the condition clears. Evaluated on each coordinator update.
- Diagnostics: entry data with password redacted, options, `asdict(device)`, `asdict(state)`.

- [x] Steps: tests → FAIL → implement → PASS → commit `feat: add send_command action, repairs and diagnostics`.

### Task 12: Push notifications

**Files:** Modify `__init__.py`, `coordinator.py`; Test `tests/test_init.py` (push section).

- One `PJLinkListener` per HA instance in `hass.data[DOMAIN]` (ref-counted, started when the first entry with `push=True` loads, stopped when the last unloads). If binding fails, log a warning and keep polling.
- `coordinator.handle_notification`: `POWR`/`INPT`/`AVMT`/`ERST` update `self.data` copy and call `async_set_updated_data`; `ERST` also triggers a full refresh next cycle. With push active, idle interval becomes `SLOW_INTERVAL`.

- [x] Steps: test with emulator `notify` updates media_player state without polling → FAIL → implement → PASS → commit `feat: add Class 2 status notifications`.

### Task 13: Packaging and CI

**Files:** Create `hacs.json`, `README.md` (EN + ES summary, install, Epson notes), `.github/workflows/ci.yml` (ruff, lib tests, integration tests, hassfest, HACS action), `lib/README.md`, `LICENSE` (MIT).

- During development `manifest.json` requirements reference `pjlink-advance==0.1.0`; README explains publishing the lib first.

- [x] Steps: run full suite + `ruff check .` → commit `chore: add packaging, docs and CI`.
