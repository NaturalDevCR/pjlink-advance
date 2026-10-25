"""Binary sensors for PJLink projectors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PJLinkCoordinator
from .entity import PJLinkEntity

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PJLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up PJLink binary sensors."""
    async_add_entities([PJLinkProblemSensor(entry.runtime_data)])


class PJLinkProblemSensor(PJLinkEntity, BinarySensorEntity):
    """On when any error category reports a warning or an error."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "problem"

    def __init__(self, coordinator: PJLinkCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, "problem")

    @property
    def is_on(self) -> bool | None:
        """Return True if the projector reports a problem."""
        errors = self.coordinator.data.errors
        return errors.has_problem if errors else None
