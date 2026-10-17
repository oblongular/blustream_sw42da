import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from custom_components.blustream_sw42da import protocol
from custom_components.blustream_sw42da.protocol import (
    OutputStatus,
    Scaling,
    SW42DAClient,
    SW42DACommandError,
    SW42DAConnectionError,
    SW42DAProtocolError,
    parse_status,
    parse_tables,
)


@pytest.fixture(autouse=True)
def allow_local_sockets(socket_enabled):
    return


def status_lines(raw: bytes) -> list[str]:
    text = raw.decode().removeprefix("STATUS\r\n").removesuffix("SW42DA>")
    return text.split("\r\n")


def test_parse_status(status_raw: bytes) -> None:
    status = parse_status(status_lines(status_raw))

    assert status.mac == "34:D0:B8:00:00:01"
    assert status.hostname == "SW42DA"
    assert status.firmware == "V1.9.0"
    assert status.power is True
    assert status.temperature == 52.0
    assert status.source == 4
    assert status.inputs == {1: True, 2: True, 3: False, 4: False}
    assert status.outputs == {
        1: OutputStatus(enabled=True, connected=True, scaling=Scaling.BYPASS),
        2: OutputStatus(enabled=True, connected=False, scaling=Scaling.AUTO),
    }


def test_parse_tables_values_with_spaces(status_raw: bytes) -> None:
    tables = parse_tables(status_lines(status_raw))

    assert tables["Output"][1]["AudioSignal"] == "Downmix 2CH"
    assert tables["MultiChannelOutFrom"][0]["2ChannelOutFrom"] == "HDMI/ARC Input"
    assert tables["Power"][0]["Network"] == "Mode 1"
    assert tables["Master Output"][0]["2.0CH Line Group"] == "On"
    assert [row["Line Output"] for row in tables["Line Output"]] == [
        "2.0CH Line L",
        "2.0CH Line R",
        "Downmix Line L",
        "Downmix Line R",
    ]


def test_parse_status_missing_table() -> None:
    with pytest.raises(SW42DAProtocolError):
        parse_status(["Unknown command", ""])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Auto", Scaling.AUTO),
        ("Bypass", Scaling.BYPASS),
        ("1080p", Scaling.FORCE_1080P),
        ("Force 1080P", Scaling.FORCE_1080P),
        ("???", None),
    ],
)
def test_scaling_from_status(text: str, expected: Scaling | None) -> None:
    assert Scaling.from_status(text) is expected


POWERED_OFF = "[ERROR]System is power off, please turn it on first."
POWER_ON_RESPONSE = (
    b"PON\r\n[SUCCESS]Set system power on, please wait a moment...\r\n\r\n"
    b"====================\r\nHDMI SW42DA Terminal Control System\r\n"
    b'FW Version: V1.9.0\r\nType "HELP" For More Information\r\n'
    b"====================\r\n\r\nSW42DA>"
)
# Sent on its own once the device has started up.
STARTUP_MESSAGE = b"SW42DA> Source Signal: None\r\n"


class FakeDevice:
    def __init__(self, status_raw: bytes) -> None:
        self.status_raw = status_raw
        self.received: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.chunk_size: int | None = None
        self.silent = False
        self.power = True
        # How many source changes to refuse after power on, while starting up.
        self.starting_up_refusals = 0
        self.server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def stop(self) -> None:
        for writer in self.writers:
            writer.close()
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def send(self, data: bytes) -> None:
        for writer in self.writers:
            if self.chunk_size:
                for i in range(0, len(data), self.chunk_size):
                    writer.write(data[i : i + self.chunk_size])
                    await writer.drain()
                    await asyncio.sleep(0)
            else:
                writer.write(data)
                await writer.drain()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.writers.append(writer)
        while True:
            try:
                command = await reader.readuntil(b"\r")
            except asyncio.IncompleteReadError, ConnectionError:
                return
            command = command.decode().strip()
            self.received.append(command)
            if self.silent:
                continue
            if command == "STATUS":
                await self.send(self.status_raw)
            elif command == "FOO":
                await self.send(b"FOO\r\n[ERROR]Command not found.\r\n\r\nSW42DA>")
            elif command == "PON":
                self.power = True
                await self.send(POWER_ON_RESPONSE)
            elif command == "POFF":
                self.power = False
                await self.send(
                    b"POFF\r\n[SUCCESS]Set system power off.\r\n\r\nSW42DA>"
                )
            elif command.startswith("OUT FR ") and (
                not self.power or self.starting_up_refusals
            ):
                if self.power:
                    self.starting_up_refusals -= 1
                await self.send(f"{command}\r\n{POWERED_OFF}\r\n\r\nSW42DA>".encode())
            elif command.startswith("OUT FR "):
                source = command.removeprefix("OUT FR ")
                await self.send(
                    f"{command}\r\n[SUCCESS]Set output from input {source}.\r\n"
                    "SW42DA>".encode()
                )
            else:
                await self.send(f"{command}\r\nSW42DA>".encode())


