"""Client for the SW42DA TCP control port. No Home Assistant imports.

Framing: the device echoes each command, sends CRLF-terminated lines, then
the prompt ``SW42DA>`` with no line ending. All output goes to every open
connection, including other clients' responses and unsolicited messages
(source and power changes, start-up output), which can arrive mid-response
and on the same line as a prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import logging
import re

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8000
PROMPT = "SW42DA>"
COMMAND_TIMEOUT = 10.0
# Source changes are retried for this long after powering on from standby.
POWER_ON_TIMEOUT = 15.0
POWER_ON_RETRY_INTERVAL = 0.5
CONNECT_TIMEOUT = 10.0

SOURCE_ARC = 5
SOURCES = (1, 2, 3, 4, SOURCE_ARC)

_SOURCE_CHANGED = re.compile(r"\[SUCCESS\]Set output from input (\d+)\.")
_POWER_CHANGED = re.compile(r"\[SUCCESS\]Set system power (on|off)")
_POWERED_OFF = re.compile(r"power off, please turn it on", re.IGNORECASE)
# Sent unprompted during start-up, preceded by a prompt of its own.
_STARTUP_MESSAGE = re.compile(r"^\s*Source Signal:")
_FIRMWARE = re.compile(r"FW Version:\s*(\S+)")
# STATUS column names and values contain single spaces; columns are separated
# by two or more.
_COLUMN_SPLIT = re.compile(r"\S+(?: \S+)*")


class SW42DAError(Exception):
    """Base class for client errors."""


class SW42DAConnectionError(SW42DAError):
    """Connect, send or response timeout failure."""


class SW42DAProtocolError(SW42DAError):
    """Unparseable response."""


class SW42DACommandError(SW42DAError):
    """The device replied ``[ERROR]...``."""


class Scaling(Enum):
    """Output 2 video mode; values are the ``OUT 02 SCALING yy`` codes."""

    AUTO = 1
    FORCE_1080P = 2
    BYPASS = 3

    @classmethod
    def from_status(cls, text: str) -> Scaling | None:
        # OutputScaler reads "Auto", "Bypass" or "Force_1080p".
        text = text.lower()
        if "auto" in text:
            return cls.AUTO
        if "bypass" in text:
            return cls.BYPASS
        if "1080" in text:
            return cls.FORCE_1080P
        return None


@dataclass(frozen=True)
class OutputStatus:
    enabled: bool
    connected: bool
    scaling: Scaling | None


@dataclass(frozen=True)
class Status:
    # None for a few seconds after power on, when STATUS replaces the network
    # table with "Network Loading...".
    mac: str | None
    hostname: str | None
    firmware: str | None
    power: bool
    temperature: float | None
    source: int | None
    # LCD ON (otherwise the display turns off after 30 s unused).
    display_always_on: bool | None = None
    # PWLED FOLLOW ON.
    power_led_follows_display: bool | None = None
    inputs: dict[int, bool] = field(default_factory=dict)
    outputs: dict[int, OutputStatus] = field(default_factory=dict)
    # From the Web-GUI, not STATUS.
    input_names: dict[int, str] = field(default_factory=dict)


def parse_tables(lines: list[str]) -> dict[str, list[dict[str, str]]]:
    """Return STATUS tables keyed by first column name, rows sliced at the
    header's column offsets."""
    tables: dict[str, list[dict[str, str]]] = {}
    block: list[str] = []
    for line in [*lines, ""]:
        if line.strip() and not line.startswith("="):
            block.append(line.rstrip())
            continue
        if len(block) >= 2:
            header, *rows = block
            columns = [(m.start(), m.group()) for m in _COLUMN_SPLIT.finditer(header)]
            ends = [start for start, _ in columns[1:]] + [None]
            tables[columns[0][1]] = [
                {
                    name: row[start:end].strip()
                    for (start, name), end in zip(columns, ends, strict=True)
                }
                for row in rows
            ]
        block = []
    return tables


