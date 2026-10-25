"""Switches for PJLink projectors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pjlink_advance import MuteTarget, PowerState

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PJLinkCoordinator
from .entity import PJLinkEntity

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class PJLinkSwitchEntityDescription(SwitchEntityDescription):
    """Describe a PJLink switch."""

    is_on_fn: Callable[[PJLinkCoordinator], bool | None]
    set_fn: Callable[[PJLinkCoordinator, bool], Awaitable[None]]
    exists_fn: Callable[[PJLinkCoordinator], bool] = lambda _: True


SWITCHES: tuple[PJLinkSwitchEntityDescription, ...] = (
    PJLinkSwitchEntityDescription(
        key="video_mute",
        translation_key="video_mute",
        is_on_fn=lambda c: c.data.mute.video if c.data.mute else None,
        set_fn=lambda c, on: c.client.set_av_mute(MuteTarget.VIDEO, on),
    ),
    PJLinkSwitchEntityDescription(
        key="freeze",
        translation_key="freeze",
        is_on_fn=lambda c: c.data.freeze,
        set_fn=lambda c, on: c.client.set_freeze(on),
        exists_fn=lambda c: c.supports("FREZ"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PJLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up PJLink switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        PJLinkSwitch(coordinator, description)
        for description in SWITCHES
        if description.exists_fn(coordinator)
    )


class PJLinkSwitch(PJLinkEntity, SwitchEntity):
    """A switch that only works while the projector is on."""

    entity_description: PJLinkSwitchEntityDescription

    def __init__(
        self,
        coordinator: PJLinkCoordinator,
        description: PJLinkSwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return True only while the projector is on."""
        return super().available and self.coordinator.data.power is PowerState.ON

    @property
    def is_on(self) -> bool | None:
        """Return the switch state."""
        return self.entity_description.is_on_fn(self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.async_command(
            self.entity_description.set_fn, self.coordinator, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.async_command(
            self.entity_description.set_fn, self.coordinator, False
        )
