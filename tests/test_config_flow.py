from ipaddress import ip_address
from unittest.mock import MagicMock

from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.blustream_sw42da.const import DOMAIN
from custom_components.blustream_sw42da.protocol import (
    SW42DAConnectionError,
    SW42DAProtocolError,
)

MAC = "34:d0:b8:00:00:01"
USER_INPUT = {CONF_HOST: "192.168.22.88", CONF_PORT: 8000}

DISCOVERY = ZeroconfServiceInfo(
    ip_address=ip_address("192.168.22.99"),
    ip_addresses=[ip_address("192.168.22.99")],
    port=80,
    hostname="SW42DA.local.",
    type="_http._tcp.local.",
    name="SW42DA._http._tcp.local.",
    properties={"path": "/"},
)


DHCP_DISCOVERY = DhcpServiceInfo(
    ip="192.168.22.99", hostname="lounge", macaddress="34d0b8000001"
)


async def test_user(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Blustream SW42DA"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == MAC
    mock_client.close.assert_awaited()


async def test_user_errors(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    mock_client.connect.side_effect = SW42DAConnectionError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.connect.side_effect = None
    mock_client.status.side_effect = SW42DAProtocolError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "invalid_response"}

    mock_client.status.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_already_configured(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=USER_INPUT).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf(hass: HomeAssistant, web_gui, mock_client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert web_gui.requests == [("192.168.22.99", "get_settings")]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "192.168.22.99", CONF_PORT: 8000}
    assert result["result"].unique_id == MAC


async def test_zeroconf_updates_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={CONF_HOST: "192.168.22.88", CONF_PORT: 8001}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_HOST: "192.168.22.99", CONF_PORT: 8001}


async def test_dhcp_any_name(
    hass: HomeAssistant, web_gui, mock_client: MagicMock
) -> None:
    # Renamed unit with a non-default control port.
    web_gui.settings |= {"hostname": "LOUNGE", "tcpipport": 8001}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_DISCOVERY
    )
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"] == {"host": "192.168.22.99"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"] == {CONF_HOST: "192.168.22.99", CONF_PORT: 8001}
    assert result["result"].unique_id == MAC


async def test_dhcp_known_device_moved(hass: HomeAssistant, web_gui) -> None:
    # Recognised by MAC: the host is updated without contacting the device.
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={CONF_HOST: "192.168.22.88", CONF_PORT: 8000}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_DISCOVERY
    )
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.22.99"
    assert web_gui.requests == []


@pytest.mark.parametrize("source", [SOURCE_DHCP, SOURCE_ZEROCONF])
async def test_discovery_other_model(hass: HomeAssistant, web_gui, source) -> None:
    web_gui.settings["device_name"] = "MX44AB"
    data = DHCP_DISCOVERY if source == SOURCE_DHCP else DISCOVERY
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source}, data=data
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_discovery_unreachable(hass: HomeAssistant, web_gui) -> None:
    web_gui.reachable = False
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_DISCOVERY
    )
    assert result["reason"] == "cannot_connect"


async def test_discovery_control_disabled(hass: HomeAssistant, web_gui) -> None:
    web_gui.settings["tcpip_enable"] = 0
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["reason"] == "control_disabled"


async def test_reconfigure(hass: HomeAssistant, mock_client: MagicMock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=USER_INPUT)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM

    new = {CONF_HOST: "sw42da.local", CONF_PORT: 8000}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert {k: entry.data[k] for k in new} == new


async def test_reconfigure_wrong_device(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="34:d0:b8:00:00:02", data=USER_INPUT
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


async def test_user_device_starting_up(
    hass: HomeAssistant, mock_client: MagicMock, status
) -> None:
    from dataclasses import replace

    mock_client.status.return_value = replace(status, mac=None)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "cannot_connect"}
