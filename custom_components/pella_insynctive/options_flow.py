from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
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


class PellaOptionsFlowHandler(OptionsFlow):
    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        o = self._entry.options

        device_options: dict = {}

        # If the integration is loaded, offer per-device naming/area overrides.
        coord = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
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

        return self.async_show_form(step_id="init", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PellaOptionsFlowHandler(config_entry)
