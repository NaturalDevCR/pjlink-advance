"""Actions for the PJLink integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
from homeassistant.helpers import entity_registry as er
from pjlink_advance import PJLinkError

from .actions import (
    MEDIA_COMMANDS,
    MUTE_TARGETS,
    async_at_media_position,
    async_set_av_mute,
    async_wait_ready,
)
from .const import DOMAIN

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

SERVICE_SEND_COMMAND = "send_command"
SERVICE_WAIT_READY = "wait_ready"
SERVICE_SET_AV_MUTE = "set_av_mute"
SERVICE_AT_MEDIA_POSITION = "at_media_position"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ENTITY_ID = "entity_id"
ATTR_COMMAND = "command"
ATTR_PARAMETER = "parameter"
ATTR_PJ_CLASS = "pj_class"
ATTR_MUTE = "mute"
ATTR_TARGET = "target"
ATTR_TIMEOUT = "timeout"
ATTR_MEDIA_PLAYER = "media_player"
ATTR_POSITION = "position"
ATTR_LEAD = "lead"
ATTR_LATE_TOLERANCE = "late_tolerance"
ATTR_CONTENT_ID_CONTAINS = "content_id_contains"
ATTR_EXPECTED_DURATION = "expected_duration"

PROJECTOR_FIELDS = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
}


def _projector_schema(fields: dict[Any, Any]) -> vol.All:
    return vol.All(
        vol.Schema({**PROJECTOR_FIELDS, **fields}),
        cv.has_at_least_one_key(ATTR_CONFIG_ENTRY_ID, ATTR_ENTITY_ID),
    )


SEND_COMMAND_SCHEMA = _projector_schema(
    {
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

WAIT_READY_SCHEMA = _projector_schema(
    {
        vol.Optional(ATTR_MUTE, default=False): cv.boolean,
        vol.Optional(ATTR_TIMEOUT, default=45): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=300)
        ),
    }
)

SET_AV_MUTE_SCHEMA = _projector_schema(
    {
        vol.Required(ATTR_MUTE): cv.boolean,
        vol.Optional(ATTR_TARGET, default="both"): vol.In(list(MUTE_TARGETS)),
    }
)

AT_MEDIA_POSITION_SCHEMA = _projector_schema(
    {
        vol.Required(ATTR_MEDIA_PLAYER): cv.entity_domain("media_player"),
        vol.Required(ATTR_POSITION): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Required(ATTR_COMMAND): vol.In(MEDIA_COMMANDS),
        vol.Optional(ATTR_LEAD, default=0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=10)
        ),
        vol.Optional(ATTR_LATE_TOLERANCE): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional(ATTR_CONTENT_ID_CONTAINS): cv.string,
        vol.Optional(ATTR_EXPECTED_DURATION): vol.All(
            vol.Coerce(float), vol.Range(min=0, min_included=False)
        ),
        vol.Optional(ATTR_TIMEOUT, default=60): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=7200)
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


def _entry_id_for_entity(hass: HomeAssistant, entity_id: str) -> str:
    registry_entry = er.async_get(hass).async_get(entity_id)
    if (
        registry_entry is None
        or registry_entry.platform != DOMAIN
        or registry_entry.config_entry_id is None
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entity_not_pjlink",
            translation_placeholders={"entity_id": entity_id},
        )
    return registry_entry.config_entry_id


def _entry_from_call(call: ServiceCall) -> PJLinkConfigEntry:
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID) or _entry_id_for_entity(
        call.hass, call.data[ATTR_ENTITY_ID]
    )
    return _get_entry(call.hass, entry_id)


async def _async_send_command(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    entry = _entry_from_call(call)
    parameter = call.data[ATTR_PARAMETER]
    try:
        response = await entry.runtime_data.client.send_raw(
            call.data[ATTR_COMMAND],
            parameter,
            call.data[ATTR_PJ_CLASS],
        )
    except PJLinkError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    if not parameter.startswith("?"):
        # A set command changed the projector: let entities catch up without
        # delaying the caller (timing-sensitive scripts chain commands).
        entry.async_create_background_task(
            hass,
            entry.runtime_data.async_request_refresh(),
            "pjlink_send_command_refresh",
        )
    return {"response": response}


async def _async_wait_ready(call: ServiceCall) -> ServiceResponse:
    entry = _entry_from_call(call)
    return await async_wait_ready(
        entry.runtime_data,
        mute=call.data[ATTR_MUTE],
        max_wait=call.data[ATTR_TIMEOUT],
    )


async def _async_set_av_mute(call: ServiceCall) -> ServiceResponse:
    entry = _entry_from_call(call)
    return await async_set_av_mute(
        entry.runtime_data,
        target=MUTE_TARGETS[call.data[ATTR_TARGET]],
        mute=call.data[ATTR_MUTE],
    )


async def _async_at_media_position(call: ServiceCall) -> ServiceResponse:
    entry = _entry_from_call(call)
    return await async_at_media_position(
        call.hass,
        entry.runtime_data,
        media_player=call.data[ATTR_MEDIA_PLAYER],
        position=call.data[ATTR_POSITION],
        command=call.data[ATTR_COMMAND],
        lead=call.data[ATTR_LEAD],
        late_tolerance=call.data.get(ATTR_LATE_TOLERANCE),
        content_id_contains=call.data.get(ATTR_CONTENT_ID_CONTAINS),
        expected_duration=call.data.get(ATTR_EXPECTED_DURATION),
        max_wait=call.data[ATTR_TIMEOUT],
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration actions."""
    for name, handler, schema in (
        (SERVICE_SEND_COMMAND, _async_send_command, SEND_COMMAND_SCHEMA),
        (SERVICE_WAIT_READY, _async_wait_ready, WAIT_READY_SCHEMA),
        (SERVICE_SET_AV_MUTE, _async_set_av_mute, SET_AV_MUTE_SCHEMA),
        (
            SERVICE_AT_MEDIA_POSITION,
            _async_at_media_position,
            AT_MEDIA_POSITION_SCHEMA,
        ),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
