"""Base entity for PJLink."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MAC, DOMAIN
from .coordinator import PJLinkCoordinator


class PJLinkEntity(CoordinatorEntity[PJLinkCoordinator]):
    """An entity that belongs to one projector."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PJLinkCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        device = coordinator.device
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        mac = entry.data.get(CONF_MAC)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=device.manufacturer or None,
            model=device.model or None,
            serial_number=device.serial,
            sw_version=device.software_version,
            connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        )
