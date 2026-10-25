"""Pure PJLink encoding and decoding. No I/O happens here."""

from __future__ import annotations

import hashlib
import re

from .errors import COMMAND_ERRORS, PJLinkAuthError, PJLinkProtocolError
from .models import (
    ERROR_CATEGORIES,
    NO_SIGNAL,
    UNKNOWN_RESOLUTION,
    ErrorLevel,
    ErrorStatus,
    InputSource,
    Lamp,
    MuteState,
    PowerState,
    Resolution,
    ResolutionValue,
)

PORT = 4352
MAX_RESPONSE = 136
TERMINATOR = b"\r"

GREETING_PREFIX = "PJLINK "
AUTH_ERROR = "PJLINK ERRA"

NOTIFICATION_COMMANDS = frozenset({"LKUP", "ERST", "POWR", "INPT", "AVMT"})

_COMMAND_RE = re.compile(r"^[A-Z0-9]{4}$")
_RESPONSE_RE = re.compile(r"^%(?P<cls>[12])(?P<cmd>[A-Z0-9]{4})=(?P<value>.*)$")
_RESOLUTION_RE = re.compile(r"^(?P<w>\d+)x(?P<h>\d+)$")

_POWER = {
    "0": PowerState.OFF,
    "1": PowerState.ON,
    "2": PowerState.COOLING,
    "3": PowerState.WARMING,
}
_ERROR_LEVELS = {"0": ErrorLevel.OK, "1": ErrorLevel.WARNING, "2": ErrorLevel.ERROR}


def encode_command(pj_class: int, command: str, param: str = "?") -> bytes:
    """Build a request line such as ``%1POWR ?\\r``."""
    if pj_class not in (1, 2):
        raise ValueError(f"Invalid PJLink class: {pj_class}")
    if not _COMMAND_RE.match(command):
        raise ValueError(f"Invalid PJLink command: {command!r}")
    if "\r" in param or "\n" in param:
        raise ValueError("Parameter must not contain line breaks")
    return f"%{pj_class}{command} {param}\r".encode()


def auth_digest(salt: str, password: str) -> str:
    """Return the MD5 digest sent before the first command."""
    return hashlib.md5(f"{salt}{password}".encode()).hexdigest()  # noqa: S324


def parse_greeting(line: str) -> str | None:
    """Parse the greeting. Return the salt when a password is required."""
    if line == AUTH_ERROR:
        raise PJLinkAuthError("Projector rejected authentication")
    if not line.startswith(GREETING_PREFIX):
        raise PJLinkProtocolError(f"Unexpected greeting: {line!r}")
    parts = line.split(" ")
    if parts[1] == "0" and len(parts) == 2:
        return None
    if parts[1] == "1" and len(parts) == 3 and parts[2]:
        return parts[2]
    raise PJLinkProtocolError(f"Unexpected greeting: {line!r}")


def parse_response(line: str, pj_class: int, command: str) -> str:
    """Return the value of a response, raising on PJLink errors."""
    if line == AUTH_ERROR:
        raise PJLinkAuthError("Projector rejected authentication")
    match = _RESPONSE_RE.match(line)
    if (
        not match
        or match["cmd"] != command
        or int(match["cls"]) != pj_class
    ):
        raise PJLinkProtocolError(
            f"Unexpected response to %{pj_class}{command}: {line!r}"
        )
    value = match["value"]
    if error := COMMAND_ERRORS.get(value):
        raise error(command)
    return value


def decode_power(value: str) -> PowerState:
    """Decode POWR."""
    try:
        return _POWER[value]
    except KeyError:
        raise PJLinkProtocolError(f"Invalid power state: {value!r}") from None


def decode_input(value: str) -> InputSource:
    """Decode INPT."""
    try:
        return InputSource.from_code(value)
    except ValueError as err:
        raise PJLinkProtocolError(str(err)) from err


def decode_inputs(value: str) -> list[InputSource]:
    """Decode INST."""
    return [decode_input(code) for code in value.split()]


def decode_av_mute(value: str) -> MuteState:
    """Decode AVMT."""
    if len(value) != 2 or value[0] not in "123" or value[1] not in "01":
        raise PJLinkProtocolError(f"Invalid mute state: {value!r}")
    on = value[1] == "1"
    target = value[0]
    return MuteState(
        video=on and target in "13",
        audio=on and target in "23",
    )


def decode_errors(value: str) -> ErrorStatus:
    """Decode ERST."""
    if len(value) != len(ERROR_CATEGORIES) or any(c not in _ERROR_LEVELS for c in value):
        raise PJLinkProtocolError(f"Invalid error status: {value!r}")
    return ErrorStatus(*(_ERROR_LEVELS[c] for c in value))


def decode_lamps(value: str) -> list[Lamp]:
    """Decode LAMP (pairs of hours and on/off)."""
    parts = value.split()
    if not parts or len(parts) % 2:
        raise PJLinkProtocolError(f"Invalid lamp status: {value!r}")
    lamps: list[Lamp] = []
    for hours, on in zip(parts[::2], parts[1::2], strict=True):
        lamps.append(Lamp(decode_int(hours), decode_bool(on)))
    return lamps


def decode_resolution(value: str) -> ResolutionValue:
    """Decode IRES / RRES."""
    if value == "-":
        return NO_SIGNAL
    if value == "*":
        return UNKNOWN_RESOLUTION
    match = _RESOLUTION_RE.match(value)
    if not match:
        raise PJLinkProtocolError(f"Invalid resolution: {value!r}")
    return Resolution(int(match["w"]), int(match["h"]))


def decode_bool(value: str) -> bool:
    """Decode a ``0``/``1`` flag."""
    if value == "1":
        return True
    if value == "0":
        return False
    raise PJLinkProtocolError(f"Invalid flag: {value!r}")


def decode_int(value: str) -> int:
    """Decode a non-negative integer."""
    if not value.isdigit():
        raise PJLinkProtocolError(f"Invalid number: {value!r}")
    return int(value)


def parse_notification(line: str) -> tuple[str, str] | None:
    """Parse a Class 2 status notification, e.g. ``%2POWR=1``."""
    match = _RESPONSE_RE.match(line)
    if not match or match["cls"] != "2" or match["cmd"] not in NOTIFICATION_COMMANDS:
        return None
    return match["cmd"], match["value"]


def parse_ack(line: str) -> str | None:
    """Parse a search reply ``%2ACKN=<mac>`` and return the MAC address."""
    match = _RESPONSE_RE.match(line)
    if not match or match["cls"] != "2" or match["cmd"] != "ACKN":
        return None
    return match["value"]
