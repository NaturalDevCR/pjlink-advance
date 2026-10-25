"""The PJLink integration."""

from __future__ import annotations

import logging

from pjlink_advance import PJLinkClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ENCODING,
    CONF_MAC,
    CONF_PUSH,
    DEFAULT_ENCODING,
    DEFAULT_PORT,
    DOMAIN,
)
from .coordinator import PJLinkCoordinator
from .push import async_enable_push
from .issues import async_delete_issues, async_track_issues
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type PJLinkConfigEntry = ConfigEntry[PJLinkCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PJLinkConfigEntry) -> bool:
    """Set up PJLink from a config entry."""
    client = PJLinkClient(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data.get(CONF_PASSWORD) or None,
        encoding=entry.data.get(CONF_ENCODING, DEFAULT_ENCODING),
    )
    coordinator = PJLinkCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise
    entry.runtime_data = coordinator
    entry.async_on_unload(client.close)

    _async_set_unique_id(hass, entry, coordinator)

    if entry.options.get(CONF_PUSH) and coordinator.pj_class >= 2:
        if unsubscribe := await async_enable_push(hass, coordinator):
            entry.async_on_unload(unsubscribe)

    entry.async_on_unload(async_track_issues(hass, coordinator))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_set_unique_id(
    hass: HomeAssistant, entry: PJLinkConfigEntry, coordinator: PJLinkCoordinator
) -> None:
    """Give entries created by the core integration a stable unique id."""
    if entry.unique_id is not None:
        return
    unique_id = (
        coordinator.device.serial
        or entry.data.get(CONF_MAC)
        or f"{entry.data[CONF_HOST]}:{entry.data.get(CONF_PORT, DEFAULT_PORT)}"
    )
    if hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id):
        _LOGGER.debug("Unique id %s already used by another entry", unique_id)
        return
    hass.config_entries.async_update_entry(entry, unique_id=unique_id)


async def _async_update_listener(hass: HomeAssistant, entry: PJLinkConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PJLinkConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: PJLinkConfigEntry) -> None:
    """Clean up repair issues of a removed entry."""
    async_delete_issues(hass, entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate entries created by the Home Assistant core integration."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        data = {
            CONF_PORT: DEFAULT_PORT,
            CONF_ENCODING: DEFAULT_ENCODING,
            **entry.data,
        }
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info("Migrated PJLink entry %s to version 2", entry.title)
    return True
