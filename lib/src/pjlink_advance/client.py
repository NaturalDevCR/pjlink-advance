"""Asyncio PJLink client with one persistent, serialized connection."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Self

from . import protocol
from .errors import (
    PJLinkAuthError,
    PJLinkCommandError,
    PJLinkConnectionError,
    PJLinkError,
    PJLinkProtocolError,
    PJLinkUndefinedCommand,
)
from .models import (
    DeviceInfo,
    ErrorStatus,
    InputSource,
    Lamp,
    MuteState,
    MuteTarget,
    PowerState,
    ProjectorState,
    ResolutionValue,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0
# Projectors close idle sessions after about 30 s; reconnect before that.
DEFAULT_MAX_IDLE = 25.0


class _StaleConnectionError(Exception):
    """The session died between commands; safe to retry once."""


class PJLinkClient:
    """Talk to one projector.

    All commands share one TCP session guarded by a lock, because many
    projectors accept only a single PJLink connection at a time.

    With ``persistent=False`` the session is closed after every command, or at
    the end of a :meth:`session` block, so other controllers can connect in
    between.
    """

    def __init__(
        self,
        host: str,
        port: int = protocol.PORT,
        password: str | None = None,
        *,
        encoding: str = "utf-8",
        timeout: float = DEFAULT_TIMEOUT,
        max_idle: float = DEFAULT_MAX_IDLE,
        persistent: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.encoding = encoding
        self.timeout = timeout
        self.max_idle = max_idle
        self.persistent = persistent
        self._sessions = 0
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._digest: str | None = None
        self._last_used = 0.0

    # ------------------------------------------------------------------ session

    @property
    def connected(self) -> bool:
        """Return True while a session is open."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Open the session now instead of on the first command."""
        async with self._lock:
            if not self.connected:
                await self._connect()

    async def close(self) -> None:
        """Close the session."""
        async with self._lock:
            await self._disconnect()

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[Self]:
        """Group several commands on one connection.

        In non-persistent mode the connection closes when the outermost block
        exits; in persistent mode this only groups the commands.
        """
        self._sessions += 1
        try:
            yield self
        finally:
            self._sessions -= 1
            if self._should_release():
                await self.close()

    def _should_release(self) -> bool:
        return not self.persistent and self._sessions == 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except (OSError, TimeoutError) as err:
            raise PJLinkConnectionError(
                f"Cannot connect to {self.host}:{self.port}: {err}"
            ) from err
        try:
            salt = protocol.parse_greeting(await self._readline())
        except _StaleConnectionError as err:
            await self._disconnect()
            raise PJLinkConnectionError("Projector closed the connection") from err
        except PJLinkError:
            await self._disconnect()
            raise
        if salt is None:
            self._digest = None
        elif self.password is None:
            await self._disconnect()
            raise PJLinkAuthError("Projector requires a password")
        else:
            self._digest = protocol.auth_digest(salt, self.password)
        self._last_used = time.monotonic()

    async def _disconnect(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        self._digest = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _readline(self) -> str:
        if self._reader is None:
            raise _StaleConnectionError
        try:
            raw = await asyncio.wait_for(
                self._reader.readuntil(protocol.TERMINATOR), self.timeout
            )
        except TimeoutError as err:
            raise PJLinkConnectionError("Projector did not answer in time") from err
        except asyncio.IncompleteReadError as err:
            raise _StaleConnectionError from err
        except asyncio.LimitOverrunError as err:
            raise PJLinkProtocolError("Response too long") from err
        except ConnectionError as err:
            raise _StaleConnectionError from err
        return self._decode(raw.rstrip(b"\r\n"))

    def _decode(self, raw: bytes) -> str:
        try:
            return raw.decode(self.encoding)
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    # ------------------------------------------------------------------ commands

    async def command(self, pj_class: int, command: str, param: str = "?") -> str:
        """Send one command and return the response value."""
        payload = protocol.encode_command(pj_class, command, param)
        async with self._lock:
            try:
                return await self._command(payload, pj_class, command)
            finally:
                if self._should_release():
                    await self._disconnect()

    async def _command(self, payload: bytes, pj_class: int, command: str) -> str:
        for attempt in range(2):
            if self.connected and time.monotonic() - self._last_used > self.max_idle:
                await self._disconnect()
            if not self.connected:
                await self._connect()
            try:
                return await self._exchange(payload, pj_class, command)
            except _StaleConnectionError as err:
                await self._disconnect()
                if attempt:
                    raise PJLinkConnectionError(
                        "Projector closed the connection"
                    ) from err
            except (PJLinkConnectionError, PJLinkAuthError, PJLinkProtocolError):
                await self._disconnect()
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _exchange(self, payload: bytes, pj_class: int, command: str) -> str:
        if self._writer is None:
            raise _StaleConnectionError
        if self._digest:
            payload = self._digest.encode() + payload
        try:
            self._writer.write(payload)
            await self._writer.drain()
        except ConnectionError as err:
            raise _StaleConnectionError from err
        line = await self._readline()
        self._digest = None
        self._last_used = time.monotonic()
        _LOGGER.debug("%s <- %r -> %r", self.host, payload, line)
        return protocol.parse_response(line, pj_class, command)

    # ------------------------------------------------------------------ Class 1

    async def get_power(self) -> PowerState:
        """Return the power state."""
        return protocol.decode_power(await self.command(1, "POWR"))

    async def set_power(self, on: bool) -> None:
        """Turn the projector on or off."""
        await self.command(1, "POWR", "1" if on else "0")

    async def get_input(self) -> InputSource:
        """Return the selected input."""
        return protocol.decode_input(await self.command(1, "INPT"))

    async def set_input(self, source: InputSource) -> None:
        """Select an input."""
        await self.command(2 if source.requires_class2 else 1, "INPT", source.code)

    async def get_inputs(self, pj_class: int = 1) -> list[InputSource]:
        """Return the available inputs."""
        return protocol.decode_inputs(await self.command(pj_class, "INST"))

    async def get_av_mute(self) -> MuteState:
        """Return the audio/video mute state."""
        return protocol.decode_av_mute(await self.command(1, "AVMT"))

    async def set_av_mute(self, target: MuteTarget, on: bool) -> None:
        """Mute or unmute video, audio or both."""
        await self.command(1, "AVMT", f"{target.value}{int(on)}")

    async def get_errors(self) -> ErrorStatus:
        """Return the error status."""
        return protocol.decode_errors(await self.command(1, "ERST"))

    async def get_lamps(self) -> list[Lamp]:
        """Return hours and state of every lamp / light source."""
        return protocol.decode_lamps(await self.command(1, "LAMP"))

    async def get_name(self) -> str:
        """Return the projector name."""
        return await self.command(1, "NAME")

    async def get_manufacturer(self) -> str:
        """Return the manufacturer (INF1)."""
        return await self.command(1, "INF1")

    async def get_model(self) -> str:
        """Return the model (INF2)."""
        return await self.command(1, "INF2")

    async def get_other_info(self) -> str:
        """Return other information (INFO)."""
        return await self.command(1, "INFO")

    async def get_class(self) -> int:
        """Return the PJLink class supported by the projector."""
        value = await self.command(1, "CLSS")
        return 2 if value.strip() == "2" else 1

    # ------------------------------------------------------------------ Class 2

    async def get_serial(self) -> str:
        """Return the serial number."""
        return await self.command(2, "SNUM")

    async def get_software_version(self) -> str:
        """Return the software version."""
        return await self.command(2, "SVER")

    async def get_input_name(self, source: InputSource) -> str:
        """Return the name of an input terminal."""
        return await self.command(2, "INNM", f"?{source.code}")

    async def get_input_resolution(self) -> ResolutionValue:
        """Return the resolution of the current input signal."""
        return protocol.decode_resolution(await self.command(2, "IRES"))

    async def get_recommended_resolution(self) -> ResolutionValue:
        """Return the native resolution recommended by the projector."""
        return protocol.decode_resolution(await self.command(2, "RRES"))

    async def get_filter_hours(self) -> int:
        """Return the filter usage time in hours."""
        return protocol.decode_int(await self.command(2, "FILT"))

    async def get_lamp_model(self) -> str:
        """Return the replacement lamp model."""
        return await self.command(2, "RLMP")

    async def get_filter_model(self) -> str:
        """Return the replacement filter model."""
        return await self.command(2, "RFIL")

    async def speaker_volume(self, *, up: bool) -> None:
        """Step the speaker volume."""
        await self.command(2, "SVOL", "1" if up else "0")

    async def mic_volume(self, *, up: bool) -> None:
        """Step the microphone volume."""
        await self.command(2, "MVOL", "1" if up else "0")

    async def get_freeze(self) -> bool:
        """Return True if the image is frozen."""
        return protocol.decode_bool(await self.command(2, "FREZ"))

    async def set_freeze(self, on: bool) -> None:
        """Freeze or unfreeze the image."""
        await self.command(2, "FREZ", "1" if on else "0")

    async def send_raw(self, command: str, param: str = "?", pj_class: int = 1) -> str:
        """Send any command and return the raw response value."""
        return await self.command(pj_class, command, param)

    # ------------------------------------------------------------------ bulk

    async def fetch_static(self) -> DeviceInfo:
        """Read information that rarely changes, on one connection."""
        async with self.session():
            return await self._fetch_static()

    async def _fetch_static(self) -> DeviceInfo:
        pj_class = await self.get_class()
        info = DeviceInfo(
            name=await _optional(self.get_name) or "",
            manufacturer=await _optional(self.get_manufacturer) or "",
            model=await _optional(self.get_model) or "",
            other_info=await _optional(self.get_other_info) or "",
            pj_class=pj_class,
        )
        info.inputs = await _optional(lambda: self.get_inputs(pj_class)) or []
        if pj_class < 2:
            return info
        info.serial = await _optional(self.get_serial) or None
        info.software_version = await _optional(self.get_software_version) or None
        for source in info.inputs:
            name = await _optional(lambda source=source: self.get_input_name(source))
            if name:
                info.input_names[source.code] = name
        info.lamp_model = await _optional(self.get_lamp_model) or None
        info.filter_model = await _optional(self.get_filter_model) or None
        info.recommended_resolution = await _optional(self.get_recommended_resolution)
        return info

    async def fetch_state(self, *, full: bool, pj_class: int) -> ProjectorState:
        """Read the current state on one connection.

        ``full`` adds errors, lamps and filter.
        """
        async with self.session():
            return await self._fetch_state(full=full, pj_class=pj_class)

    async def _fetch_state(self, *, full: bool, pj_class: int) -> ProjectorState:
        state = ProjectorState(power=await self.get_power())
        if state.power.is_on:
            state.input = await _optional(self.get_input)
            state.mute = await _optional(self.get_av_mute)
            if pj_class >= 2:
                state.freeze = await _optional(self.get_freeze)
                state.input_resolution = await _optional(self.get_input_resolution)
        if full:
            state.errors = await _optional(self.get_errors)
            try:
                state.lamps = await self.get_lamps()
            except PJLinkUndefinedCommand:
                state.lamps = []
            except (PJLinkCommandError, PJLinkProtocolError):
                state.lamps = None
            if pj_class >= 2:
                state.filter_hours = await _optional(self.get_filter_hours)
        return state


async def _optional[T](fetch: Callable[[], Awaitable[T]]) -> T | None:
    """Run a query whose failure must not break a bulk read."""
    try:
        return await fetch()
    except (PJLinkCommandError, PJLinkProtocolError) as err:
        _LOGGER.debug("Optional query failed: %s", err)
        return None
