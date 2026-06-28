from __future__ import annotations

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_SHADE, DOMAIN
from .coordinator import PellaCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coord: PellaCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [PellaShade(coord, entry.entry_id, idx) for idx, d in coord.data.items() if d.device_type == DEVICE_SHADE],
        update_before_add=False,
    )

    @callback
    def _on_update() -> None:
        existing = {e._idx for e in hass.data.setdefault(f"{DOMAIN}_shade_{entry.entry_id}", [])}
        new = []
        for idx, d in coord.data.items():
            if d.device_type == DEVICE_SHADE and idx not in existing:
                new.append(PellaShade(coord, entry.entry_id, idx))
        if new:
            async_add_entities(new, update_before_add=False)

    coord.async_add_listener(_on_update)


class PellaShade(CoverEntity):
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, coord: PellaCoordinator, entry_id: str, idx: int):
        self.coordinator = coord
        self._entry_id = entry_id
        self._idx = idx
        coord.hass.data.setdefault(f"{DOMAIN}_shade_{entry_id}", []).append(self)

    @property
    def device_info(self):
        return self.coordinator.point_device_info(self._idx)

    @property
    def unique_id(self) -> str:
        dev = self.coordinator.data.get(self._idx)
        base = dev.point_id if dev and dev.point_id else f"point_{self._idx:03d}"
        return f"{self._entry_id}_shade_{base}"

    @property
    def name(self) -> str:
        dev = self.coordinator.data.get(self._idx)
        if dev:
            return dev.name.replace("Pella Shade", "Shade")
        return f"Shade {self._idx:03d}"

    @property
    def current_cover_position(self) -> int | None:
        dev = self.coordinator.data.get(self._idx)
        if not dev or not dev.status_hex:
            return None
        return self.coordinator.shade_value_to_position(dev.status_hex)

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        return None if pos is None else pos <= 0

    async def async_open_cover(self, **kwargs) -> None:
        await self.coordinator.set_shade_position(self._idx, 100)

    async def async_close_cover(self, **kwargs) -> None:
        await self.coordinator.set_shade_position(self._idx, 0)

    async def async_stop_cover(self, **kwargs) -> None:
        await self.coordinator.pointset(self._idx, 0x6A)

    async def async_set_cover_position(self, **kwargs) -> None:
        pos = int(kwargs["position"])
        pos = max(0, min(100, pos))
        await self.coordinator.set_shade_position(self._idx, pos)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
