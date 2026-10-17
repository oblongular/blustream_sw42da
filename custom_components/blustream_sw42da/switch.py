from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .const import MANUFACTURER, MODEL
from .coordinator import SW42DAConfigEntry, SW42DACoordinator
from .entity import SW42DAEntity
from .protocol import SOURCE_ARC

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SW42DAConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PowerSwitch(coordinator),
            *(OutputSwitch(coordinator, output) for output in (1, 2)),
            *(SourceSwitch(coordinator, source) for source in (1, 2, 3, 4)),
            SourceSwitch(coordinator, SOURCE_ARC),
            DisplayAlwaysOnSwitch(coordinator),
            PowerLedFollowsDisplaySwitch(coordinator),
        ]
    )


class PowerSwitch(SW42DAEntity, SwitchEntity):
    _attr_translation_key = "power"

    def __init__(self, coordinator: SW42DACoordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.power

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(self.coordinator.client.set_power(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(self.coordinator.client.set_power(False))


class OutputSwitch(SW42DAEntity, SwitchEntity):
    _attr_translation_key = "output"

    def __init__(self, coordinator: SW42DACoordinator, output: int) -> None:
        super().__init__(coordinator, f"output_{output}")
        self._output = output
        self._attr_translation_placeholders = {"output": str(output)}

    @property
    def is_on(self) -> bool | None:
        output = self.coordinator.data.outputs.get(self._output)
        return None if output is None else output.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_output_enabled(self._output, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_output_enabled(self._output, False)
        )


class SourceSwitch(SW42DAEntity, SwitchEntity):
    """One per source, acting as radio buttons for controllers that only show
    on/off state (e.g. Stream Deck via Companion).

    Entity IDs are fixed so that renaming an input in the Web-GUI doesn't break
    references to it. Home Assistant prefixes friendly names with the device
    name, so the bare label is also exposed as the ``label`` attribute for use
    as button text.
    """

    def __init__(self, coordinator: SW42DACoordinator, source: int) -> None:
        if source == SOURCE_ARC:
            super().__init__(coordinator, "source_arc")
            self._attr_translation_key = "source_arc"
            self.entity_id = f"switch.{slugify(f'{MANUFACTURER} {MODEL}')}_source_arc"
            # Most setups only switch between the HDMI inputs.
            self._attr_entity_registry_enabled_default = False
        else:
            super().__init__(coordinator, f"source_hdmi_{source}")
            self._attr_icon = "mdi:video-input-hdmi"
            self.entity_id = (
                f"switch.{slugify(f'{MANUFACTURER} {MODEL}')}_source_hdmi_{source}"
            )
        self._source = source

    @property
    def name(self) -> str | None:
        if self._source == SOURCE_ARC:
            return super().name
        return self._label

    @property
    def _label(self) -> str:
        if self._source == SOURCE_ARC:
            return "ARC"
        return self.coordinator.data.input_names.get(self._source) or (
            f"HDMI {self._source}"
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        return {"label": self._label, "input": self._source}

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        return data.power and data.source == self._source

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_source(self._source, power_on=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        # The device always has a source selected; just revert the UI toggle.
        self.async_write_ha_state()


class DisplayAlwaysOnSwitch(SW42DAEntity, SwitchEntity):
    _attr_translation_key = "display_always_on"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SW42DACoordinator) -> None:
        super().__init__(coordinator, "display_always_on")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.display_always_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_display_always_on(True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_display_always_on(False)
        )


class PowerLedFollowsDisplaySwitch(SW42DAEntity, SwitchEntity):
    _attr_translation_key = "power_led_follows_display"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SW42DACoordinator) -> None:
        super().__init__(coordinator, "power_led_follows_display")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.power_led_follows_display

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_power_led_follows_display(True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_power_led_follows_display(False)
        )
