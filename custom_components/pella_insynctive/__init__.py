from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import CONF_HOST, CONF_PORT, DOMAIN
from .coordinator import PellaCoordinator

PLATFORMS: list[Platform] = [
    Platform.COVER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
]

SERVICE_SET_STATIC_NETWORK = "set_static_network"
SERVICE_SET_DHCP = "set_dhcp"

ATTR_ENTRY_ID = "entry_id"
ATTR_STATIC_IP = "static_ip"
ATTR_NETMASK = "netmask"
ATTR_GATEWAY = "gateway"
ATTR_DNS = "dns"

SET_STATIC_NETWORK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_STATIC_IP): cv.string,
        vol.Required(ATTR_NETMASK): cv.string,
        vol.Required(ATTR_GATEWAY): cv.string,
        vol.Required(ATTR_DNS): cv.string,
    }
)

SET_DHCP_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)


def _clear_entity_tracking(hass: HomeAssistant, entry: ConfigEntry) -> None:
    for key in (
        f"{DOMAIN}_bin_{entry.entry_id}",
        f"{DOMAIN}_shade_{entry.entry_id}",
        f"{DOMAIN}_sensor_ids_{entry.entry_id}",
        f"{DOMAIN}_button_{entry.entry_id}",
    ):
        hass.data.pop(key, None)


def _coordinator_from_service_call(hass: HomeAssistant, call: ServiceCall) -> PellaCoordinator:
    coordinators: dict[str, PellaCoordinator] = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(ATTR_ENTRY_ID)

    if entry_id:
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise HomeAssistantError(f"Pella Insynctive config entry not found: {entry_id}")
        return coordinator

    if len(coordinators) == 1:
        return next(iter(coordinators.values()))

    if not coordinators:
        raise HomeAssistantError("No Pella Insynctive bridge is loaded")

    raise HomeAssistantError("Multiple Pella Insynctive bridges are loaded; provide entry_id")


def _register_services(hass: HomeAssistant) -> None:
    async def async_set_static_network(call: ServiceCall) -> None:
        coordinator = _coordinator_from_service_call(hass, call)
        await coordinator.async_set_static_network(
            static_ip=call.data[ATTR_STATIC_IP],
            netmask=call.data[ATTR_NETMASK],
            gateway=call.data[ATTR_GATEWAY],
            dns=call.data[ATTR_DNS],
        )

    async def async_set_dhcp(call: ServiceCall) -> None:
        coordinator = _coordinator_from_service_call(hass, call)
        await coordinator.async_set_dhcp()

    if not hass.services.has_service(DOMAIN, SERVICE_SET_STATIC_NETWORK):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_STATIC_NETWORK,
            async_set_static_network,
            schema=SET_STATIC_NETWORK_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_DHCP):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_DHCP,
            async_set_dhcp,
            schema=SET_DHCP_SCHEMA,
        )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _clear_entity_tracking(hass, entry)

    coordinator = PellaCoordinator(hass, entry)
    await coordinator.async_start()

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.bridge_id)},
        name=coordinator.bridge_name,
        manufacturer="Pella",
        model="Insynctive Bridge",
        sw_version=None,
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: PellaCoordinator = hass.data[DOMAIN][entry.entry_id]
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await coordinator.async_stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _clear_entity_tracking(hass, entry)
    return unload_ok
