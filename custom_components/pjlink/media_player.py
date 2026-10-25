"""Media player for PJLink projectors."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components.media_player import (
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
)
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    AddEntitiesCallback,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from pjlink_advance import DeviceInfo, InputSource, MuteTarget

from .const import CONF_ENCODING, DEFAULT_ENCODING, DEFAULT_PORT, DOMAIN
from .coordinator import PJLinkCoordinator
from .entity import PJLinkEntity

if TYPE_CHECKING:
    from . import PJLinkConfigEntry

PARALLEL_UPDATES = 1

PLATFORM_SCHEMA = MEDIA_PLAYER_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_ENCODING, default=DEFAULT_ENCODING): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Import a YAML configuration into a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=config
    )
    if (
        result.get("type") is FlowResultType.ABORT
        and result.get("reason") != "already_configured"
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"deprecated_yaml_import_issue_{result.get('reason')}",
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key=f"deprecated_yaml_import_issue_{result.get('reason')}",
            translation_placeholders={"domain": DOMAIN, "integration_title": "PJLink"},
        )
        return
    ir.async_create_issue(
        hass,
        HOMEASSISTANT_DOMAIN,
        f"deprecated_yaml_{DOMAIN}",
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
        translation_placeholders={"domain": DOMAIN, "integration_title": "PJLink"},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PJLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the PJLink media player."""
    async_add_entities([PJLinkMediaPlayer(entry.runtime_data)])


def source_names(device: DeviceInfo) -> dict[str, InputSource]:
    """Map a display name to every input, using Class 2 names when available."""
    names: dict[str, InputSource] = {}
    for source in device.inputs:
        name = device.input_names.get(source.code) or str(source)
        if name in names:
            name = f"{name} ({source.code})"
        names[name] = source
    return names


class PJLinkMediaPlayer(PJLinkEntity, MediaPlayerEntity):
    """The projector as a media player."""

    _attr_device_class = MediaPlayerDeviceClass.PROJECTOR
    _attr_name = None

    def __init__(self, coordinator: PJLinkCoordinator) -> None:
        """Initialize the media player."""
        super().__init__(coordinator, "media_player")
        # Same unique id as the core integration, so existing entities survive.
        self._attr_unique_id = coordinator.config_entry.entry_id
        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.VOLUME_MUTE
        )
        if coordinator.supports("SVOL"):
            features |= MediaPlayerEntityFeature.VOLUME_STEP
        self._attr_supported_features = features

    @property
    def _sources(self) -> dict[str, InputSource]:
        return source_names(self.coordinator.device)

    @property
    def state(self) -> MediaPlayerState:
        """Return ON while the projector shows (or prepares) an image."""
        if self.coordinator.data.power.is_on:
            return MediaPlayerState.ON
        return MediaPlayerState.OFF

    @property
    def is_volume_muted(self) -> bool | None:
        """Return True if audio is muted."""
        mute = self.coordinator.data.mute
        return mute.audio if mute else None

    @property
    def source(self) -> str | None:
        """Return the selected input."""
        current = self.coordinator.data.input
        if current is None or not self.coordinator.data.power.is_on:
            return None
        for name, source in self._sources.items():
            if source == current:
                return name
        return self.coordinator.device.input_names.get(current.code) or str(current)

    @property
    def source_list(self) -> list[str]:
        """Return the available inputs."""
        return list(self._sources)

    async def async_turn_on(self) -> None:
        """Turn the projector on."""
        await self.coordinator.async_command(self.coordinator.client.set_power, True)

    async def async_turn_off(self) -> None:
        """Turn the projector off."""
        await self.coordinator.async_command(self.coordinator.client.set_power, False)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute audio."""
        await self.coordinator.async_command(
            self.coordinator.client.set_av_mute, MuteTarget.AUDIO, mute
        )

    async def async_select_source(self, source: str) -> None:
        """Select an input."""
        await self.coordinator.async_command(
            self.coordinator.client.set_input, self._sources[source]
        )

    async def async_volume_up(self) -> None:
        """Raise the speaker volume one step."""
        await self.coordinator.async_command(
            self.coordinator.client.speaker_volume, up=True
        )

    async def async_volume_down(self) -> None:
        """Lower the speaker volume one step."""
        await self.coordinator.async_command(
            self.coordinator.client.speaker_volume, up=False
        )
