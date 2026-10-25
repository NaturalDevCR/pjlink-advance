"""Data update coordinator for PJLink."""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pjlink_advance import (
    DeviceInfo,
    PJLinkAuthError,
    PJLinkClient,
    PJLinkError,
    PJLinkProtocolError,
    PJLinkUndefinedCommand,
    PowerState,
    ProjectorState,
)
from pjlink_advance import protocol

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PROBED_COMMANDS,
    SLOW_INTERVAL,
    TRANSITION_INTERVAL,
)

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

_LOGGER = logging.getLogger(__name__)

_TRANSITIONAL = (PowerState.WARMING, PowerState.COOLING)


class PJLinkCoordinator(DataUpdateCoordinator[ProjectorState]):
    """Poll one projector and keep its static information."""

    config_entry: PJLinkConfigEntry
    device: DeviceInfo

    def __init__(
        self, hass: HomeAssistant, entry: PJLinkConfigEntry, client: PJLinkClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.unsupported: set[str] = set()
        self.push_active = False
        self._last_full = 0.0
        self._force_full = True

    @property
    def pj_class(self) -> int:
        """Return the PJLink class of the projector."""
        return self.device.pj_class

    def supports(self, command: str) -> bool:
        """Return True if a Class 2 command can be used."""
        return self.pj_class >= 2 and command not in self.unsupported

    @property
    def _idle_interval(self) -> timedelta:
        if self.push_active:
            return timedelta(seconds=SLOW_INTERVAL)
        return timedelta(
            seconds=self.config_entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            )
        )

    async def _async_setup(self) -> None:
        """Read static information once."""
        try:
            self.device = await self.client.fetch_static()
            if self.device.pj_class >= 2:
                await self._probe()
        except PJLinkAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except PJLinkError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _probe(self) -> None:
        for command in PROBED_COMMANDS:
            try:
                await self.client.send_raw(command, "?", 2)
            except PJLinkUndefinedCommand:
                self.unsupported.add(command)
            except PJLinkError:
                # ERR3 and friends mean the command exists.
                pass

    async def _async_update_data(self) -> ProjectorState:
        now = time.monotonic()
        full = self._force_full or now - self._last_full >= SLOW_INTERVAL
        try:
            state = await self.client.fetch_state(full=full, pj_class=self.pj_class)
            if state.power.is_on and not self.device.inputs:
                # Some projectors only list inputs once they are on.
                self.device = await self.client.fetch_static()
        except PJLinkAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except PJLinkError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        if full:
            self._last_full = now
            self._force_full = False
        self._merge_previous(state, full)
        self._adapt_interval(state.power)
        return state

    def _merge_previous(self, state: ProjectorState, full: bool) -> None:
        """Keep values the projector did not report this time."""
        previous = self.data
        if previous is None:
            return
        if not full:
            state.errors = previous.errors
            state.lamps = previous.lamps
            state.filter_hours = previous.filter_hours
        if state.power.is_on and previous.power.is_on:
            # ERR3 while warming up: keep what we knew.
            if state.input is None:
                state.input = previous.input
            if state.mute is None:
                state.mute = previous.mute
            if state.freeze is None:
                state.freeze = previous.freeze
            if state.input_resolution is None:
                state.input_resolution = previous.input_resolution

    def _adapt_interval(self, power: PowerState) -> None:
        if power in _TRANSITIONAL:
            self.update_interval = timedelta(seconds=TRANSITION_INTERVAL)
        else:
            self.update_interval = self._idle_interval

    async def async_command(
        self, func: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any
    ) -> None:
        """Run a projector command, then refresh."""
        try:
            await func(*args, **kwargs)
        except PJLinkUndefinedCommand as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_supported",
                translation_placeholders={"command": err.command},
            ) from err
        except PJLinkError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.async_request_refresh()

    @callback
    def handle_notification(self, command: str, value: str) -> None:
        """Apply a Class 2 status notification."""
        if self.data is None:
            return
        _LOGGER.debug("%s notification %s=%s", self.name, command, value)
        state = dataclasses.replace(self.data)
        try:
            if command == "POWR":
                state.power = protocol.decode_power(value)
            elif command == "INPT":
                state.input = protocol.decode_input(value)
            elif command == "AVMT":
                state.mute = protocol.decode_av_mute(value)
            elif command == "ERST":
                state.errors = protocol.decode_errors(value)
                self._force_full = True
            else:
                # LKUP: projector came online; read everything.
                self._force_full = True
                self.config_entry.async_create_background_task(
                    self.hass, self.async_request_refresh(), "pjlink_lkup_refresh"
                )
                return
        except PJLinkProtocolError:
            _LOGGER.debug("Invalid notification %s=%s", command, value)
            return
        self._adapt_interval(state.power)
        self.async_set_updated_data(state)
        if command == "POWR":
            # Input, mute and resolution may have changed too.
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), "pjlink_power_refresh"
            )
