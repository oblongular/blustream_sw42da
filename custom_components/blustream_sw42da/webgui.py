"""The Web-GUI's JSON API: POST to /cgi-bin/instr on port 80, no login for reads.

Input labels and the device identity are only available here; the control
protocol has no naming commands. No Home Assistant imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from .protocol import DEFAULT_PORT, SW42DAConnectionError, SW42DAProtocolError

TIMEOUT = aiohttp.ClientTimeout(total=5)


@dataclass(frozen=True)
class DeviceSettings:
    # device_name: the model, e.g. "SW42DA". It doesn't follow hostname changes.
    model: str
    mac: str
    hostname: str | None
    control_port: int
    control_enabled: bool


async def _request(session: aiohttp.ClientSession, host: str, comhead: str) -> Any:
    # /cgi-bin/query (GET) is not an alternative: it returns the last response.
    try:
        async with session.post(
            f"http://{host}/cgi-bin/instr",
            json={"comhead": comhead, "language": 1},
            timeout=TIMEOUT,
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise SW42DAConnectionError(f"Web-GUI request failed: {err}") from err
    except ValueError as err:
        raise SW42DAProtocolError(f"Web-GUI sent invalid JSON: {err}") from err


async def get_input_names(session: aiohttp.ClientSession, host: str) -> dict[int, str]:
    # Reply: {"comhead":"get_input","name":["INPUT1",...],"cable":[...],...}
    data = await _request(session, host, "get_input")
    names = data.get("name") if isinstance(data, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise SW42DAProtocolError(f"Unexpected get_input response: {data!r}")
    return {number: name.strip() for number, name in enumerate(names, start=1)}


async def get_settings(session: aiohttp.ClientSession, host: str) -> DeviceSettings:
    data = await _request(session, host, "get_settings")
    if not isinstance(data, dict) or data.get("comhead") != "get_settings":
        raise SW42DAProtocolError(f"Unexpected get_settings response: {data!r}")
    try:
        return DeviceSettings(
            model=str(data["device_name"]).strip(),
            mac=str(data["mac_addr"]),
            hostname=data.get("hostname") or None,
            control_port=int(data.get("tcpipport", DEFAULT_PORT)),
            control_enabled=bool(data.get("tcpip_enable", 1)),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise SW42DAProtocolError(
            f"Unexpected get_settings response: {data!r}"
        ) from err
