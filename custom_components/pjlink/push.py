"""Shared UDP listener for Class 2 search replies and status notifications."""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass

from pjlink_advance import PJLinkListener

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.hass_dict import HassKey

from . import const
from .coordinator import PJLinkCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class _SharedListener:
    listener: PJLinkListener
    users: int = 0


DATA_LISTENER: HassKey[_SharedListener] = HassKey(f"{const.DOMAIN}_listener")


async def _async_acquire(hass: HomeAssistant) -> PJLinkListener:
    """Return the shared listener, starting it if needed."""
    if (shared := hass.data.get(DATA_LISTENER)) is None:
        listener = PJLinkListener(port=const.PUSH_PORT)
        await listener.start()
        shared = hass.data[DATA_LISTENER] = _SharedListener(listener)
    shared.users += 1
    return shared.listener


@callback
def _async_release(hass: HomeAssistant) -> None:
    if (shared := hass.data.get(DATA_LISTENER)) is None:
        return
    shared.users -= 1
    if shared.users <= 0:
        hass.data.pop(DATA_LISTENER)
        hass.async_create_task(shared.listener.stop(), eager_start=True)


async def _discover(listener: PJLinkListener) -> list[tuple[str, str]]:
    return await listener.discover(
        duration=const.SEARCH_DURATION,
        broadcast=const.SEARCH_BROADCAST,
        target_port=const.SEARCH_TARGET_PORT,
    )


async def async_search(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Broadcast a Class 2 search and return ``(ip, mac)`` pairs."""
    try:
        listener = await _async_acquire(hass)
    except OSError:
        # Port 4352 is busy; fall back to an ephemeral port.
        _LOGGER.debug("UDP port %s busy, searching from ephemeral port", const.PUSH_PORT)
        listener = PJLinkListener(port=0)
        await listener.start()
        try:
            return await _discover(listener)
        finally:
            await listener.stop()
    try:
        return await _discover(listener)
    finally:
        _async_release(hass)


async def async_enable_push(
    hass: HomeAssistant, coordinator: PJLinkCoordinator
) -> Callable[[], None] | None:
    """Route notifications to the coordinator. Returns an unsubscribe callback."""
    host = coordinator.config_entry.data[CONF_HOST]
    try:
        ip = await hass.async_add_executor_job(socket.gethostbyname, host)
        listener = await _async_acquire(hass)
    except OSError as err:
        _LOGGER.warning(
            "Cannot receive PJLink notifications for %s (%s); polling only", host, err
        )
        return None
    unregister = listener.register(ip, coordinator.handle_notification)
    coordinator.push_active = True

    @callback
    def _unsubscribe() -> None:
        unregister()
        coordinator.push_active = False
        _async_release(hass)

    return _unsubscribe
