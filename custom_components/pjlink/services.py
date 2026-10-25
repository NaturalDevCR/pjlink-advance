"""Actions for the PJLink integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from pjlink_advance import PJLinkError

from .const import DOMAIN

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

SERVICE_SEND_COMMAND = "send_command"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_COMMAND = "command"
ATTR_PARAMETER = "parameter"
ATTR_PJ_CLASS = "pj_class"

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_COMMAND): vol.All(
            cv.string, vol.Upper, vol.Match(r"^[A-Z0-9]{4}$")
        ),
        vol.Optional(ATTR_PARAMETER, default="?"): vol.All(
            cv.string, vol.Match(r"^[^\r\n]{0,128}$")
        ),
        vol.Optional(ATTR_PJ_CLASS, default=1): vol.All(
            vol.Coerce(int), vol.In([1, 2])
        ),
    }
)


def _get_entry(hass: HomeAssistant, entry_id: str) -> PJLinkConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"name": entry.title},
        )
    return entry


async def _async_send_command(call: ServiceCall) -> ServiceResponse:
    entry = _get_entry(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    try:
        response = await entry.runtime_data.client.send_raw(
            call.data[ATTR_COMMAND],
            call.data[ATTR_PARAMETER],
            call.data[ATTR_PJ_CLASS],
        )
    except PJLinkError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    return {"response": response}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _async_send_command,
        schema=SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
