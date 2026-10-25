"""Diagnostics for PJLink."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

TO_REDACT = {CONF_PASSWORD, "serial", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PJLinkConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "device": async_redact_data(asdict(coordinator.device), TO_REDACT),
        "state": asdict(coordinator.data) if coordinator.data else None,
        "unsupported_commands": sorted(coordinator.unsupported),
        "push_active": coordinator.push_active,
        "update_interval": coordinator.update_interval.total_seconds()
        if coordinator.update_interval
        else None,
    }
