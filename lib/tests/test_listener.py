"""Tests for the UDP listener (search + status notifications)."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from pjlink_advance.emulator import PJLinkEmulator
from pjlink_advance.listener import PJLinkListener


@pytest.fixture
async def listener() -> AsyncIterator[PJLinkListener]:
    lst = PJLinkListener(host="127.0.0.1", port=0)
    await lst.start()
    yield lst
    await lst.stop()


async def test_discover(listener: PJLinkListener) -> None:
    async with PJLinkEmulator() as emulator:
        found = await listener.discover(
            duration=0.2, broadcast="127.0.0.1", target_port=emulator.udp_port
        )
    assert found == [("127.0.0.1", "00:26:ab:00:00:01")]


async def test_discover_ignores_class1(listener: PJLinkListener) -> None:
    async with PJLinkEmulator(pj_class=1) as emulator:
        found = await listener.discover(
            duration=0.2, broadcast="127.0.0.1", target_port=emulator.udp_port
        )
    assert found == []


async def test_notification_reaches_callback(listener: PJLinkListener) -> None:
    received: list[tuple[str, str]] = []
    event = asyncio.Event()

    def callback(command: str, value: str) -> None:
        received.append((command, value))
        event.set()

    unregister = listener.register("127.0.0.1", callback)
    async with PJLinkEmulator() as emulator:
        await emulator.notify("POWR", "1", ("127.0.0.1", listener.port))
        await asyncio.wait_for(event.wait(), 1)
        assert received == [("POWR", "1")]

        unregister()
        event.clear()
        await emulator.notify("POWR", "0", ("127.0.0.1", listener.port))
        await asyncio.sleep(0.05)
    assert received == [("POWR", "1")]


async def test_notification_from_unknown_ip_ignored(listener: PJLinkListener) -> None:
    received: list[tuple[str, str]] = []
    listener.register("10.0.0.99", lambda c, v: received.append((c, v)))
    async with PJLinkEmulator() as emulator:
        await emulator.notify("POWR", "1", ("127.0.0.1", listener.port))
        await asyncio.sleep(0.05)
    assert received == []


async def test_callback_errors_do_not_break_listener(listener: PJLinkListener) -> None:
    received: list[str] = []

    def bad(_command: str, _value: str) -> None:
        raise RuntimeError("boom")

    listener.register("127.0.0.1", bad)
    listener.register("127.0.0.1", lambda c, _v: received.append(c))
    async with PJLinkEmulator() as emulator:
        await emulator.notify("ERST", "000000", ("127.0.0.1", listener.port))
        await asyncio.sleep(0.05)
    assert received == ["ERST"]
