from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol

from .const import DEFAULT_HOST, DOMAIN, MANUFACTURER, MODEL
from .protocol import (
    DEFAULT_PORT,
    Status,
    SW42DAClient,
    SW42DAConnectionError,
    SW42DAError,
)
from .webgui import get_settings

_LOGGER = logging.getLogger(__name__)

TITLE = f"{MANUFACTURER} {MODEL}"

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
    }
)


async def read_status(host: str, port: int) -> Status:
    client = SW42DAClient(host, port)
    try:
        await client.connect()
        status = await client.status()
    finally:
        await client.close()
    if status.mac is None:
        # Just after power on, STATUS omits the network section for a while.
        raise SW42DAConnectionError("Device is starting up; no MAC address yet")
    return status


class SW42DAConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    _host: str | None = None
    _port: int = DEFAULT_PORT

    async def _validate(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> Status | None:
        try:
            return await read_status(user_input[CONF_HOST], user_input[CONF_PORT])
        except SW42DAConnectionError:
            errors["base"] = "cannot_connect"
        except SW42DAError:
            _LOGGER.exception("Unexpected response from %s", user_input[CONF_HOST])
            errors["base"] = "invalid_response"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if status := await self._validate(user_input, errors):
                await self.async_set_unique_id(format_mac(status.mac))
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(title=TITLE, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        # The manifest matches Blustream's whole MAC block, which covers other
        # models and the Dante modules; _async_step_discovered filters them.
        # Known units are recognised by MAC before any request, so an address
        # change costs nothing.
        await self.async_set_unique_id(format_mac(discovery_info.macaddress))
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.ip})
        return await self._async_step_discovered(discovery_info.ip)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        return await self._async_step_discovered(str(discovery_info.ip_address))

    async def _async_step_discovered(self, host: str) -> ConfigFlowResult:
        # Identify by the Web-GUI's get_settings, which also gives the configured
        # control port; neither DHCP nor mDNS says what the device is.
        try:
            settings = await get_settings(async_get_clientsession(self.hass), host)
        except SW42DAConnectionError:
            return self.async_abort(reason="cannot_connect")
        except SW42DAError:
            return self.async_abort(reason="not_supported")
        if settings.model != MODEL:
            return self.async_abort(reason="not_supported")

        await self.async_set_unique_id(format_mac(settings.mac))
        # Keep the configured port: the user may have set it deliberately.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        if not settings.control_enabled:
            return self.async_abort(reason="control_disabled")

        self._host = host
        self._port = settings.control_port
        self.context["title_placeholders"] = {"name": settings.hostname or host}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._host is not None
        if user_input is not None:
            return self.async_create_entry(
                title=TITLE, data={CONF_HOST: self._host, CONF_PORT: self._port}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"host": self._host},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if status := await self._validate(user_input, errors):
                await self.async_set_unique_id(format_mac(status.mac))
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )
