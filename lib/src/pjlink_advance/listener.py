"""UDP side of PJLink Class 2: search (SRCH/ACKN) and status notifications."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable

from . import protocol

_LOGGER = logging.getLogger(__name__)

type NotificationCallback = Callable[[str, str], None]


class PJLinkListener:
    """One UDP socket shared by every projector.

    Projectors send notifications to UDP port 4352 of the host that last
    talked to them, and answer ``%2SRCH`` broadcasts from the same port.
    """

    def __init__(self, *, host: str = "0.0.0.0", port: int = protocol.PORT) -> None:
        self._host = host
        self._requested_port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._callbacks: dict[str, list[NotificationCallback]] = {}
        self._search_results: list[tuple[str, str]] | None = None

    @property
    def port(self) -> int:
        """Bound UDP port."""
        if self._transport is None:
            raise RuntimeError("Listener not started")
        return self._transport.get_extra_info("sockname")[1]

    @property
    def running(self) -> bool:
        """Return True while the socket is open."""
        return self._transport is not None

    async def start(self) -> None:
        """Bind the UDP socket."""
        if self._transport is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind((self._host, self._requested_port))
        except OSError:
            sock.close()
            raise
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _ListenerProtocol(self), sock=sock
        )

    async def stop(self) -> None:
        """Close the UDP socket."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def register(self, ip: str, callback: NotificationCallback) -> Callable[[], None]:
        """Deliver notifications from ``ip`` to ``callback``. Returns unregister."""
        self._callbacks.setdefault(ip, []).append(callback)

        def unregister() -> None:
            callbacks = self._callbacks.get(ip, [])
            with contextlib.suppress(ValueError):
                callbacks.remove(callback)
            if not callbacks:
                self._callbacks.pop(ip, None)

        return unregister

    async def discover(
        self,
        duration: float = 5.0,
        *,
        broadcast: str = "255.255.255.255",
        target_port: int = protocol.PORT,
    ) -> list[tuple[str, str]]:
        """Broadcast ``%2SRCH`` and return ``(ip, mac)`` for every reply."""
        await self.start()
        assert self._transport is not None  # noqa: S101
        self._search_results = []
        try:
            self._transport.sendto(b"%2SRCH\r", (broadcast, target_port))
            await asyncio.sleep(duration)
            return sorted(set(self._search_results))
        finally:
            self._search_results = None

    def _datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        ip = addr[0]
        line = data.decode(errors="replace").rstrip("\r\n")
        if (mac := protocol.parse_ack(line)) is not None:
            if self._search_results is not None:
                self._search_results.append((ip, mac.lower()))
            return
        if (notification := protocol.parse_notification(line)) is None:
            _LOGGER.debug("Ignoring datagram from %s: %r", ip, line)
            return
        for callback in list(self._callbacks.get(ip, [])):
            try:
                callback(*notification)
            except Exception:
                _LOGGER.exception("Error in PJLink notification callback")


class _ListenerProtocol(asyncio.DatagramProtocol):
    def __init__(self, listener: PJLinkListener) -> None:
        self._listener = listener

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._listener._datagram_received(data, addr)  # noqa: SLF001
