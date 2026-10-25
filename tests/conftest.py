"""Fixtures for the PJLink integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pjlink_advance.emulator import PJLinkEmulator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pjlink.const import CONF_ENCODING, DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Load custom_components/pjlink instead of the core integration."""
    return


@pytest.fixture(autouse=True)
def allow_sockets(socket_enabled: None) -> None:
    """The emulator listens on localhost TCP and UDP sockets."""


@pytest.fixture(autouse=True)
def fast_search() -> Generator[None]:
    """Keep network searches short."""
    with patch("custom_components.pjlink.const.SEARCH_DURATION", 0.2):
        yield


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Create entities that are disabled by default as enabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture
async def emulator() -> AsyncIterator[PJLinkEmulator]:
    """Start a Class 2 projector emulator."""
    async with PJLinkEmulator() as emu:
        yield emu


def entry_data(emulator: PJLinkEmulator, **extra: Any) -> dict[str, Any]:
    """Return config entry data pointing at the emulator."""
    return {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: emulator.port,
        CONF_ENCODING: "utf-8",
        **extra,
    }


@pytest.fixture
def config_entry(emulator: PJLinkEmulator) -> MockConfigEntry:
    """Return a config entry for the emulator."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="EB-L735U",
        unique_id="X8JK0000001",
        data=entry_data(emulator),
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> AsyncIterator[MockConfigEntry]:
    """Set up the integration against the emulator."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
