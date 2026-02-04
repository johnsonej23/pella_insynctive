from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_GARAGE, DEVICE_LOCK, DEVICE_WINDOW_DOOR, DOMAIN
from .coordinator import PellaCoordinator

OPEN_VALUES = {"01", "05"}
UNLOCK_VALUES = {"02", "06"}
COVER_OFF_VALUES = {"04", "05", "06"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coord: PellaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []
    for idx, dev in coord.data.items():
        if dev.device_type in (DEVICE_WINDOW_DOOR, DEVICE_GARAGE):
            entities.append(PellaContactBinary(coord, entry.entry_id, idx))
            entities.append(PellaCoverOffBinary(coord, entry.entry_id, idx))
        elif dev.device_type == DEVICE_LOCK:
            entities.append(PellaLockBinary(coord, entry.entry_id, idx))
            entities.append(PellaCoverOffBinary(coord, entry.entry_id, idx))

    async_add_entities(entities, update_before_add=False)

    @callback
    def _on_update() -> None:
        existing = {e.unique_id for e in hass.data.setdefault(f"{DOMAIN}_bin_{entry.entry_id}", [])}
        new = []
        for idx, dev in coord.data.items():
            if dev.device_type in (DEVICE_WINDOW_DOOR, DEVICE_GARAGE):
                for cls in (PellaContactBinary, PellaCoverOffBinary):
                    ent = cls(coord, entry.entry_id, idx)
                    if ent.unique_id not in existing:
                        new.append(ent)
            elif dev.device_type == DEVICE_LOCK:
                for cls in (PellaLockBinary, PellaCoverOffBinary):
                    ent = cls(coord, entry.entry_id, idx)
                    if ent.unique_id not in existing:
                        new.append(ent)
        if new:
            async_add_entities(new, update_before_add=False)

    coord.async_add_listener(_on_update)


class _BaseBin(BinarySensorEntity):
    def __init__(self, coord: PellaCoordinator, entry_id: str, idx: int):
        self.coordinator = coord
        self._entry_id = entry_id
        self._idx = idx
        coord.hass.data.setdefault(f"{DOMAIN}_bin_{entry_id}", []).append(self)

    @property
    def device_info(self):
        return self.coordinator.point_device_info(self._idx)

    @property
    def _dev(self):
        return self.coordinator.data.get(self._idx)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))


class PellaContactBinary(_BaseBin):
    _attr_device_class = BinarySensorDeviceClass.OPENING

    @property
    def unique_id(self) -> str:
        base = self._dev.point_id if self._dev and self._dev.point_id else f"point_{self._idx:03d}"
        return f"{self._entry_id}_contact_{base}"

    @property
    def name(self) -> str:
        return f"{self._dev.name} Status" if self._dev else f"Point {self._idx:03d} Status"

    @property
    def is_on(self) -> bool | None:
        if not self._dev or not self._dev.status_hex:
            return None
        return self._dev.status_hex.upper() in OPEN_VALUES


class PellaLockBinary(_BaseBin):
    _attr_device_class = BinarySensorDeviceClass.LOCK

    @property
    def unique_id(self) -> str:
        base = self._dev.point_id if self._dev and self._dev.point_id else f"point_{self._idx:03d}"
        return f"{self._entry_id}_unlocked_{base}"

    @property
    def name(self) -> str:
        return f"{self._dev.name} Status" if self._dev else f"Point {self._idx:03d} Status"

    @property
    def is_on(self) -> bool | None:
        if not self._dev or not self._dev.status_hex:
            return None
        return self._dev.status_hex.upper() in UNLOCK_VALUES


class PellaCoverOffBinary(_BaseBin):
    _attr_device_class = BinarySensorDeviceClass.TAMPER

    @property
    def unique_id(self) -> str:
        base = self._dev.point_id if self._dev and self._dev.point_id else f"point_{self._idx:03d}"
        return f"{self._entry_id}_coveroff_{base}"

    @property
    def name(self) -> str:
        return f"{self._dev.name} Tamper" if self._dev else f"Point {self._idx:03d} Tamper"

    @property
    def is_on(self) -> bool | None:
        if not self._dev or not self._dev.status_hex:
            return None
        return self._dev.status_hex.upper() in COVER_OFF_VALUES
