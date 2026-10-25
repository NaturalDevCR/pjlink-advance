"""Tests for the PJLink media player."""

from __future__ import annotations

import pytest
from pjlink_advance import InputSource, InputType, PowerState
from pjlink_advance.emulator import PJLinkEmulator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pjlink.const import DOMAIN
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MP_DOMAIN,
    SERVICE_SELECT_SOURCE,
    MediaPlayerEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_DOWN,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_UP,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

ENTITY_ID = "media_player.eb_l735u"


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )
    await hass.async_block_till_done()


@pytest.mark.usefixtures("init_integration")
async def test_off_state(hass: HomeAssistant) -> None:
    """The projector starts off with named sources."""
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == [
        "Computer",
        "HDMI1",
        "HDMI2",
        "LAN",
    ]
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & MediaPlayerEntityFeature.VOLUME_STEP


async def test_unique_id_matches_core(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The media player keeps the core integration unique id."""
    entry = er.async_get(hass).async_get(ENTITY_ID)
    assert entry.unique_id == init_integration.entry_id
    assert entry.platform == DOMAIN


@pytest.mark.usefixtures("init_integration")
async def test_power_source_and_mute(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """Commands reach the projector and the state follows."""
    await _call(hass, SERVICE_TURN_ON)
    assert emulator.state.power is PowerState.ON
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_INPUT_SOURCE] == "HDMI1"

    await _call(hass, SERVICE_SELECT_SOURCE, **{ATTR_INPUT_SOURCE: "HDMI2"})
    assert emulator.state.input == InputSource(InputType.DIGITAL, "2")
    assert hass.states.get(ENTITY_ID).attributes[ATTR_INPUT_SOURCE] == "HDMI2"

    await _call(hass, SERVICE_VOLUME_MUTE, **{ATTR_MEDIA_VOLUME_MUTED: True})
    assert emulator.state.mute.audio is True
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_VOLUME_MUTED] is True

    await _call(hass, SERVICE_VOLUME_UP)
    await _call(hass, SERVICE_VOLUME_UP)
    await _call(hass, SERVICE_VOLUME_DOWN)
    assert emulator.state.speaker_volume == 1
    assert "%2SVOL 1" in emulator.received

    await _call(hass, SERVICE_TURN_OFF)
    assert emulator.state.power is PowerState.OFF
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


@pytest.mark.usefixtures("init_integration")
async def test_command_failure(hass: HomeAssistant, emulator: PJLinkEmulator) -> None:
    """A failing command raises HomeAssistantError."""
    emulator.fail_next = "ERR4"
    with pytest.raises(HomeAssistantError):
        await _call(hass, SERVICE_TURN_ON)


@pytest.mark.usefixtures("init_integration")
async def test_select_source_while_off(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """Selecting a source while off is reported as a failure (ERR3)."""
    with pytest.raises(HomeAssistantError):
        await _call(hass, SERVICE_SELECT_SOURCE, **{ATTR_INPUT_SOURCE: "HDMI2"})


async def test_class1_sources_and_features(hass: HomeAssistant) -> None:
    """A Class 1 projector has generic source names and no volume step."""
    async with PJLinkEmulator(pj_class=1) as emulator:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=2,
            title="Viejo",
            data={"host": "127.0.0.1", "port": emulator.port, "encoding": "utf-8"},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        state = hass.states.get("media_player.viejo")
        assert state.attributes[ATTR_INPUT_SOURCE_LIST] == [
            "RGB 1",
            "DIGITAL 1",
            "DIGITAL 2",
            "NETWORK 1",
        ]
        assert not (
            state.attributes[ATTR_SUPPORTED_FEATURES]
            & MediaPlayerEntityFeature.VOLUME_STEP
        )
        await hass.config_entries.async_unload(entry.entry_id)
