from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import replace
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL
from .protocol import Status, SW42DAClient, SW42DAError
from .webgui import get_input_names

_LOGGER = logging.getLogger(__name__)

type SW42DAConfigEntry = ConfigEntry[SW42DACoordinator]


class SW42DACoordinator(DataUpdateCoordinator[Status]):
    config_entry: SW42DAConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SW42DAConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = SW42DAClient(
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            on_source_changed=self._source_changed,
            on_power_changed=self._power_changed,
            on_disconnect=self._disconnected,
        )
        self._input_names: dict[int, str] = {}

    async def _async_update_data(self) -> Status:
        try:
            if not self.client.connected:
                await self.client.connect()
            status = await self.client.status()
        except SW42DAError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self._async_update_input_names()
        return replace(status, input_names=self._input_names)

    async def _async_update_input_names(self) -> None:
        # Labels are cosmetic: on failure keep the last ones and don't fail the update.
        try:
            self._input_names = await get_input_names(
                async_get_clientsession(self.hass), self.config_entry.data[CONF_HOST]
            )
        except SW42DAError as err:
            _LOGGER.debug("Could not read input labels: %s", err)

    async def async_command(self, command: Awaitable[None]) -> None:
        try:
            if not self.client.connected:
                await self.client.connect()
            await command
        except SW42DAError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.client.close()

    @callback
    def _source_changed(self, source: int) -> None:
        if self.data is not None and self.data.source != source:
            self.async_set_updated_data(replace(self.data, source=source))

    @callback
    def _power_changed(self, power: bool) -> None:
        if self.data is not None and self.data.power != power:
            self.async_set_updated_data(replace(self.data, power=power))

    @callback
    def _disconnected(self) -> None:
        # Reconnect now rather than at the next poll.
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), "sw42da reconnect"
        )
