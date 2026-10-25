"""Sensors for PJLink projectors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from pjlink_advance import (
    ERROR_CATEGORIES,
    NO_SIGNAL,
    ErrorLevel,
    PowerState,
    Resolution,
    ResolutionValue,
)

from .coordinator import PJLinkCoordinator
from .entity import PJLinkEntity

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

PARALLEL_UPDATES = 0

ERROR_OPTIONS = [level.value for level in ErrorLevel]


def _resolution(value: ResolutionValue) -> str | None:
    if isinstance(value, Resolution):
        return str(value)
    if value == NO_SIGNAL:
        return NO_SIGNAL
    return None


@dataclass(frozen=True, kw_only=True)
class PJLinkSensorEntityDescription(SensorEntityDescription):
    """Describe a PJLink sensor."""

    value_fn: Callable[[PJLinkCoordinator], StateType]
    exists_fn: Callable[[PJLinkCoordinator], bool] = lambda _: True


def _error_description(category: str) -> PJLinkSensorEntityDescription:
    def value(coordinator: PJLinkCoordinator) -> StateType:
        errors = coordinator.data.errors
        return errors.as_dict()[category].value if errors else None

    return PJLinkSensorEntityDescription(
        key=f"error_{category}",
        translation_key=f"error_{category}",
        device_class=SensorDeviceClass.ENUM,
        options=ERROR_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=value,
    )


SENSORS: tuple[PJLinkSensorEntityDescription, ...] = (
    PJLinkSensorEntityDescription(
        key="power_status",
        translation_key="power_status",
        device_class=SensorDeviceClass.ENUM,
        options=[state.value for state in PowerState],
        value_fn=lambda c: c.data.power.value,
    ),
    PJLinkSensorEntityDescription(
        key="filter_hours",
        translation_key="filter_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda c: c.data.filter_hours,
        exists_fn=lambda c: c.supports("FILT"),
    ),
    PJLinkSensorEntityDescription(
        key="input_resolution",
        translation_key="input_resolution",
        value_fn=lambda c: (
            _resolution(c.data.input_resolution) if c.data.power.is_on else None
        ),
        exists_fn=lambda c: c.supports("IRES"),
    ),
    PJLinkSensorEntityDescription(
        key="recommended_resolution",
        translation_key="recommended_resolution",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: _resolution(c.device.recommended_resolution),
        exists_fn=lambda c: isinstance(c.device.recommended_resolution, Resolution),
    ),
    PJLinkSensorEntityDescription(
        key="lamp_model",
        translation_key="lamp_model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.device.lamp_model,
        exists_fn=lambda c: bool(c.device.lamp_model),
    ),
    PJLinkSensorEntityDescription(
        key="filter_model",
        translation_key="filter_model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.device.filter_model,
        exists_fn=lambda c: bool(c.device.filter_model),
    ),
    *(_error_description(category) for category in ERROR_CATEGORIES),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PJLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up PJLink sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        PJLinkSensor(coordinator, description)
        for description in SENSORS
        if description.exists_fn(coordinator)
    ]
    lamps = coordinator.data.lamps or []
    entities.extend(
        PJLinkLampHoursSensor(coordinator, index, single=len(lamps) == 1)
        for index in range(len(lamps))
    )
    async_add_entities(entities)


class PJLinkSensor(PJLinkEntity, SensorEntity):
    """A sensor described by a PJLinkSensorEntityDescription."""

    entity_description: PJLinkSensorEntityDescription

    def __init__(
        self,
        coordinator: PJLinkCoordinator,
        description: PJLinkSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator)


class PJLinkLampHoursSensor(PJLinkEntity, SensorEntity):
    """Usage hours of one lamp (or laser light source)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: PJLinkCoordinator, index: int, *, single: bool
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, f"lamp_{index + 1}_hours")
        self._index = index
        if single:
            self._attr_translation_key = "lamp_hours"
        else:
            self._attr_translation_key = "lamp_n_hours"
            self._attr_translation_placeholders = {"number": str(index + 1)}

    @property
    def native_value(self) -> int | None:
        """Return the lamp hours."""
        lamps = self.coordinator.data.lamps
        if not lamps or self._index >= len(lamps):
            return None
        return lamps[self._index].hours
