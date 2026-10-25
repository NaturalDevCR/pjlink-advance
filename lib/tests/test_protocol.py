"""Tests for pjlink_advance.protocol."""

import pytest

from pjlink_advance import protocol
from pjlink_advance.errors import (
    PJLinkAuthError,
    PJLinkOutOfParameter,
    PJLinkProjectorFailure,
    PJLinkProtocolError,
    PJLinkUnavailableTime,
    PJLinkUndefinedCommand,
)
from pjlink_advance.models import (
    NO_SIGNAL,
    UNKNOWN_RESOLUTION,
    ErrorLevel,
    InputSource,
    InputType,
    Lamp,
    MuteState,
    PowerState,
    Resolution,
)


def test_encode_command() -> None:
    assert protocol.encode_command(1, "POWR") == b"%1POWR ?\r"
    assert protocol.encode_command(2, "INPT", "3A") == b"%2INPT 3A\r"


@pytest.mark.parametrize(
    ("pj_class", "command"), [(3, "POWR"), (1, "POW"), (1, "powr"), (1, "POWRX")]
)
def test_encode_command_invalid(pj_class: int, command: str) -> None:
    with pytest.raises(ValueError):
        protocol.encode_command(pj_class, command)


def test_encode_command_rejects_carriage_return() -> None:
    with pytest.raises(ValueError):
        protocol.encode_command(1, "NAME", "a\rb")


def test_auth_digest_spec_vector() -> None:
    assert (
        protocol.auth_digest("498e4a67", "JBMIAProjectorLink")
        == "5d8409bc1c3fa39749434aa3a5c38682"
    )


def test_parse_greeting() -> None:
    assert protocol.parse_greeting("PJLINK 0") is None
    assert protocol.parse_greeting("PJLINK 1 498e4a67") == "498e4a67"
    with pytest.raises(PJLinkAuthError):
        protocol.parse_greeting("PJLINK ERRA")
    with pytest.raises(PJLinkProtocolError):
        protocol.parse_greeting("HELLO")


def test_parse_response_value() -> None:
    assert protocol.parse_response("%1POWR=1", 1, "POWR") == "1"
    assert protocol.parse_response("%1NAME=", 1, "NAME") == ""
    assert protocol.parse_response("%2INNM=HDMI 1", 2, "INNM") == "HDMI 1"


@pytest.mark.parametrize(
    ("code", "exc"),
    [
        ("ERR1", PJLinkUndefinedCommand),
        ("ERR2", PJLinkOutOfParameter),
        ("ERR3", PJLinkUnavailableTime),
        ("ERR4", PJLinkProjectorFailure),
    ],
)
def test_parse_response_errors(code: str, exc: type[Exception]) -> None:
    with pytest.raises(exc):
        protocol.parse_response(f"%1POWR={code}", 1, "POWR")


def test_parse_response_auth_error() -> None:
    with pytest.raises(PJLinkAuthError):
        protocol.parse_response("PJLINK ERRA", 1, "POWR")


@pytest.mark.parametrize("line", ["%1INPT=31", "%2POWR=1", "POWR=1", "%1POWR 1"])
def test_parse_response_mismatch(line: str) -> None:
    with pytest.raises(PJLinkProtocolError):
        protocol.parse_response(line, 1, "POWR")


@pytest.mark.parametrize(
    ("value", "state"),
    [
        ("0", PowerState.OFF),
        ("1", PowerState.ON),
        ("2", PowerState.COOLING),
        ("3", PowerState.WARMING),
    ],
)
def test_decode_power(value: str, state: PowerState) -> None:
    assert protocol.decode_power(value) is state


def test_decode_power_invalid() -> None:
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_power("9")


def test_decode_input() -> None:
    assert protocol.decode_input("31") == InputSource(InputType.DIGITAL, "1")
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_input("99")


def test_decode_inputs() -> None:
    assert protocol.decode_inputs("11 31 32") == [
        InputSource(InputType.RGB, "1"),
        InputSource(InputType.DIGITAL, "1"),
        InputSource(InputType.DIGITAL, "2"),
    ]
    assert protocol.decode_inputs("") == []


@pytest.mark.parametrize(
    ("value", "state"),
    [
        ("11", MuteState(video=True, audio=False)),
        ("21", MuteState(video=False, audio=True)),
        ("31", MuteState(video=True, audio=True)),
        ("30", MuteState(video=False, audio=False)),
        ("10", MuteState(video=False, audio=False)),
        ("20", MuteState(video=False, audio=False)),
    ],
)
def test_decode_av_mute(value: str, state: MuteState) -> None:
    assert protocol.decode_av_mute(value) == state


def test_decode_av_mute_invalid() -> None:
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_av_mute("41")


def test_decode_errors() -> None:
    status = protocol.decode_errors("012000")
    assert status.fan is ErrorLevel.OK
    assert status.lamp is ErrorLevel.WARNING
    assert status.temperature is ErrorLevel.ERROR
    assert status.has_problem
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_errors("01200")
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_errors("013000")


def test_decode_lamps() -> None:
    assert protocol.decode_lamps("1200 1") == [Lamp(1200, True)]
    assert protocol.decode_lamps("10 0 20 1") == [Lamp(10, False), Lamp(20, True)]
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_lamps("10")
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_lamps("x 1")


def test_decode_resolution() -> None:
    assert protocol.decode_resolution("1920x1200") == Resolution(1920, 1200)
    assert protocol.decode_resolution("-") == NO_SIGNAL
    assert protocol.decode_resolution("*") == UNKNOWN_RESOLUTION
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_resolution("big")


def test_decode_bool_and_int() -> None:
    assert protocol.decode_bool("1") is True
    assert protocol.decode_bool("0") is False
    assert protocol.decode_int("0300") == 300
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_bool("2")
    with pytest.raises(PJLinkProtocolError):
        protocol.decode_int("abc")


def test_parse_notification() -> None:
    assert protocol.parse_notification("%2POWR=1") == ("POWR", "1")
    assert protocol.parse_notification("%2LKUP=00:11:22:33:44:55") == (
        "LKUP",
        "00:11:22:33:44:55",
    )
    assert protocol.parse_notification("%2ACKN=00:11:22:33:44:55") is None
    assert protocol.parse_notification("garbage") is None


def test_parse_ack() -> None:
    assert protocol.parse_ack("%2ACKN=00:11:22:33:44:55") == "00:11:22:33:44:55"
    assert protocol.parse_ack("%2POWR=1") is None
