from dataclasses import replace
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_OPTION,
    CONF_HOST,
    CONF_PORT,
    SERVICE_SELECT_OPTION,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.blustream_sw42da.const import (
    DOMAIN,
    SCAN_INTERVAL,
)
from custom_components.blustream_sw42da.protocol import (
    Scaling,
    SW42DACommandError,
    SW42DAConnectionError,
)

SOURCE = "select.blustream_sw42da_source"
SCALING = "select.blustream_sw42da_hdmi_output_2_scaling"


@pytest.fixture
async def entry(hass: HomeAssistant, mock_client: MagicMock) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="34:d0:b8:00:00:01",
        data={CONF_HOST: "192.168.22.88", CONF_PORT: 8000},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    expected = {
        SOURCE: "hdmi_4",
        SCALING: "auto",
        "switch.blustream_sw42da_power": STATE_ON,
        "switch.blustream_sw42da_hdmi_output_1": STATE_ON,
        "switch.blustream_sw42da_hdmi_output_2": STATE_ON,
        "binary_sensor.blustream_sw42da_hdmi_input_1_connected": STATE_ON,
        "binary_sensor.blustream_sw42da_hdmi_input_3_connected": STATE_OFF,
        "binary_sensor.blustream_sw42da_hdmi_output_1_connected": STATE_ON,
        "binary_sensor.blustream_sw42da_hdmi_output_2_connected": STATE_OFF,
        "sensor.blustream_sw42da_temperature": "52.0",
    }
    for entity_id, state in expected.items():
        assert hass.states.get(entity_id).state == state, entity_id
    assert hass.states.get(SOURCE).attributes["options"] == [
        "hdmi_1",
        "hdmi_2",
        "hdmi_3",
        "hdmi_4",
        "arc",
    ]


async def test_select_source(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock, status
) -> None:
    mock_client.status.return_value = replace(status, source=2)
    await hass.services.async_call(
        "select",
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SOURCE, ATTR_OPTION: "hdmi_2"},
        blocking=True,
    )
    mock_client.set_source.assert_awaited_once_with(2, power_on=True)
    await hass.async_block_till_done()
    assert hass.states.get(SOURCE).state == "hdmi_2"


async def test_controls(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await hass.services.async_call(
        "select",
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SCALING, ATTR_OPTION: "force_1080p"},
        blocking=True,
    )
    mock_client.set_scaling.assert_awaited_once_with(Scaling.FORCE_1080P)

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "switch.blustream_sw42da_hdmi_output_2"},
        blocking=True,
    )
    mock_client.set_output_enabled.assert_awaited_once_with(2, False)

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "switch.blustream_sw42da_power"},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.blustream_sw42da_power"},
        blocking=True,
    )
    assert [c.args for c in mock_client.set_power.await_args_list] == [
        (False,),
        (True,),
    ]


async def test_command_error(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.set_source.side_effect = SW42DACommandError("[ERROR]Nope")
    with pytest.raises(HomeAssistantError, match="Nope"):
        await hass.services.async_call(
            "select",
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: SOURCE, ATTR_OPTION: "arc"},
            blocking=True,
        )


async def test_front_panel_change(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    reads = mock_client.status.await_count
    mock_client.callbacks["on_source_changed"](1)
    await hass.async_block_till_done()
    assert hass.states.get(SOURCE).state == "hdmi_1"
    assert mock_client.status.await_count == reads  # no extra poll needed


async def test_poll_and_reconnect(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    mock_client.connected = False
    mock_client.connect.side_effect = SW42DAConnectionError
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SOURCE).state == STATE_UNAVAILABLE

    mock_client.connect.side_effect = None
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SOURCE).state == "hdmi_4"


async def test_setup_retry(hass: HomeAssistant, mock_client: MagicMock) -> None:
    mock_client.connected = False
    mock_client.connect.side_effect = SW42DAConnectionError
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="34:d0:b8:00:00:01",
        data={CONF_HOST: "192.168.22.88", CONF_PORT: 8000},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.close.assert_awaited()


