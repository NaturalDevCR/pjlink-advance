"""Tests for the send_command action, repair issues, diagnostics and push."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from pjlink_advance import ErrorLevel, PowerState
from pjlink_advance.emulator import PJLinkEmulator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.pjlink.const import (
    CONF_FILTER_WARNING_HOURS,
    CONF_LAMP_WARNING_HOURS,
    CONF_PUSH,
    DOMAIN,
)
from custom_components.pjlink.push import DATA_LISTENER

from .conftest import entry_data


async def _send(hass: HomeAssistant, **data: object) -> dict:
    return await hass.services.async_call(
        DOMAIN, "send_command", data, blocking=True, return_response=True
    )


async def test_send_command(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The action returns the raw projector answer."""
    response = await _send(
        hass, config_entry_id=init_integration.entry_id, command="inf1"
    )
    assert response == {"response": "EPSON"}
    response = await _send(
        hass, config_entry_id=init_integration.entry_id, command="SVER", pj_class=2
    )
    assert response == {"response": "1.00"}


async def test_send_command_errors(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """Bad input, unknown entries and projector errors are reported."""
    with pytest.raises(vol.Invalid):
        await _send(hass, config_entry_id=init_integration.entry_id, command="TOOLONG")
    with pytest.raises(ServiceValidationError):
        await _send(hass, config_entry_id="missing", command="INF1")
    emulator.unsupported.add("XXXX")
    with pytest.raises(HomeAssistantError):
        await _send(hass, config_entry_id=init_integration.entry_id, command="XXXX")


async def test_send_command_entry_not_loaded(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Unloaded entries are rejected."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    with pytest.raises(ServiceValidationError):
        await _send(hass, config_entry_id=init_integration.entry_id, command="INF1")


async def _setup(
    hass: HomeAssistant, emulator: PJLinkEmulator, **options: object
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="EB-L735U",
        unique_id="X8JK0000001",
        data=entry_data(emulator),
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_lamp_and_filter_issues(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """Hour thresholds create issues that clear when the counter resets."""
    entry = await _setup(
        hass,
        emulator,
        **{CONF_LAMP_WARNING_HOURS: 1000, CONF_FILTER_WARNING_HOURS: 250},
    )
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"lamp_hours_{entry.entry_id}")
    assert registry.async_get_issue(DOMAIN, f"filter_hours_{entry.entry_id}")

    emulator.state.filter_hours = 0
    coordinator = entry.runtime_data
    coordinator._force_full = True
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, f"filter_hours_{entry.entry_id}") is None
    assert registry.async_get_issue(DOMAIN, f"lamp_hours_{entry.entry_id}")

    await hass.config_entries.async_remove(entry.entry_id)
    assert registry.async_get_issue(DOMAIN, f"lamp_hours_{entry.entry_id}") is None


async def test_disabled_thresholds(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """A threshold of 0 never creates an issue."""
    entry = await _setup(
        hass, emulator, **{CONF_LAMP_WARNING_HOURS: 0, CONF_FILTER_WARNING_HOURS: 0}
    )
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"lamp_hours_{entry.entry_id}") is None
    assert registry.async_get_issue(DOMAIN, f"filter_hours_{entry.entry_id}") is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_projector_error_issue(
    hass: HomeAssistant, emulator: PJLinkEmulator, init_integration: MockConfigEntry
) -> None:
    """An ERST error creates an issue until it clears."""
    registry = ir.async_get(hass)
    issue_id = f"projector_error_{init_integration.entry_id}"
    assert registry.async_get_issue(DOMAIN, issue_id) is None

    coordinator = init_integration.runtime_data
    emulator.state.errors = emulator.state.errors.replace(fan=ErrorLevel.ERROR)
    coordinator._force_full = True
    await coordinator.async_refresh()
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders["categories"] == "fan"

    emulator.state.errors = emulator.state.errors.ok()
    coordinator._force_full = True
    await coordinator.async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """Diagnostics redact secrets and include device and state."""
    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    assert result["device"]["model"] == "EB-L735U"
    assert result["device"]["serial"] == "**REDACTED**"
    assert result["entry"]["unique_id"] == "**REDACTED**"
    assert result["state"]["power"] == "off"
    assert result["unsupported_commands"] == []


async def test_push_notifications(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """Status notifications update entities without polling."""
    with patch("custom_components.pjlink.const.PUSH_PORT", 0):
        entry = await _setup(hass, emulator, **{CONF_PUSH: True})
    coordinator = entry.runtime_data
    assert coordinator.push_active
    listener = hass.data[DATA_LISTENER].listener

    emulator.state.power = PowerState.ON
    await emulator.notify("POWR", "1", ("127.0.0.1", listener.port))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if coordinator.data.power is PowerState.ON:
            break
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("media_player.eb_l735u").state == STATE_ON

    await emulator.notify("ERST", "002000", ("127.0.0.1", listener.port))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if coordinator.data.errors and coordinator.data.errors.has_problem:
            break
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.eb_l735u_problem").state == STATE_ON

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DATA_LISTENER not in hass.data
    assert not coordinator.push_active


async def test_push_port_busy_falls_back_to_polling(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """If the UDP port cannot be bound, the entry still loads."""
    with patch(
        "custom_components.pjlink.push.PJLinkListener.start",
        side_effect=OSError("address in use"),
    ):
        entry = await _setup(hass, emulator, **{CONF_PUSH: True})
    assert not entry.runtime_data.push_active
    await hass.config_entries.async_unload(entry.entry_id)
