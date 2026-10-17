from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SW42DAConfigEntry, SW42DACoordinator
from .entity import SW42DAEntity
from .protocol import SOURCE_ARC, Scaling

SOURCE_OPTIONS = {1: "hdmi_1", 2: "hdmi_2", 3: "hdmi_3", 4: "hdmi_4", SOURCE_ARC: "arc"}
SCALING_OPTIONS = {
    Scaling.AUTO: "auto",
    Scaling.FORCE_1080P: "force_1080p",
    Scaling.BYPASS: "bypass",
}

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SW42DAConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([SourceSelect(coordinator), ScalingSelect(coordinator)])


class SourceSelect(SW42DAEntity, SelectEntity):
    _attr_translation_key = "source"
    _attr_options = list(SOURCE_OPTIONS.values())

    def __init__(self, coordinator: SW42DACoordinator) -> None:
        super().__init__(coordinator, "source")

    @property
    def current_option(self) -> str | None:
        return SOURCE_OPTIONS.get(self.coordinator.data.source)

    @property
    def extra_state_attributes(self) -> dict[str, dict[str, str]]:
        names = self.coordinator.data.input_names
        return {
            "labels": {
                option: names[source]
                for source, option in SOURCE_OPTIONS.items()
                if source in names
            }
        }

    async def async_select_option(self, option: str) -> None:
        source = next(k for k, v in SOURCE_OPTIONS.items() if v == option)
        await self.coordinator.async_command(
            self.coordinator.client.set_source(source, power_on=True)
        )


class ScalingSelect(SW42DAEntity, SelectEntity):
    _attr_translation_key = "output_2_scaling"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SCALING_OPTIONS.values())

    def __init__(self, coordinator: SW42DACoordinator) -> None:
        super().__init__(coordinator, "output_2_scaling")

    @property
    def current_option(self) -> str | None:
        output = self.coordinator.data.outputs.get(2)
        if output is None or output.scaling is None:
            return None
        return SCALING_OPTIONS[output.scaling]

    async def async_select_option(self, option: str) -> None:
        scaling = next(k for k, v in SCALING_OPTIONS.items() if v == option)
        await self.coordinator.async_command(
            self.coordinator.client.set_scaling(scaling)
        )
