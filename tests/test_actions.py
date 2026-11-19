"""Tests for the wait_ready, set_av_mute and at_media_position actions."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pjlink_advance import MuteState, PowerState
from pjlink_advance.emulator import PJLinkEmulator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pjlink.const import DOMAIN

PROJECTOR = "media_player.eb_l735u"
CAST = "media_player.cast"
CLIP = "media-source://local/clips/abc-123/out.mp4"


async def _call(hass: HomeAssistant, service: str, **data: Any) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN,
        service,
        {"entity_id": PROJECTOR, **data},
        blocking=True,
        return_response=True,
    )


def _cast(
    hass: HomeAssistant,
    state: str,
    position: float,
    *,
    content: str = CLIP,
    duration: float = 120.0,
    age: float = 0.0,
) -> None:
    hass.states.async_set(
        CAST,
        state,
        {
            "media_content_id": content,
            "media_duration": duration,
            "media_position": position,
            "media_position_updated_at": dt_util.utcnow() - timedelta(seconds=age),
        },
    )


async def _power_on_muted(
    hass: HomeAssistant, emulator: PJLinkEmulator, entry: MockConfigEntry
) -> None:
    emulator.state.power = PowerState.ON
    emulator.state.mute = MuteState(video=True, audio=True)
    await entry.runtime_data.async_refresh()


@pytest.mark.usefixtures("init_integration")
async def test_wait_ready_mutes_during_warm_up(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """The projector is muted while warming up and reported ready once on."""
    emulator.state.power = PowerState.WARMING

    async def finish_warm_up() -> None:
        await asyncio.sleep(0.6)
        emulator.state.power = PowerState.ON

    task = asyncio.create_task(finish_warm_up())
    result = await _call(hass, "wait_ready", mute=True, timeout=10)
    await task
    assert result == {"ok": True, "power": "on", "muted": True}
    assert emulator.state.mute == MuteState(video=True, audio=True)


@pytest.mark.usefixtures("init_integration")
async def test_wait_ready_without_mute(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    emulator.state.power = PowerState.ON
    assert await _call(hass, "wait_ready") == {
        "ok": True,
        "power": "on",
        "muted": False,
    }
    assert emulator.state.mute == MuteState(video=False, audio=False)


@pytest.mark.usefixtures("init_integration")
async def test_wait_ready_times_out(hass: HomeAssistant) -> None:
    result = await _call(hass, "wait_ready", mute=True, timeout=1)
    assert result["ok"] is False
    assert result["power"] == "off"


@pytest.mark.usefixtures("init_integration")
async def test_set_av_mute(hass: HomeAssistant, emulator: PJLinkEmulator) -> None:
    """Mute is confirmed; an off projector is left alone."""
    assert await _call(hass, "set_av_mute", mute=False) == {
        "ok": True,
        "power_off": True,
        "power": "off",
    }
    emulator.state.power = PowerState.ON
    result = await _call(hass, "set_av_mute", mute=True)
    assert result["ok"] is True
    assert emulator.state.mute == MuteState(video=True, audio=True)
    result = await _call(hass, "set_av_mute", mute=False, target="video")
    assert result == {
        "ok": True,
        "power_off": False,
        "video_muted": False,
        "audio_muted": True,
    }


async def test_unmute_at_position(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """Unmute fires `lead` seconds before the position."""
    await _power_on_muted(hass, emulator, init_integration)
    _cast(hass, "playing", 10.0)
    start = asyncio.get_running_loop().time()
    result = await _call(
        hass,
        "at_media_position",
        media_player=CAST,
        position=10.5,
        command="unmute",
        lead=0.25,
        late_tolerance=0.75,
        content_id_contains="abc-123",
        expected_duration=120.2,
        timeout=10,
    )
    elapsed = asyncio.get_running_loop().time() - start
    assert result["ok"] is True
    assert result["done"] is True
    assert 10.2 <= result["position"] <= 10.6
    assert 0.2 <= elapsed < 1.0
    assert emulator.state.mute == MuteState(video=False, audio=False)


async def test_power_off_at_position(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    await _power_on_muted(hass, emulator, init_integration)
    _cast(hass, "playing", 30.0)
    result = await _call(
        hass, "at_media_position", media_player=CAST, position=30, command="power_off"
    )
    assert result["ok"] is True
    assert emulator.state.power is PowerState.OFF


async def test_missed_window(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    await _power_on_muted(hass, emulator, init_integration)
    _cast(hass, "playing", 12.0)
    result = await _call(
        hass,
        "at_media_position",
        media_player=CAST,
        position=10,
        command="unmute",
        late_tolerance=0.75,
    )
    assert result["ok"] is False
    assert result["error"] == "Missed position window"
    assert emulator.state.mute.video is True


async def test_waits_through_pause(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """A paused player is followed until it plays again."""
    await _power_on_muted(hass, emulator, init_integration)
    _cast(hass, "paused", 5.0)

    async def resume() -> None:
        await asyncio.sleep(0.3)
        _cast(hass, "playing", 5.0)

    task = asyncio.create_task(resume())
    result = await _call(
        hass,
        "at_media_position",
        media_player=CAST,
        position=5.2,
        command="unmute",
        timeout=5,
    )
    await task
    assert result["ok"] is True


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"state": "idle"}, {"ok": False, "natural_end": True}),
        (
            {"state": "playing", "content": "media-source://other.mp4"},
            {"ok": False, "error": "Media content replaced"},
        ),
    ],
)
async def test_ends_when_media_changes(
    hass: HomeAssistant,
    emulator: PJLinkEmulator,
    init_integration: MockConfigEntry,
    change: dict[str, str],
    expected: dict[str, Any],
) -> None:
    await _power_on_muted(hass, emulator, init_integration)
    _cast(hass, "playing", 1.0)

    async def alter() -> None:
        await asyncio.sleep(0.2)
        _cast(hass, change["state"], 2.0, content=change.get("content", CLIP))

    task = asyncio.create_task(alter())
    result = await _call(
        hass,
        "at_media_position",
        media_player=CAST,
        position=60,
        command="power_off",
        content_id_contains="abc-123",
        timeout=5,
    )
    await task
    assert result == expected
    assert emulator.state.power is PowerState.ON


async def test_deferred_and_duration_mismatch(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    await _power_on_muted(hass, emulator, init_integration)
    _cast(hass, "playing", 1.0)
    result = await _call(
        hass,
        "at_media_position",
        media_player=CAST,
        position=60,
        command="power_off",
        timeout=1,
    )
    assert result == {
        "ok": False,
        "deferred": True,
        "error": "Position not reached within timeout",
    }
    result = await _call(
        hass,
        "at_media_position",
        media_player=CAST,
        position=60,
        command="power_off",
        expected_duration=90,
    )
    assert result["ok"] is False
    assert result["error"] == "Media duration differs from expected"
    assert emulator.state.power is PowerState.ON