def parse_status(lines: list[str]) -> Status:
    firmware = None
    for line in lines:
        if match := _FIRMWARE.search(line):
            firmware = match.group(1)
            break

    tables = parse_tables(lines)
    try:
        system = tables["Power"][0]
        output_rows = tables["Output"]
        input_rows = tables["Input"]
    except (KeyError, IndexError) as err:
        raise SW42DAProtocolError(f"STATUS response is missing {err}") from err

    outputs = {
        int(row["Output"]): OutputStatus(
            enabled=row.get("OutputEn", "").lower() == "yes",
            connected=row.get("HDMIcon", "").lower() == "on",
            scaling=Scaling.from_status(row.get("OutputScaler", "")),
        )
        for row in output_rows
    }
    inputs = {
        int(row["Input"]): row.get("HDMIcon", "").lower() == "on" for row in input_rows
    }

    source = None
    if from_in := output_rows[0].get("FromIn"):
        source = int(from_in)

    temperature = None
    if temp := system.get("Temp(C)"):
        try:
            temperature = float(temp.rstrip("Cc"))
        except ValueError:
            _LOGGER.debug("Unexpected temperature %r", temp)

    network = tables.get("Telnet", [{}])[0]

    def on_off(value: str | None) -> bool | None:
        return {"on": True, "off": False}.get((value or "").lower())

    return Status(
        mac=network.get("Mac") or None,
        hostname=network.get("Local") or None,
        firmware=firmware,
        power=system.get("Power", "").lower() == "on",
        temperature=temperature,
        source=source,
        display_always_on=on_off(system.get("LCD")),
        power_led_follows_display=on_off(system.get("PWLED_Follow")),
        inputs=inputs,
        outputs=outputs,
    )


