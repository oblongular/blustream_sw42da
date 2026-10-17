from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import SW42DACoordinator


class SW42DAEntity(CoordinatorEntity[SW42DACoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SW42DACoordinator, key: str) -> None:
        super().__init__(coordinator)
        mac = coordinator.config_entry.unique_id
        assert mac is not None
        self._attr_unique_id = f"{mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=f"{MANUFACTURER} {MODEL}",
            sw_version=coordinator.data.firmware,
        )
