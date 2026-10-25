"""Buttons for PJLink projectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pjlink_advance import PowerState

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PJLinkCoordinator
from .entity import PJLinkEntity

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class PJLinkButtonEntityDescription(ButtonEntityDescription):
    """Describe a PJLink volume button."""

    up: bool


BUTTONS: tuple[PJLinkButtonEntityDescription, ...] = (
    PJLinkButtonEntityDescription(
        key="mic_volume_up",
        translation_key="mic_volume_up",
        entity_registry_enabled_default=False,
        up=True,
    ),
    PJLinkButtonEntityDescription(
        key="mic_volume_down",
        translation_key="mic_volume_down",
        entity_registry_enabled_default=False,
        up=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PJLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up PJLink buttons (Class 2 only)."""
    coordinator = entry.runtime_data
    if not coordinator.supports("MVOL"):
        return
    async_add_entities(
        PJLinkMicVolumeButton(coordinator, description) for description in BUTTONS
    )


class PJLinkMicVolumeButton(PJLinkEntity, ButtonEntity):
    """Step the microphone volume."""

    entity_description: PJLinkButtonEntityDescription

    def __init__(
        self,
        coordinator: PJLinkCoordinator,
        description: PJLinkButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return True only while the projector is on."""
        return super().available and self.coordinator.data.power is PowerState.ON

    async def async_press(self) -> None:
        """Press the button."""
        await self.coordinator.async_command(
            self.coordinator.client.mic_volume, up=self.entity_description.up
        )
