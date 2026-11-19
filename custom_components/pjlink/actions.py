"""Long-running projector actions: wait for ready, verified A/V mute, media sync.

They return a result mapping instead of raising on projector-side outcomes
(timeouts, missed windows), so automations can branch on ``ok``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

from homeassistant.const import STATE_IDLE, STATE_PLAYING
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from pjlink_advance import MuteTarget, PJLinkCommandError, PJLinkError, PowerState

from .coordinator import PJLinkCoordinator

_LOGGER = logging.getLogger(__name__)

MUTE_TARGETS = {
    "video": MuteTarget.VIDEO,
    "audio": MuteTarget.AUDIO,
    "both": MuteTarget.BOTH,
}

COMMAND_UNMUTE = "unmute"
COMMAND_MUTE = "mute"
COMMAND_POWER_OFF = "power_off"
MEDIA_COMMANDS = (COMMAND_UNMUTE, COMMAND_MUTE, COMMAND_POWER_OFF)

# A playing media player whose duration differs more than this from the
# expected one is treated as different content.
DURATION_TOLERANCE = 0.5


def _refresh_later(coordinator: PJLinkCoordinator) -> None:
    coordinator.config_entry.async_create_background_task(
        coordinator.hass,
        coordinator.async_request_refresh(),
        "pjlink_action_refresh",
    )


def _is_muted(video: bool, audio: bool, target: MuteTarget) -> bool:
    if target is MuteTarget.VIDEO:
        return video
    if target is MuteTarget.AUDIO:
        return audio
    return video and audio


async def async_wait_ready(
    coordinator: PJLinkCoordinator,
    *,
    mute: bool,
    max_wait: float,
    poll_interval: float = 0.5,
) -> dict[str, Any]:
    """Wait until the projector is fully on.

    With ``mute`` the image and sound are muted as soon as the projector starts
    warming up, and the result is only ``ok`` once the mute is confirmed.
    """
    client = coordinator.client
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait
    masked = False
    power: PowerState | None = None
    try:
        while True:
            try:
                power = await client.get_power()
                if mute and not masked and power.is_on:
                    await client.set_av_mute(MuteTarget.BOTH, True)
                    masked = True
                if power is PowerState.ON:
                    if not mute:
                        return {"ok": True, "power": power.value, "muted": False}
                    state = await client.get_av_mute()
                    if state.video and state.audio:
                        return {"ok": True, "power": power.value, "muted": True}
                    masked = False
            except PJLinkError as err:
                # ERR3 while warming up and dropped sessions are expected; retry.
                _LOGGER.debug("wait_ready: %s", err)
            if loop.time() + poll_interval > deadline:
                return {
                    "ok": False,
                    "power": power.value if power else None,
                    "muted": masked,
                    "error": "Projector did not confirm ready"
                    + (" and muted" if mute else ""),
                }
            await asyncio.sleep(poll_interval)
    finally:
        _refresh_later(coordinator)


async def async_set_av_mute(
    coordinator: PJLinkCoordinator, *, target: MuteTarget, mute: bool
) -> dict[str, Any]:
    """Mute or unmute and confirm it. Off or cooling projectors are left alone."""
    client = coordinator.client
    try:
        power = await client.get_power()
        if not power.is_on:
            return {"ok": True, "power_off": True, "power": power.value}
        await client.set_av_mute(target, mute)
        state = await client.get_av_mute()
    except PJLinkError as err:
        return {"ok": False, "error": str(err)}
    finally:
        _refresh_later(coordinator)
    confirmed = _is_muted(state.video, state.audio, target) is mute
    result: dict[str, Any] = {
        "ok": confirmed,
        "power_off": False,
        "video_muted": state.video,
        "audio_muted": state.audio,
    }
    if not confirmed:
        result["error"] = "Mute state not confirmed"
    return result


def _position_now(attributes: dict[str, Any]) -> float | None:
    """Extrapolate the playback position of a playing media player."""
    position = attributes.get("media_position")
    updated = attributes.get("media_position_updated_at")
    if position is None or updated is None:
        return None
    if isinstance(updated, str):
        updated = dt_util.parse_datetime(updated)
    if not isinstance(updated, datetime):
        return None
    return float(position) + (dt_util.utcnow() - updated).total_seconds()


async def _run_media_command(
    coordinator: PJLinkCoordinator, command: str
) -> dict[str, Any] | None:
    """Send the boundary command; return an error result or None on success."""
    client = coordinator.client
    try:
        if command == COMMAND_POWER_OFF:
            await client.set_power(False)
            return None
        mute = command == COMMAND_MUTE
        await client.set_av_mute(MuteTarget.BOTH, mute)
        state = await client.get_av_mute()
    except PJLinkCommandError as err:
        return {"ok": False, "error": f"Projector rejected {command}: {err}"}
    except PJLinkError as err:
        return {"ok": False, "error": str(err)}
    confirmed = (
        (state.video and state.audio) if mute else not (state.video or state.audio)
    )
    if not confirmed:
        return {"ok": False, "error": f"{command} not confirmed"}
    return None


async def async_at_media_position(
    hass: HomeAssistant,
    coordinator: PJLinkCoordinator,
    *,
    media_player: str,
    position: float,
    command: str,
    lead: float,
    late_tolerance: float | None,
    content_id_contains: str | None,
    expected_duration: float | None,
    max_wait: float,
) -> dict[str, Any]:
    """Run ``command`` when ``media_player`` reaches ``position`` seconds.

    The position is extrapolated from the media player's last reported
    position. The command fires ``lead`` seconds early. With
    ``late_tolerance`` the call gives up instead of firing when the position
    is already past ``position + late_tolerance``.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait
    changed = asyncio.Event()

    @callback
    def _changed(_event: Event[EventStateChangedData]) -> None:
        changed.set()

    unsubscribe = async_track_state_change_event(hass, media_player, _changed)
    matched = False
    try:
        while True:
            changed.clear()
            wait: float | None = None
            state = hass.states.get(media_player)
            attributes = dict(state.attributes) if state else {}
            content = str(attributes.get("media_content_id") or "")
            belongs = state is not None and (
                not content_id_contains or content_id_contains in content
            )
            if matched and not belongs:
                return {"ok": False, "error": "Media content replaced"}
            if belongs:
                matched = True
                duration = attributes.get("media_duration")
                if (
                    expected_duration is not None
                    and duration
                    and abs(float(duration) - expected_duration) > DURATION_TOLERANCE
                ):
                    return {
                        "ok": False,
                        "error": "Media duration differs from expected",
                        "media_duration": duration,
                    }
                if state.state == STATE_IDLE:
                    return {"ok": False, "natural_end": True}
                current = (
                    _position_now(attributes) if state.state == STATE_PLAYING else None
                )
                if current is not None:
                    if late_tolerance is not None and current > position + (
                        late_tolerance
                    ):
                        return {
                            "ok": False,
                            "error": "Missed position window",
                            "position": round(current, 3),
                        }
                    target = max(0.0, position - lead)
                    if current >= target:
                        if error := await _run_media_command(coordinator, command):
                            return {**error, "position": round(current, 3)}
                        _refresh_later(coordinator)
                        return {
                            "ok": True,
                            "done": True,
                            "command": command,
                            "position": round(current, 3),
                        }
                    wait = target - current
            remaining = deadline - loop.time()
            if remaining <= 0:
                return {
                    "ok": False,
                    "deferred": True,
                    "error": "Position not reached within timeout",
                }
            # Sleep until the target, or until the media player changes
            # (pause, seek, new content), whichever comes first.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    changed.wait(),
                    remaining if wait is None else min(wait, remaining),
                )
    finally:
        unsubscribe()
