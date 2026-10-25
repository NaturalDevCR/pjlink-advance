# pjlink-advance

Asyncio PJLink Class 1 and Class 2 library with network search, status
notifications and a projector emulator. No runtime dependencies.

## Client

```python
import asyncio
from pjlink_advance import PJLinkClient, MuteTarget


async def main() -> None:
    async with PJLinkClient("192.168.1.50", password="secret") as projector:
        info = await projector.fetch_static()
        print(info.manufacturer, info.model, info.serial)
        await projector.set_power(True)
        await projector.set_av_mute(MuteTarget.VIDEO, True)
        state = await projector.fetch_state(full=True, pj_class=info.pj_class)
        print(state.power, state.lamps, state.errors)


asyncio.run(main())
```

One TCP session is kept open and shared by all commands (serialized with a
lock). It reconnects on its own when the projector drops idle sessions.

Errors: `PJLinkConnectionError`, `PJLinkAuthError`, `PJLinkProtocolError`, and
`PJLinkCommandError` subclasses for `ERR1`–`ERR4`
(`PJLinkUndefinedCommand`, `PJLinkOutOfParameter`, `PJLinkUnavailableTime`,
`PJLinkProjectorFailure`).

## Search and notifications (Class 2)

```python
from pjlink_advance import PJLinkListener

listener = PJLinkListener()  # UDP port 4352
await listener.start()
print(await listener.discover(duration=3))  # [(ip, mac), ...]
listener.register("192.168.1.50", lambda command, value: print(command, value))
```

## Emulator

```bash
python -m pjlink_advance.emulator --port 4352 --password secret --pj-class 2
```

In tests:

```python
from pjlink_advance.emulator import PJLinkEmulator

async with PJLinkEmulator(password="secret") as emulator:
    emulator.unavailable.add("INPT")  # answer ERR3
    emulator.fail_next = "ERR4"
    ...
```

## License

MIT
