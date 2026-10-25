"""Exceptions raised by pjlink_advance."""


class PJLinkError(Exception):
    """Base class for all PJLink errors."""


class PJLinkConnectionError(PJLinkError):
    """The projector could not be reached or the connection broke."""


class PJLinkAuthError(PJLinkError):
    """The projector rejected the password (``PJLINK ERRA``)."""


class PJLinkProtocolError(PJLinkError):
    """The projector sent a response that does not follow the protocol."""


class PJLinkCommandError(PJLinkError):
    """The projector answered a command with ``ERRn``."""

    code: str = ""

    def __init__(self, command: str, message: str | None = None) -> None:
        self.command = command
        super().__init__(message or f"{command}: {self.code}")


class PJLinkUndefinedCommand(PJLinkCommandError):
    """``ERR1``: the command is not supported by the projector."""

    code = "ERR1"


class PJLinkOutOfParameter(PJLinkCommandError):
    """``ERR2``: the parameter is not valid for the command."""

    code = "ERR2"


class PJLinkUnavailableTime(PJLinkCommandError):
    """``ERR3``: the projector cannot answer right now (e.g. warming up)."""

    code = "ERR3"


class PJLinkProjectorFailure(PJLinkCommandError):
    """``ERR4``: the projector reports an internal failure."""

    code = "ERR4"


COMMAND_ERRORS: dict[str, type[PJLinkCommandError]] = {
    cls.code: cls
    for cls in (
        PJLinkUndefinedCommand,
        PJLinkOutOfParameter,
        PJLinkUnavailableTime,
        PJLinkProjectorFailure,
    )
}
