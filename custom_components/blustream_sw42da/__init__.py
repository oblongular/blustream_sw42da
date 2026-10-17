from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_DEFAULTS_APPLIED
from .coordinator import SW42DAConfigEntry, SW42DACoordinator
from .protocol import SW42DAError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: SW42DAConfigEntry) -> bool:
    coordinator = SW42DACoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.client.close()
        raise
    entry.runtime_data = coordinator
    if not entry.data.get(CONF_DEFAULTS_APPLIED):
        await _async_apply_defaults(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_apply_defaults(hass: HomeAssistant, entry: SW42DAConfigEntry) -> None:
    # The factory settings blank the display and power LED after 30 s, so a
    # powered unit looks like it is in standby. Applied once; later changes are
    # the user's.
    coordinator = entry.runtime_data
    try:
        await coordinator.client.set_display_always_on(True)
        await coordinator.client.set_power_led_follows_display(False)
        await coordinator.async_refresh()
    except SW42DAError as err:
        _LOGGER.warning("Could not apply default display settings: %s", err)
        return  # Try again at the next setup.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_DEFAULTS_APPLIED: True}
    )


async def async_unload_entry(hass: HomeAssistant, entry: SW42DAConfigEntry) -> bool:
    # The coordinator closes the connection on unload.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
