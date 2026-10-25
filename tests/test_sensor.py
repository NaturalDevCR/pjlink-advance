"""Tests for PJLink sensors and the problem binary sensor."""

from __future__ import annotations

import pytest
from pjlink_advance import NO_SIGNAL, ErrorLevel, Lamp, PowerState
from pjlink_advance.emulator import EmulatorState, PJLinkEmulator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pjlink.const import DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

PREFIX = "sensor.eb_l735u_"


@pytest.mark.usefixtures("init_integration")
async def test_sensor_values(hass: HomeAssistant) -> None:
    """Sensors report the emulator values."""
    assert hass.states.get(f"{PREFIX}power_status").state == "off"
    lamp = hass.states.get(f"{PREFIX}lamp_hours")
    assert lamp.state == "1200"
    assert lamp.attributes["unit_of_measurement"] == "h"
    assert hass.states.get(f"{PREFIX}filter_hours").state == "300"
    assert hass.states.get(f"{PREFIX}filter_status").state == "ok"
    assert hass.states.get(f"{PREFIX}replacement_filter").state == "ELPAF60"
    # Laser projector: no replacement lamp model.
    assert hass.states.get(f"{PREFIX}replacement_lamp") is None
    assert hass.states.get(f"{PREFIX}input_resolution").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.eb_l735u_problem").state == STATE_OFF


async def test_recommended_resolution_disabled_by_default(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The recommended resolution sensor exists but is disabled."""
    entry = er.async_get(hass).async_get(f"{PREFIX}recommended_resolution")
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_values_follow_projector(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """Power, resolution and errors update on refresh."""
    coordinator = init_integration.runtime_data
    emulator.state.power = PowerState.ON
    emulator.state.errors = emulator.state.errors.replace(
        temperature=ErrorLevel.WARNING
    )
    coordinator._force_full = True  # noqa: SLF001
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(f"{PREFIX}power_status").state == "on"
    assert hass.states.get(f"{PREFIX}input_resolution").state == "1920x1200"
    assert hass.states.get(f"{PREFIX}temperature_status").state == "warning"
    assert hass.states.get("binary_sensor.eb_l735u_problem").state == STATE_ON

    emulator.state.input_resolution = NO_SIGNAL
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(f"{PREFIX}input_resolution").state == "no_signal"


async def test_multiple_lamps(hass: HomeAssistant) -> None:
    """Projectors with several lamps get one numbered sensor per lamp."""
    state = EmulatorState(lamps=[Lamp(10, False), Lamp(20, False)])
    async with PJLinkEmulator(state) as emulator:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=2,
            title="Dual",
            data={"host": "127.0.0.1", "port": emulator.port, "encoding": "utf-8"},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.dual_lamp_1_hours").state == "10"
        assert hass.states.get("sensor.dual_lamp_2_hours").state == "20"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_class1_has_no_class2_sensors(hass: HomeAssistant) -> None:
    """Class 1 projectors only get Class 1 sensors."""
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
        assert hass.states.get("sensor.viejo_power_status") is not None
        assert hass.states.get("sensor.viejo_lamp_hours") is not None
        assert hass.states.get("sensor.viejo_filter_hours") is None
        assert hass.states.get("sensor.viejo_input_resolution") is None
        assert hass.states.get("switch.viejo_freeze_image") is None
        assert hass.states.get("button.viejo_microphone_volume_up") is None
        assert hass.states.get("switch.viejo_video_mute") is not None
        await hass.config_entries.async_unload(entry.entry_id)


async def test_unsupported_class2_command_skips_entity(hass: HomeAssistant) -> None:
    """A Class 2 query answered with ERR1 removes its entity."""
    async with PJLinkEmulator() as emulator:
        emulator.unsupported.update({"FREZ", "FILT"})
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=2,
            title="Parcial",
            data={"host": "127.0.0.1", "port": emulator.port, "encoding": "utf-8"},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.parcial_filter_hours") is None
        assert hass.states.get("switch.parcial_freeze_image") is None
        assert hass.states.get("sensor.parcial_input_resolution") is not None
        await hass.config_entries.async_unload(entry.entry_id)
