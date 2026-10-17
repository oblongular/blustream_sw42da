from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SW42DAConfigEntry, SW42DACoordinator
from .entity import SW42DAEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SW42DAConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(InputConnected(coordinator, n) for n in sorted(coordinator.data.inputs)),
            *(
                OutputConnected(coordinator, n)
                for n in sorted(coordinator.data.outputs)
            ),
        ]
    )


class _Connected(SW42DAEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SW42DACoordinator, kind: str, port: int) -> None:
        super().__init__(coordinator, f"{kind}_{port}_connected")
        self._port = port
        self._attr_translation_key = f"{kind}_connected"
        self._attr_translation_placeholders = {kind: str(port)}


class InputConnected(_Connected):
    def __init__(self, coordinator: SW42DACoordinator, port: int) -> None:
        super().__init__(coordinator, "input", port)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.inputs.get(self._port)


class OutputConnected(_Connected):
    def __init__(self, coordinator: SW42DACoordinator, port: int) -> None:
        super().__init__(coordinator, "output", port)

    @property
    def is_on(self) -> bool | None:
        output = self.coordinator.data.outputs.get(self._port)
        return None if output is None else output.connected
