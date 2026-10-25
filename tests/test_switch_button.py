"""Tests for the video mute and freeze switches and mic volume buttons."""

from __future__ import annotations

import pytest
from pjlink_advance import PowerState
from pjlink_advance.emulator import PJLinkEmulator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

VIDEO_MUTE = "switch.eb_l735u_video_mute"
FREEZE = "switch.eb_l735u_freeze_image"
MIC_UP = "button.eb_l735u_microphone_volume_up"
MIC_DOWN = "button.eb_l735u_microphone_volume_down"


async def _power_on(emulator: PJLinkEmulator, entry: MockConfigEntry) -> None:
    emulator.state.power = PowerState.ON
    await entry.runtime_data.async_refresh()


async def test_switches_unavailable_while_off(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Switches only work while the projector is on."""
    assert hass.states.get(VIDEO_MUTE).state == STATE_UNAVAILABLE
    assert hass.states.get(FREEZE).state == STATE_UNAVAILABLE


async def test_video_mute_and_freeze(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """Switches send AVMT and FREZ."""
    await _power_on(emulator, init_integration)
    await hass.async_block_till_done()
    assert hass.states.get(VIDEO_MUTE).state == STATE_OFF

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: VIDEO_MUTE}, blocking=True
    )
    assert emulator.state.mute.video is True
    assert emulator.state.mute.audio is False
    assert hass.states.get(VIDEO_MUTE).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: VIDEO_MUTE}, blocking=True
    )
    assert emulator.state.mute.video is False

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: FREEZE}, blocking=True
    )
    assert emulator.state.freeze is True
    assert hass.states.get(FREEZE).state == STATE_ON


async def test_mic_buttons_disabled_by_default(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Mic volume buttons are created disabled."""
    registry = er.async_get(hass)
    for entity_id in (MIC_UP, MIC_DOWN):
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_mic_buttons(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """Mic buttons send MVOL."""
    await _power_on(emulator, init_integration)
    await hass.async_block_till_done()
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: MIC_UP}, blocking=True
    )
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: MIC_UP}, blocking=True
    )
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: MIC_DOWN}, blocking=True
    )
    assert emulator.state.mic_volume == 1
