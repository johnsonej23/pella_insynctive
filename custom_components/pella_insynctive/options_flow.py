from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import OptionsFlow
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    CONF_HOST,
    DEFAULT_BATTERY_POLL_MINUTES,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_RECONNECT_MAX_SECONDS,
    DEFAULT_RECONNECT_MIN_SECONDS,
    DEFAULT_SCAN_ALL_128,
    OPT_BATTERY_POLL_MINUTES,
    OPT_POLL_INTERVAL_SECONDS,
    OPT_RECONNECT_MAX_SECONDS,
    OPT_RECONNECT_MIN_SECONDS,
    OPT_SCAN_ALL_128,
    OPT_DEVICE_NAME_PREFIX,
    OPT_DEVICE_AREA_PREFIX,
    DOMAIN,
)
from .coordinator import PellaCoordinator

NETWORK_MODE_DHCP = "dhcp"
NETWORK_MODE_STATIC = "static"
CONF_NETWORK_MODE = "network_mode"
CONF_CONFIRM_DHCP = "confirm_dhcp"
CONF_STATIC_IP = "static_ip"
CONF_NETMASK = "netmask"
CONF_GATEWAY = "gateway"
CONF_DNS = "dns"


class PellaOptionsFlowHandler(OptionsFlow):
    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "bridge_network"],
        )

    async def async_step_general(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        o = self._entry.options

        device_options: dict = {}

        # If the integration is loaded, offer per-device naming/area overrides.
        coord = self._get_coordinator()
        if coord and getattr(coord, "data", None):
            for idx, dev in coord.data.items():
                name_key = f"{OPT_DEVICE_NAME_PREFIX}{idx:03d}"
                area_key = f"{OPT_DEVICE_AREA_PREFIX}{idx:03d}"

                device_options[
                    vol.Optional(name_key, default=o.get(name_key, dev.name))
                ] = selector.TextSelector(selector.TextSelectorConfig())

                # AreaSelector expects an area_id. Use "" (no selection) if not set.
                device_options[
                    vol.Optional(area_key, default=o.get(area_key, ""))
                ] = selector.AreaSelector()

        schema = vol.Schema(
            {
                vol.Optional(
                    OPT_RECONNECT_MIN_SECONDS,
                    default=o.get(OPT_RECONNECT_MIN_SECONDS, DEFAULT_RECONNECT_MIN_SECONDS),
                ): vol.Coerce(int),
                vol.Optional(
                    OPT_RECONNECT_MAX_SECONDS,
                    default=o.get(OPT_RECONNECT_MAX_SECONDS, DEFAULT_RECONNECT_MAX_SECONDS),
                ): vol.Coerce(int),
                vol.Optional(
                    OPT_POLL_INTERVAL_SECONDS,
                    default=o.get(OPT_POLL_INTERVAL_SECONDS, DEFAULT_POLL_INTERVAL_SECONDS),
                ): vol.Coerce(int),
                vol.Optional(
                    OPT_BATTERY_POLL_MINUTES,
                    default=o.get(OPT_BATTERY_POLL_MINUTES, DEFAULT_BATTERY_POLL_MINUTES),
                ): vol.Coerce(int),
                vol.Optional(
                    OPT_SCAN_ALL_128,
                    default=o.get(OPT_SCAN_ALL_128, DEFAULT_SCAN_ALL_128),
                ): bool,
                **device_options,
            }
        )

        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_bridge_network(self, user_input=None):
        coord = self._get_coordinator()
        if coord is None:
            return self.async_show_form(
                step_id="bridge_network",
                data_schema=self._bridge_network_schema(NETWORK_MODE_DHCP),
                errors={"base": "bridge_not_loaded"},
            )

        if user_input is not None:
            mode = user_input[CONF_NETWORK_MODE]
            if mode == NETWORK_MODE_STATIC:
                return await self.async_step_static_network()
            return await self.async_step_dhcp_confirm()

        default_mode = NETWORK_MODE_STATIC if coord.bridge_info.static_ip else NETWORK_MODE_DHCP
        return self.async_show_form(
            step_id="bridge_network",
            data_schema=self._bridge_network_schema(default_mode),
        )

    async def async_step_static_network(self, user_input=None):
        coord = self._get_coordinator()
        if coord is None:
            return self.async_show_form(
                step_id="static_network",
                data_schema=self._static_network_schema(None),
                errors={"base": "bridge_not_loaded"},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await coord.async_set_static_network(
                    static_ip=user_input[CONF_STATIC_IP],
                    netmask=user_input[CONF_NETMASK],
                    gateway=user_input[CONF_GATEWAY],
                    dns=user_input[CONF_DNS],
                )
            except HomeAssistantError:
                errors["base"] = "cannot_set_static_network"
            except Exception:
                errors["base"] = "cannot_set_static_network"
            else:
                return self.async_create_entry(title="", data=dict(self._entry.options))

        return self.async_show_form(
            step_id="static_network",
            data_schema=self._static_network_schema(coord),
            errors=errors,
        )

    async def async_step_dhcp_confirm(self, user_input=None):
        coord = self._get_coordinator()
        if coord is None:
            return self.async_show_form(
                step_id="dhcp_confirm",
                data_schema=self._dhcp_confirm_schema(),
                errors={"base": "bridge_not_loaded"},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_CONFIRM_DHCP):
                errors[CONF_CONFIRM_DHCP] = "confirm_required"
            else:
                try:
                    await coord.async_set_dhcp()
                except HomeAssistantError:
                    errors["base"] = "cannot_set_dhcp"
                except Exception:
                    errors["base"] = "cannot_set_dhcp"
                else:
                    return self.async_create_entry(title="", data=dict(self._entry.options))

        return self.async_show_form(
            step_id="dhcp_confirm",
            data_schema=self._dhcp_confirm_schema(),
            errors=errors,
        )

    def _get_coordinator(self) -> PellaCoordinator | None:
        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)

    @staticmethod
    def _bridge_network_schema(default_mode: str) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NETWORK_MODE, default=default_mode): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=NETWORK_MODE_DHCP, label="DHCP"),
                            selector.SelectOptionDict(value=NETWORK_MODE_STATIC, label="Static IP"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    def _static_network_schema(self, coord: PellaCoordinator | None) -> vol.Schema:
        bridge_info = coord.bridge_info if coord else None
        return vol.Schema(
            {
                vol.Required(
                    CONF_STATIC_IP,
                    default=(bridge_info.static_ip if bridge_info and bridge_info.static_ip else self._entry.data.get(CONF_HOST, "")),
                ): selector.TextSelector(selector.TextSelectorConfig()),
                vol.Required(
                    CONF_NETMASK,
                    default=(bridge_info.netmask if bridge_info and bridge_info.netmask else ""),
                ): selector.TextSelector(selector.TextSelectorConfig()),
                vol.Required(
                    CONF_GATEWAY,
                    default=(bridge_info.gateway if bridge_info and bridge_info.gateway else ""),
                ): selector.TextSelector(selector.TextSelectorConfig()),
                vol.Required(
                    CONF_DNS,
                    default=(bridge_info.dns if bridge_info and bridge_info.dns else ""),
                ): selector.TextSelector(selector.TextSelectorConfig()),
            }
        )

    @staticmethod
    def _dhcp_confirm_schema() -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_CONFIRM_DHCP, default=False): bool,
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PellaOptionsFlowHandler(config_entry)
