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

    registered: set[tuple[int, type[BinarySensorEntity]]] = set()

    @callback
    def _on_update() -> None:
        new_entities: list[BinarySensorEntity] = []
        for idx, dev in coord.data.items():
            if dev.device_type in (DEVICE_WINDOW_DOOR, DEVICE_GARAGE):
                classes = (PellaContactBinary, PellaCoverOffBinary)
            elif dev.device_type == DEVICE_LOCK:
                classes = (PellaLockBinary, PellaCoverOffBinary)
            else:
                continue

            for cls in classes:
                key = (idx, cls)
                if key not in registered:
                    registered.add(key)
                    new_entities.append(cls(coord, entry.entry_id, idx))

        if new_entities:
            async_add_entities(new_entities, update_before_add=False)

    _on_update()
    entry.async_on_unload(coord.async_add_listener(_on_update))

class _BaseBin(BinarySensorEntity):
    def __init__(self, coord: PellaCoordinator, entry_id: str, idx: int):
        self.coordinator = coord
        self._entry_id = entry_id
        self._idx = idx

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
        return self.coordinator.point_unique_id(self._idx, "contact")

    @property
    def name(self) -> str:
        return "Status"

    @property
    def is_on(self) -> bool | None:
        if not self._dev or not self._dev.status_hex:
            return None
        return self._dev.status_hex.upper() in OPEN_VALUES


class PellaLockBinary(_BaseBin):
    _attr_device_class = BinarySensorDeviceClass.LOCK

    @property
    def unique_id(self) -> str:
        return self.coordinator.point_unique_id(self._idx, "unlocked")

    @property
    def name(self) -> str:
        return "Status"

    @property
    def is_on(self) -> bool | None:
        if not self._dev or not self._dev.status_hex:
            return None
        return self._dev.status_hex.upper() in UNLOCK_VALUES


class PellaCoverOffBinary(_BaseBin):
    _attr_device_class = BinarySensorDeviceClass.TAMPER

    @property
    def unique_id(self) -> str:
        return self.coordinator.point_unique_id(self._idx, "coveroff")

    @property
    def name(self) -> str:
        return "Tamper"

    @property
    def is_on(self) -> bool | None:
        if not self._dev or not self._dev.status_hex:
            return None
        return self._dev.status_hex.upper() in COVER_OFF_VALUES
