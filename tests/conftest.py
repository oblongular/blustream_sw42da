from pathlib import Path
import re

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    # Required for Home Assistant to load integrations from custom_components.
    return


@pytest.fixture
def status_raw() -> bytes:
    # Exactly as sent by the device: echo, body, prompt. MAC anonymised.
    return (FIXTURES / "status_raw.txt").read_bytes()


@pytest.fixture
def status(status_raw: bytes):
    from custom_components.blustream_sw42da.protocol import parse_status

    text = status_raw.decode().removeprefix("STATUS\r\n").removesuffix("SW42DA>")
    return parse_status(text.split("\r\n"))


@pytest.fixture
def mock_client(status):
    # callbacks holds the last client's on_* keyword arguments, so tests can
    # simulate device messages.
    from unittest.mock import AsyncMock, MagicMock, patch

    client = MagicMock()
    client.connected = True
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.status = AsyncMock(return_value=status)
    for name in (
        "set_source",
        "set_output_enabled",
        "set_scaling",
        "set_power",
        "set_display_always_on",
        "set_power_led_follows_display",
    ):
        setattr(client, name, AsyncMock())
    client.callbacks = {}

    def factory(host, port, **kwargs):
        client.host, client.port = host, port
        client.callbacks = kwargs
        return client

    with (
        patch(
            "custom_components.blustream_sw42da.coordinator.SW42DAClient",
            side_effect=factory,
        ),
        patch(
            "custom_components.blustream_sw42da.config_flow.SW42DAClient",
            side_effect=factory,
        ),
    ):
        yield client


class FakeWebGui:
    """The Web-GUI's /cgi-bin/instr endpoint, on any host."""

    def __init__(self) -> None:
        self.reachable = True
        self.input_names = ["Apple TV", "Laptop", "INPUT3", "INPUT4"]
        self.settings = {
            "comhead": "get_settings",
            "device_name": "SW42DA",
            "hostname": "SW42DA",
            "mac_addr": "34:D0:B8:00:00:01",
            "tcpipport": 8000,
            "tcpip_enable": 1,
        }
        self.requests: list[tuple[str, str]] = []

    async def respond(self, method, url, data):
        self.requests.append((url.host, data["comhead"]))
        if not self.reachable:
            return AiohttpClientMockResponse(method, url, status=500)
        if data["comhead"] == "get_settings":
            return AiohttpClientMockResponse(method, url, json=self.settings)
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "comhead": "get_input",
                "name": self.input_names,
                "cable": [1, 1, 0, 0],
            },
        )


@pytest.fixture(autouse=True)
def web_gui(aioclient_mock) -> FakeWebGui:
    fake = FakeWebGui()
    aioclient_mock.post(re.compile(r"/cgi-bin/instr$"), side_effect=fake.respond)
    return fake
