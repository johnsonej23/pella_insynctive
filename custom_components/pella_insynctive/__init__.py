from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import PellaCoordinator

PLATFORMS: list[Platform] = [
    Platform.COVER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
]


def _clear_entity_tracking(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear per-platform dynamic entity tracking for this config entry.

    These values live in hass.data and can survive a config entry reload. If they
    are left behind, the platforms may think point entities were already added
    and skip attaching fresh entities to the new coordinator after reconfigure.
    """
    for key in (
        f"{DOMAIN}_bin_{entry.entry_id}",
        f"{DOMAIN}_shade_{entry.entry_id}",
        f"{DOMAIN}_sensor_ids_{entry.entry_id}",
        f"{DOMAIN}_button_{entry.entry_id}",
    ):
        hass.data.pop(key, None)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
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
