# PJLink Advance

A `pjlink` integration for Home Assistant that **replaces** the core one and
uses the full PJLink Class 1 and Class 2 protocol. It ships with the
[`pjlink-advance`](lib/) asyncio library, which includes a projector emulator
for testing without hardware.

## What it adds over the core integration

| Feature | Core | PJLink Advance |
|---|---|---|
| Power, inputs, audio mute | ✅ | ✅ |
| Transitional states (warming up / cooling down) | ❌ | ✅ power status sensor |
| Real input names (Class 2) | ❌ | ✅ "HDMI1" instead of "DIGITAL 1" |
| Video mute (blank screen) | ❌ | ✅ switch |
| Freeze image (Class 2) | ❌ | ✅ switch |
| Speaker volume +/− (Class 2) | ❌ | ✅ on the media player |
| Microphone volume (Class 2) | ❌ | ✅ buttons (disabled by default) |
| Lamp / light source hours | ❌ | ✅ one sensor per lamp |
| Filter hours (Class 2) | ❌ | ✅ |
| Per-category alarms (fan, lamp, temperature, cover, filter, other) | ❌ | ✅ sensors + "Problem" binary sensor |
| Input and recommended resolution (Class 2) | ❌ | ✅ |
| Replacement lamp and filter models (Class 2) | ❌ | ✅ |
| Device info (manufacturer, model, serial, firmware, MAC) | ❌ | ✅ |
| Network search (Class 2) | ❌ | ✅ in the config flow |
| UDP push notifications (Class 2) | ❌ | ✅ optional |
| Reauth, reconfigure, options | ❌ | ✅ |
| Repair issues (lamp, filter, errors) | ❌ | ✅ |
| `pjlink.send_command` action with response | ❌ | ✅ |
| Downloadable diagnostics | ❌ | ✅ |
| Persistent connection with automatic reconnect | ❌ (one connection per query) | ✅ (optional: close after each polling cycle) |
| Adaptive polling (5 s while warming up) | ❌ | ✅ |
| Translations | EN | EN + ES |

Class 2 entities are only created when the projector is Class 2 and answers
the command (if it replies `ERR1`, the entity is skipped).

## Compatibility with the core integration

- It uses the same `pjlink` domain, so Home Assistant loads it instead of the
  core integration.
- Existing entries (version 1) are migrated to version 2 automatically.
- The `media_player` keeps the same `unique_id`, so its `entity_id` and your
  automations keep working.
- YAML configuration (`media_player: - platform: pjlink`) is still imported,
  as in core.

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → Custom repositories →
   `https://github.com/NaturalDevCR/pjlink-advance`, category *Integration*.
2. Install **PJLink Advance** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **PJLink**.

Because it overrides a core domain, this integration can only be added to HACS
as a custom repository, not from the default list.

### Manual

Copy `custom_components/pjlink` to `config/custom_components/pjlink` and
restart.

## Options

| Option | Default | Description |
|---|---|---|
| Polling interval | 30 s | While idle. Warm-up and cool-down are always polled every 5 s. |
| Lamp warning | 20000 h | Creates a repair issue. 0 disables it. |
| Filter warning | 0 (disabled) | Same as above. |
| Class 2 notifications | off | Listens on UDP 4352. With push enabled, polling drops to every 5 min. |
| Keep the connection open | on | Reuses one PJLink session. Turn it off when another control system (Crestron, Extron, vendor software) also uses PJLink with the projector: the connection then closes after every polling cycle and every command. |

Push notifications and network search need Home Assistant and the projector on
the same subnet, and Home Assistant must receive UDP on port 4352 (Home
Assistant OS, or Docker with `network_mode: host`). If the port is busy, the
integration keeps working with polling only.

## Notes for the Epson EB-L735U

- Enable standby communication (an option like *Standby Mode: Communication
  On* in the network menu). Otherwise the projector does not answer while off
  and shows as unavailable.
- It is a laser projector: "Lamp hours" are light source hours and there is
  no replacement lamp model.
- During warm-up it answers `ERR3` to several commands; the integration keeps
  the last known values meanwhile.
- Epson-specific features (picture mode, lens) use ESC/VP.net and are out of
  scope. You can try `pjlink.send_command` only if the projector exposes them
  over PJLink.

## Actions

All actions target the projector with `entity_id` (any entity of the
projector) or `config_entry_id`, and return a response.

| Action | What it does | Response |
|---|---|---|
| `pjlink.send_command` | Sends a raw PJLink command. Set commands refresh the entities in the background. | `response` |
| `pjlink.wait_ready` | Waits until the projector is fully on. With `mute: true` it mutes image and sound as soon as warm-up starts and only succeeds once the mute is confirmed. | `ok`, `power`, `muted` |
| `pjlink.set_av_mute` | Mutes or unmutes (`target`: both, video, audio) and confirms it. Off or cooling projectors are left alone. | `ok`, `power_off`, `video_muted`, `audio_muted` |
| `pjlink.at_media_position` | Runs `unmute`, `mute` or `power_off` when a media player reaches a playback position. | `ok`, `done`, `position`, or `natural_end` / `deferred` / `error` |

Projector-side outcomes such as a timeout or a missed window are returned with
`ok: false` instead of raising, so automations can branch on them.

### Example: hide the warm-up, reveal the picture when the content starts

```yaml
- action: media_player.turn_on
  target:
    entity_id: media_player.projector
- action: pjlink.wait_ready
  data:
    entity_id: media_player.projector
    mute: true
    timeout: 60
  response_variable: ready
- action: media_player.play_media
  target:
    entity_id: media_player.cast
  data:
    media_content_id: media-source://media_source/local/clip.mp4
    media_content_type: video
- action: pjlink.at_media_position
  data:
    entity_id: media_player.projector
    media_player: media_player.cast
    position: 4.0          # where the content starts
    command: unmute
    lead: 0.25             # open the shutter slightly early
    late_tolerance: 0.75   # never reveal mid-scene
    content_id_contains: clip.mp4
  response_variable: opened
```

`at_media_position` extrapolates the position from the media player's
`media_position` and `media_position_updated_at` attributes, waits until the
target, and re-checks whenever the player changes (pause, seek, new content).
Its accuracy depends on how promptly the media player integration reports
playback changes.

### Example: `pjlink.send_command`

```yaml
action: pjlink.send_command
data:
  entity_id: media_player.projector
  command: SVER
  pj_class: 2
response_variable: answer
```

## Development

```bash
uv sync --python 3.14                      # integration environment (HA + tests)
uv run pytest -q                           # integration tests
cd lib && uv run --extra test pytest -q    # library tests
```

Projector emulator (Class 2, TCP and UDP on 4352):

```bash
cd lib && uv run python -m pjlink_advance.emulator --port 4352 --password secret
```

## Releasing

Publishing a GitHub release runs `.github/workflows/publish.yml`, which builds
the library and uploads it to PyPI with trusted publishing. When the library
version changes, update `requirements` in
`custom_components/pjlink/manifest.json`.

## Towards upstream (Home Assistant core)

The protocol code already lives in a separate library, as core requires. A
future PR should land in steps: first replace `pypjlink2` with
`pjlink-advance` at feature parity, then add the new platforms (sensors,
switches, and so on).