async def test_unload(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    mock_client.close.assert_awaited()


async def test_source_switches(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    states = {
        n: hass.states.get(f"switch.blustream_sw42da_source_hdmi_{n}").state
        for n in (1, 2, 3, 4)
    }
    assert states == {1: STATE_OFF, 2: STATE_OFF, 3: STATE_OFF, 4: STATE_ON}
    assert hass.states.get("switch.blustream_sw42da_source_arc") is None

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.blustream_sw42da_source_hdmi_2"},
        blocking=True,
    )
    mock_client.set_source.assert_awaited_once_with(2, power_on=True)

    mock_client.callbacks["on_source_changed"](2)
    await hass.async_block_till_done()
    assert hass.states.get("switch.blustream_sw42da_source_hdmi_2").state == STATE_ON
    assert hass.states.get("switch.blustream_sw42da_source_hdmi_4").state == STATE_OFF

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "switch.blustream_sw42da_source_hdmi_2"},
        blocking=True,
    )
    assert hass.states.get("switch.blustream_sw42da_source_hdmi_2").state == STATE_ON
    mock_client.set_source.assert_awaited_once()


async def test_standby(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    hdmi_4 = "switch.blustream_sw42da_source_hdmi_4"
    assert hass.states.get(hdmi_4).state == STATE_ON

    mock_client.callbacks["on_power_changed"](False)
    await hass.async_block_till_done()
    assert hass.states.get("switch.blustream_sw42da_power").state == STATE_OFF
    assert hass.states.get(hdmi_4).state == STATE_OFF
    # The select still shows which source the device will return to.
    assert hass.states.get(SOURCE).state == "hdmi_4"

    # Pressing a source powers on first; the client handles that.
    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: hdmi_4}, blocking=True
    )
    mock_client.set_source.assert_awaited_once_with(4, power_on=True)

    mock_client.callbacks["on_power_changed"](True)
    await hass.async_block_till_done()
    assert hass.states.get(hdmi_4).state == STATE_ON


async def test_source_switches_named_after_labels(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    states = [
        hass.states.get(f"switch.blustream_sw42da_source_hdmi_{n}")
        for n in (1, 2, 3, 4)
    ]
    assert [s.attributes["label"] for s in states] == [
        "Apple TV",
        "Laptop",
        "INPUT3",
        "INPUT4",
    ]
    assert states[0].name == "Blustream SW42DA Apple TV"
    assert states[0].attributes["input"] == 1
    assert hass.states.get(SOURCE).attributes["labels"] == {
        "hdmi_1": "Apple TV",
        "hdmi_2": "Laptop",
        "hdmi_3": "INPUT3",
        "hdmi_4": "INPUT4",
    }


async def test_source_switch_names_without_web_gui(
    hass: HomeAssistant, mock_client: MagicMock, web_gui
) -> None:
    web_gui.reachable = False
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="34:d0:b8:00:00:01",
        data={CONF_HOST: "192.168.22.88", CONF_PORT: 8000},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get("switch.blustream_sw42da_source_hdmi_2")
    assert state.attributes["label"] == "HDMI 2"
    assert state.name == "Blustream SW42DA HDMI 2"


async def test_setup_changes_no_device_settings(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    for name in (
        "set_source",
        "set_output_enabled",
        "set_scaling",
        "set_power",
        "set_display_always_on",
        "set_power_led_follows_display",
    ):
        getattr(mock_client, name).assert_not_awaited()


async def test_display_switches(
    hass: HomeAssistant, entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    # The fixture has the factory settings.
    display = "switch.blustream_sw42da_display_always_on"
    led = "switch.blustream_sw42da_power_led_follows_display"
    assert hass.states.get(display).state == STATE_OFF
    assert hass.states.get(led).state == STATE_ON

    mock_client.set_display_always_on.reset_mock()
    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: display}, blocking=True
    )
    mock_client.set_display_always_on.assert_awaited_once_with(True)
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: led}, blocking=True
    )
    mock_client.set_power_led_follows_display.assert_awaited_with(False)
