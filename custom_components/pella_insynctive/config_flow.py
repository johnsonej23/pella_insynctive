from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN


class PellaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))

            if host:
                return self.async_create_entry(
                    title=f"Pella Insynctive ({host})",
                    data={CONF_HOST: host, CONF_PORT: port},
                )

            errors[CONF_HOST] = "required"

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))

            if host:
                return self.async_update_reload_and_abort(
                    entry,
                    title=f"Pella Insynctive ({host})",
                    data_updates={CONF_HOST: host, CONF_PORT: port},
                )

            errors[CONF_HOST] = "required"

        current_host = str(entry.data.get(CONF_HOST, ""))
        current_port = int(entry.data.get(CONF_PORT, DEFAULT_PORT))
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current_host): str,
                vol.Optional(CONF_PORT, default=current_port): int,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        from .options_flow import PellaOptionsFlowHandler
        return PellaOptionsFlowHandler(config_entry)
