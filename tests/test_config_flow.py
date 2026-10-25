"""Tests for the PJLink config, reauth, reconfigure and options flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pjlink_advance.emulator import PJLinkEmulator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pjlink.const import (
    CONF_ENCODING,
    CONF_FILTER_WARNING_HOURS,
    CONF_LAMP_WARNING_HOURS,
    CONF_MAC,
    CONF_PUSH,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.pjlink.push import async_search

from .conftest import entry_data

SEARCH = "custom_components.pjlink.config_flow.async_search"


async def _start_manual(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    return result["flow_id"]


async def test_manual_success(hass: HomeAssistant, emulator: PJLinkEmulator) -> None:
    """Manual setup creates an entry named after the projector."""
    flow_id = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "EB-L735U"
    assert result["data"] == {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: emulator.port,
        CONF_ENCODING: "utf-8",
    }
    assert result["result"].unique_id == "X8JK0000001"
    await hass.async_block_till_done()


async def test_manual_with_password(hass: HomeAssistant) -> None:
    """The password is validated and stored."""
    async with PJLinkEmulator(password="secret") as emulator:
        flow_id = await _start_manual(hass)
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: "bad"},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}

        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: "secret"},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_PASSWORD] == "secret"
        await hass.async_block_till_done()


async def test_manual_cannot_connect(hass: HomeAssistant) -> None:
    """An unreachable host shows an error."""
    async with PJLinkEmulator() as emulator:
        port = emulator.port
    flow_id = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_HOST: "127.0.0.1", CONF_PORT: port}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_unknown_error(hass: HomeAssistant) -> None:
    """Unexpected exceptions map to unknown."""
    flow_id = await _start_manual(hass)
    with patch(
        "custom_components.pjlink.config_flow.validate_connection",
        side_effect=RuntimeError,
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_HOST: "127.0.0.1", CONF_PORT: 4352}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_manual_already_configured(
    hass: HomeAssistant, emulator: PJLinkEmulator, config_entry: MockConfigEntry
) -> None:
    """The same projector cannot be added twice."""
    config_entry.add_to_hass(hass)
    flow_id = await _start_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_same_serial_new_address_updates_entry(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """A known projector at a new address updates the existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="X8JK0000001",
        data={CONF_HOST: "10.0.0.5", CONF_PORT: 4352, CONF_ENCODING: "utf-8"},
    )
    entry.add_to_hass(hass)
    flow_id = await _start_manual(hass)
    with patch("custom_components.pjlink.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "127.0.0.1"
    assert entry.data[CONF_PORT] == emulator.port


async def test_search_success(hass: HomeAssistant, emulator: PJLinkEmulator) -> None:
    """A projector found by search is added with its MAC address."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with (
        patch(SEARCH, return_value=[("127.0.0.1", "00:26:ab:00:00:01")]),
        patch("custom_components.pjlink.config_flow.DEFAULT_PORT", emulator.port),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "search"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "search"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MAC] == "00:26:ab:00:00:01"
    assert result["data"][CONF_PORT] == emulator.port
    await hass.async_block_till_done()


async def test_search_nothing_found(hass: HomeAssistant) -> None:
    """An empty search aborts."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(SEARCH, return_value=[]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "search"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_search_with_emulator(hass: HomeAssistant) -> None:
    """The real search finds an emulator answering on its UDP port."""
    async with PJLinkEmulator() as emulator:
        with (
            patch("custom_components.pjlink.const.PUSH_PORT", 0),
            patch("custom_components.pjlink.const.SEARCH_BROADCAST", "127.0.0.1"),
            patch(
                "custom_components.pjlink.const.SEARCH_TARGET_PORT",
                emulator.udp_port,
            ),
        ):
            found = await async_search(hass)
    assert found == [("127.0.0.1", "00:26:ab:00:00:01")]


async def test_import(hass: HomeAssistant, emulator: PJLinkEmulator) -> None:
    """YAML import creates an entry with the configured name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: emulator.port,
            CONF_NAME: "Conference Room",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Conference Room"
    await hass.async_block_till_done()


async def test_import_cannot_connect(hass: HomeAssistant) -> None:
    """A failing YAML import aborts with the reason."""
    async with PJLinkEmulator() as emulator:
        port = emulator.port
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={CONF_HOST: "127.0.0.1", CONF_PORT: port},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_reauth(hass: HomeAssistant) -> None:
    """Reauth stores the new password and reloads."""
    async with PJLinkEmulator(password="new") as emulator:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=2,
            unique_id="X8JK0000001",
            data=entry_data(emulator, password="old"),
        )
        entry.add_to_hass(hass)
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "wrong"}
        )
        assert result["errors"] == {"base": "invalid_auth"}

        with patch("custom_components.pjlink.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_PASSWORD: "new"}
            )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == "new"


async def test_reconfigure(hass: HomeAssistant, emulator: PJLinkEmulator) -> None:
    """Reconfigure changes host and port."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="X8JK0000001",
        data={CONF_HOST: "10.0.0.5", CONF_PORT: 4352, CONF_ENCODING: "utf-8"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    with patch("custom_components.pjlink.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "127.0.0.1"


async def test_reconfigure_wrong_device(
    hass: HomeAssistant, emulator: PJLinkEmulator
) -> None:
    """Reconfigure refuses a different projector."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="OTHER-SERIAL",
        data={CONF_HOST: "10.0.0.5", CONF_PORT: 4352, CONF_ENCODING: "utf-8"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


@pytest.mark.usefixtures("init_integration")
async def test_options(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Options are saved and the entry reloads."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 60,
            CONF_LAMP_WARNING_HOURS: 1000,
            CONF_FILTER_WARNING_HOURS: 250,
            CONF_PUSH: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options[CONF_SCAN_INTERVAL] == 60
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 60
