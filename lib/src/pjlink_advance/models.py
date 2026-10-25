"""Data types used by pjlink_advance."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

_CLASS1_INDEXES = "123456789"
_CLASS2_INDEXES = _CLASS1_INDEXES + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

NO_SIGNAL = "no_signal"
UNKNOWN_RESOLUTION = "unknown"


class PowerState(StrEnum):
    """Projector power state (POWR)."""

    OFF = "off"
    ON = "on"
    COOLING = "cooling"
    WARMING = "warming"

    @property
    def is_on(self) -> bool:
        """Return True when the projector is showing or about to show an image."""
        return self in (PowerState.ON, PowerState.WARMING)


class InputType(IntEnum):
    """Input terminal type (first digit of INPT)."""

    RGB = 1
    VIDEO = 2
    DIGITAL = 3
    STORAGE = 4
    NETWORK = 5
    INTERNAL = 6


@dataclass(frozen=True, slots=True)
class InputSource:
    """An input terminal, e.g. ``31`` (DIGITAL 1)."""

    type: InputType
    index: str

    def __post_init__(self) -> None:
        if len(self.index) != 1 or self.index not in _CLASS2_INDEXES:
            raise ValueError(f"Invalid input index: {self.index!r}")

    @classmethod
    def from_code(cls, code: str) -> InputSource:
        """Parse a two character input code."""
        if len(code) != 2 or not code[0].isdigit():
            raise ValueError(f"Invalid input code: {code!r}")
        try:
            input_type = InputType(int(code[0]))
        except ValueError as err:
            raise ValueError(f"Invalid input type in {code!r}") from err
        return cls(input_type, code[1])

    @property
    def code(self) -> str:
        """Return the two character code sent to the projector."""
        return f"{self.type.value}{self.index}"

    @property
    def requires_class2(self) -> bool:
        """Return True if this input can only be addressed with Class 2."""
        return self.type is InputType.INTERNAL or self.index not in _CLASS1_INDEXES

    def __str__(self) -> str:
        return f"{self.type.name} {self.index}"


class MuteTarget(IntEnum):
    """What AVMT mutes."""

    VIDEO = 1
    AUDIO = 2
    BOTH = 3


@dataclass(frozen=True, slots=True)
class MuteState:
    """Audio/video mute state (AVMT)."""

    video: bool
    audio: bool


class ErrorLevel(StrEnum):
    """Severity of one ERST category."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


ERROR_CATEGORIES = ("fan", "lamp", "temperature", "cover", "filter", "other")


@dataclass(frozen=True, slots=True)
class ErrorStatus:
    """Error status (ERST), one level per category."""

    fan: ErrorLevel
    lamp: ErrorLevel
    temperature: ErrorLevel
    cover: ErrorLevel
    filter: ErrorLevel
    other: ErrorLevel

    @classmethod
    def ok(cls) -> ErrorStatus:
        """Return a status with every category OK."""
        return cls(*(ErrorLevel.OK,) * len(ERROR_CATEGORIES))

    @property
    def has_problem(self) -> bool:
        """Return True if any category is not OK."""
        return any(level is not ErrorLevel.OK for level in self.as_dict().values())

    @property
    def has_error(self) -> bool:
        """Return True if any category is in ERROR."""
        return any(level is ErrorLevel.ERROR for level in self.as_dict().values())

    def as_dict(self) -> dict[str, ErrorLevel]:
        """Return the levels keyed by category name."""
        return {name: getattr(self, name) for name in ERROR_CATEGORIES}

    def replace(self, **changes: ErrorLevel) -> ErrorStatus:
        """Return a copy with some categories changed."""
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True, slots=True)
class Lamp:
    """One lamp (or laser light source) reported by LAMP."""

    hours: int
    on: bool


@dataclass(frozen=True, slots=True)
class Resolution:
    """A video resolution."""

    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


type ResolutionValue = Resolution | str | None


@dataclass(slots=True)
class DeviceInfo:
    """Information that rarely changes, read once at setup."""

    name: str
    manufacturer: str
    model: str
    other_info: str
    pj_class: int
    serial: str | None = None
    software_version: str | None = None
    inputs: list[InputSource] = field(default_factory=list)
    input_names: dict[str, str] = field(default_factory=dict)
    lamp_model: str | None = None
    filter_model: str | None = None
    recommended_resolution: ResolutionValue = None


@dataclass(slots=True)
class ProjectorState:
    """Values that change while the projector runs."""

    power: PowerState
    input: InputSource | None = None
    mute: MuteState | None = None
    freeze: bool | None = None
    input_resolution: ResolutionValue = None
    errors: ErrorStatus | None = None
    lamps: list[Lamp] | None = None
    filter_hours: int | None = None