@pytest.fixture
async def device(status_raw: bytes) -> AsyncIterator[FakeDevice]:
    fake = FakeDevice(status_raw)
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def connect(
    device: FakeDevice,
) -> AsyncIterator[Callable[..., asyncio.Future[SW42DAClient]]]:
    clients: list[SW42DAClient] = []

    async def factory(**kwargs) -> SW42DAClient:
        client = SW42DAClient("127.0.0.1", device.port, **kwargs)
        await client.connect()
        clients.append(client)
        # Let the server register the connection.
        for _ in range(100):
            if device.writers:
                break
            await asyncio.sleep(0.01)
        return client

    yield factory
    for client in clients:
        await client.close()


async def test_status(connect) -> None:
    client = await connect()
    status = await client.status()
    assert status.source == 4
    assert status.mac == "34:D0:B8:00:00:01"


async def test_status_in_small_chunks(device: FakeDevice, connect) -> None:
    # Includes splits inside the prompt.
    device.chunk_size = 3
    client = await connect()
    status = await client.status()
    assert status.source == 4


async def test_commands(device: FakeDevice, connect) -> None:
    client = await connect()
    await client.set_source(2)
    await client.set_source(protocol.SOURCE_ARC)
    await client.set_output_enabled(1, False)
    await client.set_output_enabled(2, True)
    await client.set_scaling(Scaling.FORCE_1080P)
    await client.set_power(False)
    await client.set_power(True)
    assert device.received == [
        "OUT FR 02",
        "OUT FR 05",
        "OUT 01 OFF",
        "OUT 02 ON",
        "OUT 02 SCALING 02",
        "POFF",
        "PON",
    ]


async def test_command_strips_echo(connect) -> None:
    client = await connect()
    assert await client.command("OUT FR 03") == ["[SUCCESS]Set output from input 03."]


async def test_invalid_arguments(connect) -> None:
    client = await connect()
    with pytest.raises(ValueError):
        await client.set_source(6)
    with pytest.raises(ValueError):
        await client.set_output_enabled(3, True)


async def test_front_panel_message(device: FakeDevice, connect) -> None:
    changes: list[int] = []
    await connect(on_source_changed=changes.append)

    await device.send(b"[SUCCESS]Set output from input 03.\r\n")
    await asyncio.sleep(0.05)
    assert changes == [3]


async def test_front_panel_message_after_prompt(device: FakeDevice, connect) -> None:
    changes: list[int] = []
    client = await connect(on_source_changed=changes.append)
    await client.status()

    await device.send(
        b"SW42DA>[SUCCESS]Set output from input 01.\r\n"
        b"[SUCCESS]Set output from input 02.\r\n"
    )
    await asyncio.sleep(0.05)
    assert changes == [1, 2]


async def test_front_panel_message_during_command(device: FakeDevice, connect) -> None:
    changes: list[int] = []
    client = await connect(on_source_changed=changes.append)
    head, sep, tail = device.status_raw.partition(b"Input     Edid")
    device.status_raw = (
        head + b"[SUCCESS]Set output from input 04.\r\n\r\n" + sep + tail
    )

    status = await client.status()
    assert changes == [4]
    assert status.inputs[1] is True


