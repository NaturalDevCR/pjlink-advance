"""A fake PJLink projector for tests and development without hardware.

Run it standalone with ``python -m pjlink_advance.emulator --port 4352``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Self

from .models import (
    ERROR_CATEGORIES,
    ErrorLevel,
    ErrorStatus,
    InputSource,
    InputType,
    Lamp,
    MuteState,
    PowerState,
    Resolution,
    ResolutionValue,
)
from .protocol import auth_digest

_LOGGER = logging.getLogger(__name__)

_REQUEST_RE = re.compile(r"^%(?P<cls>[12])(?P<cmd>[A-Z0-9]{4}) (?P<param>.*)$")
_POWER_CODES = {
    PowerState.OFF: "0",
    PowerState.ON: "1",
    PowerState.COOLING: "2",
    PowerState.WARMING: "3",
}
_ERROR_CODES = {ErrorLevel.OK: "0", ErrorLevel.WARNING: "1", ErrorLevel.ERROR: "2"}


def _default_inputs() -> list[InputSource]:
    return [
        InputSource(InputType.RGB, "1"),
        InputSource(InputType.DIGITAL, "1"),
        InputSource(InputType.DIGITAL, "2"),
        InputSource(InputType.NETWORK, "1"),
    ]


@dataclass
class EmulatorState:
    """Everything the emulated projector knows. Tests change it freely."""

    power: PowerState = PowerState.OFF
    input: InputSource = field(
        default_factory=lambda: InputSource(InputType.DIGITAL, "1")
    )
    inputs: list[InputSource] = field(default_factory=_default_inputs)
    input_names: dict[str, str] = field(
        default_factory=lambda: {
            "11": "Computer",
            "31": "HDMI1",
            "32": "HDMI2",
            "51": "LAN",
        }
    )
    mute: MuteState = field(default_factory=lambda: MuteState(video=False, audio=False))
    freeze: bool = False
    errors: ErrorStatus = field(default_factory=ErrorStatus.ok)
    lamps: list[Lamp] = field(default_factory=lambda: [Lamp(1200, False)])
    filter_hours: int = 300
    name: str = "EB-L735U"
    manufacturer: str = "EPSON"
    model: str = "EB-L735U"
    other_info: str = ""
    serial: str = "X8JK0000001"
    software_version: str = "1.00"
    input_resolution: ResolutionValue = field(
        default_factory=lambda: Resolution(1920, 1200)
    )
    recommended_resolution: ResolutionValue = field(
        default_factory=lambda: Resolution(1920, 1200)
    )
    lamp_model: str = ""
    filter_model: str = "ELPAF60"
    mac: str = "00:26:ab:00:00:01"
    speaker_volume: int = 0
    mic_volume: int = 0


class PJLinkEmulator:
    """Emulate one projector over TCP (and UDP for Class 2 search/notify)."""

    def __init__(
        self,
        state: EmulatorState | None = None,
        *,
        pj_class: int = 2,
        password: str | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        udp_port: int | None = 0,
        idle_timeout: float = 30.0,
        transition_delay: float = 0.0,
        encoding: str = "utf-8",
    ) -> None:
        self.state = state or EmulatorState()
        self.pj_class = pj_class
        self.password = password
        self.host = host
        self.idle_timeout = idle_timeout
        self.transition_delay = transition_delay
        self.encoding = encoding
        self.unsupported: set[str] = set()
        self.unavailable: set[str] = set()
        self.fail_next: str | None = None
        self.drop_after_greeting = False
        self.silent = False
        self.connections = 0
        self.received: list[str] = []
        self._requested_port = port
        self._requested_udp_port = udp_port
        self._server: asyncio.Server | None = None
        self._udp: asyncio.DatagramTransport | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ life cycle

    async def start(self) -> None:
        """Start listening."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self._requested_port
        )
        if self._requested_udp_port is not None:
            loop = asyncio.get_running_loop()
            self._udp, _ = await loop.create_datagram_endpoint(
                lambda: _EmulatorDatagram(self),
                local_addr=(self.host, self._requested_udp_port),
            )

    async def stop(self) -> None:
        """Stop listening and drop every connection."""
        for task in self._tasks:
            task.cancel()
        for writer in list(self._writers):
            writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._udp:
            self._udp.close()
            self._udp = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    @property
    def port(self) -> int:
        """TCP port in use."""
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    @property
    def udp_port(self) -> int:
        """UDP port in use."""
        assert self._udp is not None
        return self._udp.get_extra_info("sockname")[1]

    def disconnect_all(self) -> None:
        """Close all open TCP sessions (simulates the projector dropping them)."""
        for writer in list(self._writers):
            writer.close()

    # ------------------------------------------------------------------ UDP

    async def notify(self, command: str, value: str, target: tuple[str, int]) -> None:
        """Send a Class 2 status notification such as ``%2POWR=1``."""
        assert self._udp is not None
        self._udp.sendto(f"%2{command}={value}\r".encode(), target)

    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        line = data.decode(errors="replace").rstrip("\r\n")
        if line == "%2SRCH" and self.pj_class >= 2 and self._udp:
            self._udp.sendto(f"%2ACKN={self.state.mac}\r".encode(), addr)

    # ------------------------------------------------------------------ TCP

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        self._writers.add(writer)
        salt = secrets.token_hex(4) if self.password else None
        try:
            writer.write(f"PJLINK 1 {salt}\r".encode() if salt else b"PJLINK 0\r")
            await writer.drain()
            if self.drop_after_greeting:
                return
            authenticated = salt is None
            while True:
                try:
                    raw = await asyncio.wait_for(
                        reader.readuntil(b"\r"), self.idle_timeout
                    )
                except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                    return
                line = raw.decode(self.encoding, errors="replace").rstrip("\r")
                if not authenticated:
                    assert salt is not None and self.password is not None
                    expected = auth_digest(salt, self.password)
                    if line[:32] != expected:
                        writer.write(b"PJLINK ERRA\r")
                        await writer.drain()
                        return
                    authenticated = True
                    line = line[32:]
                self.received.append(line)
                if self.silent:
                    continue
                response = self._respond(line)
                writer.write(f"{response}\r".encode(self.encoding))
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _respond(self, line: str) -> str:
        match = _REQUEST_RE.match(line)
        if not match:
            return "PJLINK ERR1" if not line.startswith("%") else f"{line[:6]}=ERR1"
        pj_class = int(match["cls"])
        command = match["cmd"]
        param = match["param"]
        header = f"%{pj_class}{command}="
        if self.fail_next:
            result, self.fail_next = self.fail_next, None
            return header + result
        if pj_class > self.pj_class or command in self.unsupported:
            return header + "ERR1"
        if command in self.unavailable:
            return header + "ERR3"
        handler = self._handlers().get((pj_class, command)) or self._handlers().get(
            (0, command)
        )
        if handler is None:
            return header + "ERR1"
        return header + handler(param, pj_class)

    def _handlers(self) -> dict[tuple[int, str], Callable[[str, int], str]]:
        # Class 0 means "valid in both classes".
        return {
            (1, "POWR"): self._powr,
            (0, "INPT"): self._inpt,
            (0, "INST"): self._inst,
            (1, "AVMT"): self._avmt,
            (1, "ERST"): self._query(self._erst),
            (1, "LAMP"): self._query(self._lamp),
            (1, "NAME"): self._query(lambda: self.state.name),
            (1, "INF1"): self._query(lambda: self.state.manufacturer),
            (1, "INF2"): self._query(lambda: self.state.model),
            (1, "INFO"): self._query(lambda: self.state.other_info),
            (1, "CLSS"): self._query(lambda: str(self.pj_class)),
            (2, "SNUM"): self._query(lambda: self.state.serial),
            (2, "SVER"): self._query(lambda: self.state.software_version),
            (2, "INNM"): self._innm,
            (2, "IRES"): self._ires,
            (2, "RRES"): self._query(
                lambda: _resolution(self.state.recommended_resolution)
            ),
            (2, "FILT"): self._query(lambda: str(self.state.filter_hours)),
            (2, "RLMP"): self._query(lambda: self.state.lamp_model),
            (2, "RFIL"): self._query(lambda: self.state.filter_model),
            (2, "SVOL"): self._volume("speaker_volume"),
            (2, "MVOL"): self._volume("mic_volume"),
            (2, "FREZ"): self._frez,
        }

    @staticmethod
    def _query(getter: Callable[[], str]) -> Callable[[str, int], str]:
        def handle(param: str, _cls: int) -> str:
            return getter() if param == "?" else "ERR2"

        return handle

    def _powr(self, param: str, _cls: int) -> str:
        if param == "?":
            return _POWER_CODES[self.state.power]
        if param not in ("0", "1"):
            return "ERR2"
        turning_on = param == "1"
        if turning_on and self.state.power in (PowerState.ON, PowerState.WARMING):
            return "OK"
        if not turning_on and self.state.power in (PowerState.OFF, PowerState.COOLING):
            return "OK"
        if self.transition_delay <= 0:
            self._set_power(PowerState.ON if turning_on else PowerState.OFF)
            return "OK"
        self._set_power(PowerState.WARMING if turning_on else PowerState.COOLING)
        task = asyncio.get_running_loop().create_task(
            self._finish_transition(PowerState.ON if turning_on else PowerState.OFF)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return "OK"

    async def _finish_transition(self, final: PowerState) -> None:
        await asyncio.sleep(self.transition_delay)
        self._set_power(final)

    def _set_power(self, power: PowerState) -> None:
        self.state.power = power
        if power is PowerState.OFF:
            self.state.mute = MuteState(video=False, audio=False)
            self.state.freeze = False

    def _require_on(self) -> bool:
        return self.state.power is PowerState.ON

    def _inpt(self, param: str, pj_class: int) -> str:
        if not self._require_on():
            return "ERR3"
        if param == "?":
            return self.state.input.code
        try:
            source = InputSource.from_code(param)
        except ValueError:
            return "ERR2"
        if source not in self.state.inputs or (
            pj_class == 1 and source.requires_class2
        ):
            return "ERR2"
        self.state.input = source
        return "OK"

    def _inst(self, param: str, pj_class: int) -> str:
        if param != "?":
            return "ERR2"
        inputs = [
            src for src in self.state.inputs if pj_class == 2 or not src.requires_class2
        ]
        return " ".join(src.code for src in inputs)

    def _avmt(self, param: str, _cls: int) -> str:
        if param == "?":
            mute = self.state.mute
            if mute.video and mute.audio:
                return "31"
            if mute.video:
                return "11"
            if mute.audio:
                return "21"
            return "30"
        if not self._require_on():
            return "ERR3"
        if len(param) != 2 or param[0] not in "123" or param[1] not in "01":
            return "ERR2"
        on = param[1] == "1"
        video, audio = self.state.mute.video, self.state.mute.audio
        if param[0] in "13":
            video = on
        if param[0] in "23":
            audio = on
        self.state.mute = MuteState(video=video, audio=audio)
        return "OK"

    def _erst(self) -> str:
        levels = self.state.errors.as_dict()
        return "".join(_ERROR_CODES[levels[name]] for name in ERROR_CATEGORIES)

    def _lamp(self) -> str:
        if not self.state.lamps:
            return "ERR1"
        # The light source is lit exactly when the projector is on.
        lit = int(self.state.power is PowerState.ON)
        return " ".join(f"{lamp.hours} {lit}" for lamp in self.state.lamps)

    def _innm(self, param: str, _cls: int) -> str:
        if not param.startswith("?"):
            return "ERR2"
        code = param[1:]
        if code not in {src.code for src in self.state.inputs}:
            return "ERR2"
        return self.state.input_names.get(code, "")

    def _ires(self, param: str, _cls: int) -> str:
        if param != "?":
            return "ERR2"
        if not self._require_on():
            return "ERR3"
        return _resolution(self.state.input_resolution)

    def _volume(self, attribute: str) -> Callable[[str, int], str]:
        def handle(param: str, _cls: int) -> str:
            if param not in ("0", "1"):
                return "ERR2"
            if not self._require_on():
                return "ERR3"
            setattr(
                self.state,
                attribute,
                getattr(self.state, attribute) + (1 if param == "1" else -1),
            )
            return "OK"

        return handle

    def _frez(self, param: str, _cls: int) -> str:
        if param == "?":
            return "1" if self.state.freeze else "0"
        if param not in ("0", "1"):
            return "ERR2"
        if not self._require_on():
            return "ERR3"
        self.state.freeze = param == "1"
        return "OK"


def _resolution(value: ResolutionValue) -> str:
    if isinstance(value, Resolution):
        return str(value)
    if value == "no_signal":
        return "-"
    return "*"


class _EmulatorDatagram(asyncio.DatagramProtocol):
    def __init__(self, emulator: PJLinkEmulator) -> None:
        self._emulator = emulator

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._emulator._handle_datagram(data, addr)  # noqa: SLF001


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run a fake PJLink projector")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4352)
    parser.add_argument("--password")
    parser.add_argument("--pj-class", type=int, default=2, choices=(1, 2))
    parser.add_argument("--transition-delay", type=float, default=10.0)
    args = parser.parse_args()
    emulator = PJLinkEmulator(
        pj_class=args.pj_class,
        password=args.password,
        host=args.host,
        port=args.port,
        udp_port=args.port,
        transition_delay=args.transition_delay,
    )
    async with emulator:
        print(f"PJLink emulator listening on {args.host}:{emulator.port}")
        await asyncio.Event().wait()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
