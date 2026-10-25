"""Config flow for the PJLink integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from pjlink_advance import (
    DeviceInfo,
    PJLinkAuthError,
    PJLinkClient,
    PJLinkError,
)

from .const import (
    CONF_ENCODING,
    CONF_FILTER_WARNING_HOURS,
    CONF_LAMP_WARNING_HOURS,
    CONF_MAC,
    CONF_PUSH,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENCODING,
    DEFAULT_FILTER_WARNING_HOURS,
    DEFAULT_LAMP_WARNING_HOURS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .push import async_search

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): (
                cv.port
            ),
            vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
        }
    )


async def validate_connection(data: Mapping[str, Any]) -> DeviceInfo:
    """Connect to the projector and read its information."""
    async with PJLinkClient(
        data[CONF_HOST],
        data.get(CONF_PORT, DEFAULT_PORT),
        data.get(CONF_PASSWORD) or None,
        encoding=data.get(CONF_ENCODING, DEFAULT_ENCODING),
    ) as client:
        return await client.fetch_static()


async def _try_validate(
    data: Mapping[str, Any],
) -> tuple[DeviceInfo | None, str | None]:
    """Return the device info, or an error key."""
    try:
        return await validate_connection(data), None
    except PJLinkAuthError:
        return None, "invalid_auth"
    except PJLinkError:
        return None, "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected exception")
        return None, "unknown"


def _entry_data(
    user_input: Mapping[str, Any], mac: str | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = {
        CONF_HOST: user_input[CONF_HOST],
        CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
        CONF_ENCODING: user_input.get(CONF_ENCODING, DEFAULT_ENCODING),
    }
    if user_input.get(CONF_PASSWORD):
        data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
    if mac:
        data[CONF_MAC] = mac
    return data


def _unique_id(info: DeviceInfo, data: Mapping[str, Any]) -> str:
    return (
        info.serial
        or data.get(CONF_MAC)
        or f"{data[CONF_HOST]}:{data.get(CONF_PORT, DEFAULT_PORT)}"
    )


class PJLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PJLink."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PJLinkOptionsFlow:
        """Return the options flow."""
        return PJLinkOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user search the network or enter a host."""
        return self.async_show_menu(step_id="user", menu_options=["search", "manual"])

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a projector found with a Class 2 search."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**user_input, CONF_PORT: DEFAULT_PORT}
            mac = self._discovered.get(user_input[CONF_HOST])
            result = await self._async_create_or_error(data, mac, errors)
            if result is not None:
                return result
        else:
            configured = {
                entry.data.get(CONF_HOST) for entry in self._async_current_entries()
            }
            found = await async_search(self.hass)
            self._discovered = {ip: mac for ip, mac in found if ip not in configured}
            if not self._discovered:
                return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="search",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): vol.In(
                        {ip: f"{ip} ({mac})" for ip, mac in self._discovered.items()}
                    ),
                    vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter host, port and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_create_or_error(user_input, None, errors)
            if result is not None:
                return result
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema({}), user_input
            ),
            errors=errors,
        )

    async def _async_create_or_error(
        self, user_input: Mapping[str, Any], mac: str | None, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        data = _entry_data(user_input, mac)
        self._async_abort_entries_match(
            {CONF_HOST: data[CONF_HOST], CONF_PORT: data[CONF_PORT]}
        )
        info, error = await _try_validate(data)
        if info is None:
            errors["base"] = error or "unknown"
            return None
        await self.async_set_unique_id(_unique_id(info, data))
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: data[CONF_HOST], CONF_PORT: data[CONF_PORT]}
        )
        return self.async_create_entry(
            title=info.name or info.model or data[CONF_HOST], data=data
        )

    async def async_step_import(
        self, import_config: dict[str, Any]
    ) -> ConfigFlowResult:
        """Import a configuration from configuration.yaml."""
        data = _entry_data(import_config)
        self._async_abort_entries_match(
            {CONF_HOST: data[CONF_HOST], CONF_PORT: data[CONF_PORT]}
        )
        info, error = await _try_validate(data)
        if info is None:
            return self.async_abort(reason=error or "unknown")
        await self.async_set_unique_id(_unique_id(info, data))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=import_config.get(CONF_NAME) or info.name or data[CONF_HOST],
            data=data,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD) or ""
            info, error = await _try_validate({**entry.data, CONF_PASSWORD: password})
            if info is not None:
                data = {**entry.data, CONF_PASSWORD: password}
                if not password:
                    data.pop(CONF_PASSWORD)
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, port or password."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                **_entry_data(user_input, entry.data.get(CONF_MAC)),
                CONF_ENCODING: entry.data.get(CONF_ENCODING, DEFAULT_ENCODING),
            }
            info, error = await _try_validate(data)
            if info is not None:
                if entry.unique_id is not None and info.serial:
                    await self.async_set_unique_id(info.serial)
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or entry.data),
            errors=errors,
        )


class PJLinkOptionsFlow(OptionsFlow):
    """Handle PJLink options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
                    vol.Required(
                        CONF_LAMP_WARNING_HOURS,
                        default=options.get(
                            CONF_LAMP_WARNING_HOURS, DEFAULT_LAMP_WARNING_HOURS
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=200000)),
                    vol.Required(
                        CONF_FILTER_WARNING_HOURS,
                        default=options.get(
                            CONF_FILTER_WARNING_HOURS, DEFAULT_FILTER_WARNING_HOURS
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=200000)),
                    vol.Required(
                        CONF_PUSH, default=options.get(CONF_PUSH, False)
                    ): bool,
                }
            ),
        )
