"""Tests for the projector emulator, talking raw PJLink over TCP."""

import asyncio

import pytest

from pjlink_advance.emulator import PJLinkEmulator
from pjlink_advance.models import ErrorLevel, PowerState
from pjlink_advance.protocol import auth_digest


async def _open(
    emulator: PJLinkEmulator,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", emulator.port)
    greeting = (await reader.readuntil(b"\r")).decode().rstrip("\r")
    return reader, writer, greeting


async def _ask(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, line: str
) -> str:
    writer.write(f"{line}\r".encode())
    await writer.drain()
    return (await reader.readuntil(b"\r")).decode().rstrip("\r")


async def test_greeting_without_password() -> None:
    async with PJLinkEmulator() as emulator:
        reader, writer, greeting = await _open(emulator)
        assert greeting == "PJLINK 0"
        assert await _ask(reader, writer, "%1POWR ?") == "%1POWR=0"
        writer.close()


async def test_password_success_and_failure() -> None:
    async with PJLinkEmulator(password="secret") as emulator:
        reader, writer, greeting = await _open(emulator)
        salt = greeting.split(" ")[2]
        assert len(salt) == 8
        digest = auth_digest(salt, "secret")
        assert await _ask(reader, writer, f"{digest}%1POWR ?") == "%1POWR=0"
        assert await _ask(reader, writer, "%1CLSS ?") == "%1CLSS=2"
        writer.close()

        reader, writer, _ = await _open(emulator)
        assert await _ask(reader, writer, f"{'0' * 32}%1POWR ?") == "PJLINK ERRA"
        assert await reader.read() == b""


async def test_power_cycle() -> None:
    async with PJLinkEmulator() as emulator:
        reader, writer, _ = await _open(emulator)
        assert await _ask(reader, writer, "%1INPT ?") == "%1INPT=ERR3"
        assert await _ask(reader, writer, "%1POWR 1") == "%1POWR=OK"
        assert emulator.state.power is PowerState.ON
        assert await _ask(reader, writer, "%1POWR 0") == "%1POWR=OK"
        assert emulator.state.power is PowerState.OFF
        assert await _ask(reader, writer, "%1POWR 7") == "%1POWR=ERR2"
        writer.close()


async def test_power_transition_delay() -> None:
    async with PJLinkEmulator(transition_delay=0.05) as emulator:
        reader, writer, _ = await _open(emulator)
        await _ask(reader, writer, "%1POWR 1")
        assert await _ask(reader, writer, "%1POWR ?") == "%1POWR=3"
        await asyncio.sleep(0.1)
        assert await _ask(reader, writer, "%1POWR ?") == "%1POWR=1"
        writer.close()


async def test_round_trips_when_on() -> None:
    async with PJLinkEmulator() as emulator:
        emulator.state.power = PowerState.ON
        reader, writer, _ = await _open(emulator)
        assert await _ask(reader, writer, "%1INPT 32") == "%1INPT=OK"
        assert await _ask(reader, writer, "%1INPT ?") == "%1INPT=32"
        assert await _ask(reader, writer, "%1INPT 39") == "%1INPT=ERR2"
        assert await _ask(reader, writer, "%1AVMT 11") == "%1AVMT=OK"
        assert await _ask(reader, writer, "%1AVMT ?") == "%1AVMT=11"
        assert await _ask(reader, writer, "%1AVMT 21") == "%1AVMT=OK"
        assert await _ask(reader, writer, "%1AVMT ?") == "%1AVMT=31"
        assert await _ask(reader, writer, "%1AVMT 30") == "%1AVMT=OK"
        assert await _ask(reader, writer, "%1AVMT ?") == "%1AVMT=30"
        assert await _ask(reader, writer, "%2FREZ 1") == "%2FREZ=OK"
        assert await _ask(reader, writer, "%2FREZ ?") == "%2FREZ=1"
        assert await _ask(reader, writer, "%2SVOL 1") == "%2SVOL=OK"
        assert emulator.state.speaker_volume == 1
        assert await _ask(reader, writer, "%2INNM ?31") == "%2INNM=HDMI1"
        assert await _ask(reader, writer, "%2IRES ?") == "%2IRES=1920x1200"
        assert await _ask(reader, writer, "%1LAMP ?") == "%1LAMP=1200 1"
        writer.close()


async def test_info_queries() -> None:
    async with PJLinkEmulator() as emulator:
        emulator.state.errors = emulator.state.errors.replace(filter=ErrorLevel.WARNING)
        reader, writer, _ = await _open(emulator)
        assert await _ask(reader, writer, "%1INF1 ?") == "%1INF1=EPSON"
        assert await _ask(reader, writer, "%1INF2 ?") == "%1INF2=EB-L735U"
        assert await _ask(reader, writer, "%1ERST ?") == "%1ERST=000010"
        assert await _ask(reader, writer, "%1INST ?") == "%1INST=11 31 32 51"
        assert await _ask(reader, writer, "%2SNUM ?") == "%2SNUM=X8JK0000001"
        assert await _ask(reader, writer, "%2FILT ?") == "%2FILT=300"
        assert await _ask(reader, writer, "%2RFIL ?") == "%2RFIL=ELPAF60"
        writer.close()


async def test_class1_emulator_rejects_class2() -> None:
    async with PJLinkEmulator(pj_class=1) as emulator:
        reader, writer, _ = await _open(emulator)
        assert await _ask(reader, writer, "%1CLSS ?") == "%1CLSS=1"
        assert await _ask(reader, writer, "%2SNUM ?") == "%2SNUM=ERR1"
        writer.close()


async def test_unsupported_unavailable_and_fail_next() -> None:
    async with PJLinkEmulator() as emulator:
        emulator.unsupported.add("FREZ")
        emulator.unavailable.add("NAME")
        reader, writer, _ = await _open(emulator)
        assert await _ask(reader, writer, "%2FREZ ?") == "%2FREZ=ERR1"
        assert await _ask(reader, writer, "%1NAME ?") == "%1NAME=ERR3"
        emulator.fail_next = "ERR4"
        assert await _ask(reader, writer, "%1POWR ?") == "%1POWR=ERR4"
        assert await _ask(reader, writer, "%1POWR ?") == "%1POWR=0"
        assert emulator.received[-1] == "%1POWR ?"
        writer.close()


async def test_idle_timeout_closes_connection() -> None:
    async with PJLinkEmulator(idle_timeout=0.05) as emulator:
        reader, writer, _ = await _open(emulator)
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()


@pytest.mark.parametrize("encoding", ["latin-1"])
async def test_name_encoding(encoding: str) -> None:
    async with PJLinkEmulator(encoding=encoding) as emulator:
        emulator.state.name = "Café Projector"
        reader, writer, _ = await _open(emulator)
        writer.write(b"%1NAME ?\r")
        await writer.drain()
        raw = await reader.readuntil(b"\r")
        assert raw == "%1NAME=Café Projector\r".encode(encoding)
        writer.close()