class SW42DAClient:
    """A single reader task owns the socket.

    A response starts at the echo of its command and ends at the next prompt;
    anything before the echo belongs to another client. Source and power
    messages are dispatched wherever they appear.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        on_source_changed: Callable[[int], None] | None = None,
        on_power_changed: Callable[[bool], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_source_changed = on_source_changed
        self.on_power_changed = on_power_changed
        self.on_disconnect = on_disconnect
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._awaiting_echo: str | None = None
        self._response: list[str] | None = None
        self._is_complete: Callable[[list[str]], bool] | None = None
        self._response_done: asyncio.Future[list[str]] | None = None

    @property
    def connected(self) -> bool:
        return self._reader_task is not None and not self._reader_task.done()

    async def connect(self) -> None:
        await self.close()
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                self._reader, self._writer = await asyncio.open_connection(
                    self.host, self.port
                )
        except (OSError, TimeoutError) as err:
            raise SW42DAConnectionError(
                f"Cannot connect to {self.host}:{self.port}: {err}"
            ) from err
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"sw42da reader {self.host}"
        )

    async def close(self) -> None:
        task, self._reader_task = self._reader_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = self._writer = None
        self._fail_pending(SW42DAConnectionError("Connection closed"))

    async def command(
        self,
        command: str,
        *,
        is_complete: Callable[[list[str]], bool] | None = None,
    ) -> list[str]:
        """Return the response lines, excluding the echo.

        With ``is_complete``, prompts are ignored until it accepts the lines so
        far, so an unsolicited prompt cannot truncate a long response.
        """
        async with self._lock:
            if not self.connected or self._writer is None:
                raise SW42DAConnectionError("Not connected")
            loop = asyncio.get_running_loop()
            self._awaiting_echo = command
            self._response = []
            self._is_complete = is_complete
            self._response_done = loop.create_future()
            _LOGGER.debug("Sending %r", command)
            try:
                self._writer.write(f"{command}\r".encode("ascii"))
                await self._writer.drain()
                async with asyncio.timeout(COMMAND_TIMEOUT):
                    lines = await self._response_done
            except OSError as err:
                raise SW42DAConnectionError(f"Send failed: {err}") from err
            except TimeoutError as err:
                raise SW42DAConnectionError(
                    f"No response to {command!r} from {self.host}"
                ) from err
            finally:
                self._awaiting_echo = None
                self._response = None
                self._is_complete = None
                self._response_done = None

        for line in lines:
            if line.startswith("[ERROR]"):
                raise SW42DACommandError(f"{command!r} failed: {line}")
        return lines

    async def status(self) -> Status:
        # The body is framed by two lines of "=".
        lines = await self.command(
            "STATUS",
            is_complete=lambda lines: sum(ln.startswith("=") for ln in lines) >= 2,
        )
        return parse_status(lines)

    async def set_source(self, source: int, *, power_on: bool = False) -> None:
        """Select 1-4 (HDMI) or 5 (ARC).

        The device refuses this in standby. With ``power_on`` it is powered on
        and the command retried until accepted.
        """
        if source not in SOURCES:
            raise ValueError(f"Invalid source {source}")
        command = f"OUT FR {source:02d}"
        try:
            await self.command(command)
            return
        except SW42DACommandError as err:
            if not power_on or not _POWERED_OFF.search(str(err)):
                raise
        _LOGGER.debug("In standby; powering on before switching to %d", source)
        await self.set_power(True)
        try:
            async with asyncio.timeout(POWER_ON_TIMEOUT):
                while True:
                    try:
                        await self.command(command)
                        return
                    except SW42DACommandError as err:
                        if not _POWERED_OFF.search(str(err)):
                            raise
                    await asyncio.sleep(POWER_ON_RETRY_INTERVAL)
        except TimeoutError as err:
            raise SW42DACommandError(
                f"{command!r} still refused {POWER_ON_TIMEOUT:.0f} s after power on"
            ) from err

    async def set_output_enabled(self, output: int, enabled: bool) -> None:
        if output not in (1, 2):
            raise ValueError(f"Invalid output {output}")
        await self.command(f"OUT {output:02d} {'ON' if enabled else 'OFF'}")

    async def set_scaling(self, scaling: Scaling) -> None:
        await self.command(f"OUT 02 SCALING {scaling.value:02d}")

    async def set_power(self, power: bool) -> None:
        await self.command("PON" if power else "POFF")

    async def set_display_always_on(self, always_on: bool) -> None:
        await self.command("LCD ON" if always_on else "LCD OFF")

    async def set_power_led_follows_display(self, follows: bool) -> None:
        await self.command("PWLED FOLLOW ON" if follows else "PWLED FOLLOW OFF")

    async def _read_loop(self) -> None:
        assert self._reader is not None
        buffer = ""
        try:
            while data := await self._reader.read(4096):
                buffer += data.decode("ascii", errors="replace")
                buffer = self._process(buffer)
        except OSError as err:
            _LOGGER.debug("Connection to %s lost: %s", self.host, err)
        _LOGGER.debug("Connection to %s closed", self.host)
        self._fail_pending(SW42DAConnectionError("Connection lost"))
        if self.on_disconnect is not None:
            self.on_disconnect()

    def _process(self, buffer: str) -> str:
        # Returns the unconsumed remainder.
        while True:
            if buffer.startswith(PROMPT):
                buffer = buffer[len(PROMPT) :]
                self._prompt()
            elif (end := buffer.find("\n")) >= 0:
                self._line(buffer[:end].rstrip("\r"))
                buffer = buffer[end + 1 :]
            else:
                return buffer

    def _line(self, line: str) -> None:
        if match := _SOURCE_CHANGED.search(line):
            source = int(match.group(1))
            _LOGGER.debug("Source changed to %d", source)
            if self.on_source_changed is not None:
                self.on_source_changed(source)
        if match := _POWER_CHANGED.search(line):
            power = match.group(1) == "on"
            _LOGGER.debug("Power changed to %s", match.group(1))
            if self.on_power_changed is not None:
                self.on_power_changed(power)
        if _STARTUP_MESSAGE.match(line):
            _LOGGER.debug("Startup message: %r", line)
            return
        if self._response is not None:
            if self._awaiting_echo is not None:
                if line.strip() == self._awaiting_echo:
                    self._awaiting_echo = None
            else:
                self._response.append(line)
        elif line.strip():
            _LOGGER.debug("Unsolicited line: %r", line)

    def _prompt(self) -> None:
        if (
            self._response_done is not None
            and not self._response_done.done()
            and self._awaiting_echo is None
            and (self._is_complete is None or self._is_complete(self._response or []))
        ):
            self._response_done.set_result(self._response or [])

    def _fail_pending(self, err: Exception) -> None:
        if self._response_done is not None and not self._response_done.done():
            self._response_done.set_exception(err)
