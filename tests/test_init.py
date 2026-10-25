"""Tests for setup, unload, migration and polling."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pjlink_advance import PowerState
from pjlink_advance.emulator import PJLinkEmulator
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.pjlink.const import (
    CONF_ENCODING,
    CONF_KEEP_CONNECTION,
    DOMAIN,
    TRANSITION_INTERVAL,
)

from .conftest import entry_data


async def test_setup_and_unload(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """The entry loads, creates a device and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED
    coordinator = init_integration.runtime_data
    assert coordinator.device.model == "EB-L735U"
    assert coordinator.client.connected

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert not coordinator.client.connected


async def test_setup_retry_when_unreachable(hass: HomeAssistant) -> None:
    """An unreachable projector makes setup retry later."""
    async with PJLinkEmulator() as emulator:
        port = emulator.port
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_HOST: "127.0.0.1", CONF_PORT: port, CONF_ENCODING: "utf-8"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_wrong_password_starts_reauth(hass: HomeAssistant) -> None:
    """A rejected password starts the reauth flow."""
    async with PJLinkEmulator(password="right") as emulator:
        entry = MockConfigEntry(
            domain=DOMAIN, version=2, data=entry_data(emulator, password="wrong")
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_ERROR
        flows = hass.config_entries.flow.async_progress()
        assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_migrate_core_entry(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """A version 1 entry created by the core integration keeps working."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="Conference Room",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.data[CONF_ENCODING] == "utf-8"
    assert entry.unique_id == "X8JK0000001"
    # Core used entry_id as the media player unique id.
    state = hass.states.get("media_player.conference_room")
    assert state is not None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_future_version_is_rejected(hass: HomeAssistant) -> None:
    """Downgrading from a newer version is refused."""
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={CONF_HOST: "x"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_transition_polls_fast(
    hass: HomeAssistant,
    emulator: PJLinkEmulator,
    init_integration: MockConfigEntry,
) -> None:
    """While warming up the coordinator polls every few seconds."""
    coordinator = init_integration.runtime_data
    emulator.state.power = PowerState.WARMING
    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(seconds=TRANSITION_INTERVAL)

    emulator.state.power = PowerState.ON
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=TRANSITION_INTERVAL + 1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data.power is PowerState.ON
    assert coordinator.update_interval == timedelta(seconds=30)


async def test_projector_goes_offline(
    hass: HomeAssistant,
    emulator: PJLinkEmulator,
    init_integration: MockConfigEntry,
) -> None:
    """Entities become unavailable when the projector stops answering."""
    coordinator = init_integration.runtime_data
    await emulator.stop()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("media_player.eb_l735u").state == STATE_UNAVAILABLE


async def test_keeps_values_during_err3(
    hass: HomeAssistant,
    emulator: PJLinkEmulator,
    init_integration: MockConfigEntry,
) -> None:
    """ERR3 while on keeps the last known input."""
    coordinator = init_integration.runtime_data
    emulator.state.power = PowerState.ON
    await coordinator.async_refresh()
    assert coordinator.data.input is not None
    emulator.unavailable.add("INPT")
    await coordinator.async_refresh()
    assert coordinator.data.input is not None


async def test_close_connection_after_each_cycle(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """With keep_connection off the socket is released after every cycle."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="EB-L735U",
        unique_id="X8JK0000001",
        data=entry_data(emulator),
        options={CONF_KEEP_CONNECTION: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert not coordinator.client.connected
    # Setup (static info + probes) and the first refresh: one connection each.
    assert emulator.connections == 2

    await coordinator.async_refresh()
    assert not coordinator.client.connected
    assert emulator.connections == 3

    # A command and its follow-up refresh share one connection.
    await hass.services.async_call(
        "media_player",
        "turn_on",
        {"entity_id": "media_player.eb_l735u"},
        blocking=True,
    )
    assert emulator.state.power is PowerState.ON
    assert not coordinator.client.connected
    assert emulator.connections == 4
    await hass.config_entries.async_unload(entry.entry_id)


async def test_keep_connection_open_by_default(
    emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """The default keeps one connection for everything."""
    coordinator = init_integration.runtime_data
    await coordinator.async_refresh()
    assert coordinator.client.connected
    assert emulator.connections == 1