async def test_timeout(
    device: FakeDevice, connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(protocol, "COMMAND_TIMEOUT", 0.1)
    device.silent = True
    client = await connect()
    with pytest.raises(SW42DAConnectionError):
        await client.status()


async def test_disconnect(device: FakeDevice, connect) -> None:
    disconnected = asyncio.Event()
    device.silent = True
    client = await connect(on_disconnect=disconnected.set)

    pending = asyncio.create_task(client.status())
    await asyncio.sleep(0.05)
    for writer in device.writers:
        writer.close()

    with pytest.raises(SW42DAConnectionError):
        await pending
    await asyncio.wait_for(disconnected.wait(), 1)
    assert not client.connected
    with pytest.raises(SW42DAConnectionError):
        await client.status()


async def test_connect_refused() -> None:
    client = SW42DAClient("127.0.0.1", 1)
    with pytest.raises(SW42DAConnectionError):
        await client.connect()


async def test_command_error(connect) -> None:
    client = await connect()
    with pytest.raises(SW42DACommandError, match="Command not found"):
        await client.command("FOO")


async def test_other_clients_output_ignored(device: FakeDevice, connect) -> None:
    # Another client's response, prompt included, arriving before our echo.
    client = await connect()
    device.status_raw = (
        b"UPTIME\r\nThe uptime of the system is 0005:23:47:23\r\n\r\nSW42DA>"
        + device.status_raw
    )
    status = await client.status()
    assert status.source == 4


async def test_shared_connection_messages(device: FakeDevice, connect) -> None:
    changes: list[int] = []
    await connect(on_source_changed=changes.append)
    other = await connect()

    await other.set_source(1)
    await asyncio.sleep(0.05)
    assert changes == [1]


WEB_GUI_CLICKS = (
    b"SW42DA>out fr 4\r\n[SUCCESS]Set output from input 04.\r\n"
    b"out fr 3\r\n[SUCCESS]Set output from input 03.\r\n"
)


async def test_web_gui_changes(device: FakeDevice, connect) -> None:
    # Web-GUI commands are echoed lowercase and unpadded, with no prompt after.
    changes: list[int] = []
    await connect(on_source_changed=changes.append)

    await device.send(WEB_GUI_CLICKS)
    await asyncio.sleep(0.05)
    assert changes == [4, 3]


async def test_web_gui_changes_during_command(device: FakeDevice, connect) -> None:
    changes: list[int] = []
    client = await connect(on_source_changed=changes.append)
    device.status_raw = WEB_GUI_CLICKS.removeprefix(b"SW42DA>") + device.status_raw

    status = await client.status()
    assert status.mac == "34:D0:B8:00:00:01"
    assert changes == [4, 3]


async def test_set_source_in_standby(device: FakeDevice, connect) -> None:
    device.power = False
    client = await connect()
    with pytest.raises(SW42DACommandError, match="power off"):
        await client.set_source(2)
    assert device.received == ["OUT FR 02"]


async def test_set_source_powers_on(
    device: FakeDevice, connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(protocol, "POWER_ON_RETRY_INTERVAL", 0.01)
    device.power = False
    device.starting_up_refusals = 2
    power: list[bool] = []
    sources: list[int] = []
    client = await connect(
        on_power_changed=power.append, on_source_changed=sources.append
    )

    await client.set_source(2, power_on=True)
    assert device.received == [
        "OUT FR 02",
        "PON",
        "OUT FR 02",
        "OUT FR 02",
        "OUT FR 02",
    ]
    assert power == [True]
    assert sources == [2]


async def test_set_source_power_on_timeout(
    device: FakeDevice, connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(protocol, "POWER_ON_RETRY_INTERVAL", 0.01)
    monkeypatch.setattr(protocol, "POWER_ON_TIMEOUT", 0.1)
    device.power = False
    device.starting_up_refusals = 1000
    client = await connect()
    with pytest.raises(SW42DACommandError, match="after power on"):
        await client.set_source(2, power_on=True)


async def test_power_button_messages(device: FakeDevice, connect) -> None:
    power: list[bool] = []
    await connect(on_power_changed=power.append)

    await device.send(
        b"SW42DA>[SUCCESS]Set system power off.\r\n"
        b"SW42DA>[SUCCESS]Set system power on, please wait a moment...\r\n"
    )
    await asyncio.sleep(0.05)
    assert power == [False, True]


async def test_startup_message_during_status(device: FakeDevice, connect) -> None:
    client = await connect()
    head, sep, tail = device.status_raw.partition(b"02        Force")
    device.status_raw = head + STARTUP_MESSAGE + sep + tail

    status = await client.status()
    assert status.inputs == {1: True, 2: True, 3: False, 4: False}
    assert status.mac == "34:D0:B8:00:00:01"


def test_parse_status_while_network_loading(status_raw: bytes) -> None:
    lines = status_lines(status_raw)
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Telnet"))
    lines[start : start + 2] = ["Network Loading..."]

    status = parse_status(lines)
    assert status.mac is None
    assert status.hostname is None
    assert status.source == 4
    assert status.power is True


def test_parse_display_settings(status_raw: bytes) -> None:
    status = parse_status(status_lines(status_raw))
    # The fixture was captured with the factory settings.
    assert status.display_always_on is False
    assert status.power_led_follows_display is True


async def test_display_commands(device: FakeDevice, connect) -> None:
    client = await connect()
    await client.set_display_always_on(True)
    await client.set_display_always_on(False)
    await client.set_power_led_follows_display(False)
    await client.set_power_led_follows_display(True)
    assert device.received == [
        "LCD ON",
        "LCD OFF",
        "PWLED FOLLOW OFF",
        "PWLED FOLLOW ON",
    ]
