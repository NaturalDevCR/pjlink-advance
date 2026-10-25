"""Tests for PJLinkClient against the emulator."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from pjlink_advance import (
    NO_SIGNAL,
    ErrorLevel,
    InputSource,
    InputType,
    Lamp,
    MuteState,
    MuteTarget,
    PJLinkAuthError,
    PJLinkConnectionError,
    PJLinkOutOfParameter,
    PJLinkUnavailableTime,
    PJLinkUndefinedCommand,
    PowerState,
    Resolution,
)
from pjlink_advance.client import PJLinkClient
from pjlink_advance.emulator import PJLinkEmulator


@pytest.fixture
async def emulator() -> AsyncIterator[PJLinkEmulator]:
    async with PJLinkEmulator() as emu:
        yield emu


@pytest.fixture
async def client(emulator: PJLinkEmulator) -> AsyncIterator[PJLinkClient]:
    async with PJLinkClient("127.0.0.1", emulator.port, timeout=1) as cli:
        yield cli


async def test_power_round_trip(client: PJLinkClient, emulator: PJLinkEmulator) -> None:
    assert await client.get_power() is PowerState.OFF
    await client.set_power(True)
    assert emulator.state.power is PowerState.ON
    assert await client.get_power() is PowerState.ON
    await client.set_power(False)
    assert await client.get_power() is PowerState.OFF


async def test_single_persistent_connection(
    client: PJLinkClient, emulator: PJLinkEmulator
) -> None:
    for _ in range(5):
        await client.get_power()
    assert emulator.connections == 1
    assert client.connected


async def test_reconnects_after_projector_drops(
    client: PJLinkClient, emulator: PJLinkEmulator
) -> None:
    await client.get_power()
    emulator.disconnect_all()
    await asyncio.sleep(0.01)
    assert await client.get_power() is PowerState.OFF
    assert emulator.connections == 2


async def test_reconnects_after_idle_timeout() -> None:
    async with (
        PJLinkEmulator(idle_timeout=0.05) as emulator,
        PJLinkClient("127.0.0.1", emulator.port, timeout=1) as client,
    ):
        await client.get_power()
        await asyncio.sleep(0.1)
        assert await client.get_power() is PowerState.OFF
        assert emulator.connections == 2


async def test_proactive_reconnect_when_idle() -> None:
    async with (
        PJLinkEmulator() as emulator,
        PJLinkClient("127.0.0.1", emulator.port, timeout=1, max_idle=0.01) as client,
    ):
        await client.get_power()
        await asyncio.sleep(0.03)
        await client.get_power()
        assert emulator.connections == 2


async def test_password() -> None:
    async with PJLinkEmulator(password="secret") as emulator:
        async with PJLinkClient("127.0.0.1", emulator.port, "secret") as client:
            assert await client.get_power() is PowerState.OFF
            assert await client.get_class() == 2
        async with PJLinkClient("127.0.0.1", emulator.port, "wrong") as client:
            with pytest.raises(PJLinkAuthError):
                await client.get_power()
        async with PJLinkClient("127.0.0.1", emulator.port) as client:
            with pytest.raises(PJLinkAuthError):
                await client.get_power()


async def test_command_errors(client: PJLinkClient, emulator: PJLinkEmulator) -> None:
    with pytest.raises(PJLinkUnavailableTime):
        await client.get_input()
    emulator.state.power = PowerState.ON
    with pytest.raises(PJLinkOutOfParameter):
        await client.set_input(InputSource(InputType.VIDEO, "9"))
    emulator.unsupported.add("FREZ")
    with pytest.raises(PJLinkUndefinedCommand):
        await client.get_freeze()


async def test_timeout(emulator: PJLinkEmulator) -> None:
    emulator.silent = True
    async with PJLinkClient("127.0.0.1", emulator.port, timeout=0.1) as client:
        with pytest.raises(PJLinkConnectionError):
            await client.get_power()
        assert not client.connected


async def test_unreachable() -> None:
    async with PJLinkEmulator() as emulator:
        port = emulator.port
    async with PJLinkClient("127.0.0.1", port, timeout=0.5) as client:
        with pytest.raises(PJLinkConnectionError):
            await client.get_power()


async def test_drop_after_greeting(emulator: PJLinkEmulator) -> None:
    emulator.drop_after_greeting = True
    async with PJLinkClient("127.0.0.1", emulator.port, timeout=0.5) as client:
        with pytest.raises(PJLinkConnectionError):
            await client.get_power()


async def test_controls(client: PJLinkClient, emulator: PJLinkEmulator) -> None:
    emulator.state.power = PowerState.ON
    await client.set_input(InputSource(InputType.DIGITAL, "2"))
    assert await client.get_input() == InputSource(InputType.DIGITAL, "2")
    await client.set_av_mute(MuteTarget.VIDEO, True)
    assert await client.get_av_mute() == MuteState(video=True, audio=False)
    await client.set_av_mute(MuteTarget.AUDIO, True)
    assert await client.get_av_mute() == MuteState(video=True, audio=True)
    await client.set_av_mute(MuteTarget.BOTH, False)
    assert await client.get_av_mute() == MuteState(video=False, audio=False)
    await client.set_freeze(True)
    assert await client.get_freeze() is True
    await client.speaker_volume(up=True)
    await client.speaker_volume(up=True)
    await client.mic_volume(up=False)
    assert emulator.state.speaker_volume == 2
    assert emulator.state.mic_volume == -1
    assert await client.send_raw("INF1") == "EPSON"
    assert await client.send_raw("SNUM", pj_class=2) == "X8JK0000001"


async def test_extended_input_uses_class2(
    client: PJLinkClient, emulator: PJLinkEmulator
) -> None:
    extended = InputSource(InputType.DIGITAL, "A")
    emulator.state.inputs.append(extended)
    emulator.state.power = PowerState.ON
    await client.set_input(extended)
    assert emulator.received[-1] == "%2INPT 3A"
    assert extended not in await client.get_inputs()
    assert extended in await client.get_inputs(pj_class=2)


async def test_fetch_static_class2(client: PJLinkClient) -> None:
    info = await client.fetch_static()
    assert info.pj_class == 2
    assert info.name == "EB-L735U"
    assert info.manufacturer == "EPSON"
    assert info.model == "EB-L735U"
    assert info.serial == "X8JK0000001"
    assert info.software_version == "1.00"
    assert info.input_names["31"] == "HDMI1"
    assert len(info.inputs) == 4
    assert info.lamp_model is None
    assert info.filter_model == "ELPAF60"
    assert info.recommended_resolution == Resolution(1920, 1200)


async def test_fetch_static_class1() -> None:
    async with (
        PJLinkEmulator(pj_class=1) as emulator,
        PJLinkClient("127.0.0.1", emulator.port) as client,
    ):
        info = await client.fetch_static()
    assert info.pj_class == 1
    assert info.serial is None
    assert info.software_version is None
    assert info.input_names == {}
    assert info.filter_model is None


async def test_fetch_state_off(client: PJLinkClient) -> None:
    state = await client.fetch_state(full=True, pj_class=2)
    assert state.power is PowerState.OFF
    assert state.input is None
    assert state.errors is not None
    assert state.lamps == [Lamp(1200, False)]
    assert state.filter_hours == 300


async def test_fetch_state_on(client: PJLinkClient, emulator: PJLinkEmulator) -> None:
    emulator.state.power = PowerState.ON
    emulator.state.input_resolution = NO_SIGNAL
    state = await client.fetch_state(full=False, pj_class=2)
    assert state.power is PowerState.ON
    assert state.input == InputSource(InputType.DIGITAL, "1")
    assert state.mute == MuteState(video=False, audio=False)
    assert state.freeze is False
    assert state.input_resolution == NO_SIGNAL
    assert state.errors is None
    assert state.lamps is None


async def test_fetch_state_tolerates_unavailable(
    client: PJLinkClient, emulator: PJLinkEmulator
) -> None:
    emulator.state.power = PowerState.ON
    emulator.unavailable.update({"INPT", "IRES"})
    emulator.state.errors = emulator.state.errors.replace(lamp=ErrorLevel.ERROR)
    state = await client.fetch_state(full=True, pj_class=2)
    assert state.input is None
    assert state.input_resolution is None
    assert state.errors is not None
    assert state.errors.lamp is ErrorLevel.ERROR


async def test_latin1_fallback() -> None:
    async with PJLinkEmulator(encoding="latin-1") as emulator:
        emulator.state.name = "Café Projector"
        async with PJLinkClient("127.0.0.1", emulator.port) as client:
            assert await client.get_name() == "Café Projector"


async def test_concurrent_commands_are_serialized(
    client: PJLinkClient, emulator: PJLinkEmulator
) -> None:
    results = await asyncio.gather(*(client.get_power() for _ in range(10)))
    assert results == [PowerState.OFF] * 10
    assert emulator.connections == 1
