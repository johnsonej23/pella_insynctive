from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .coordinator import PellaCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coord: PellaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for idx in coord.data:
        entities.append(PellaBatterySensor(coord, entry.entry_id, idx))
        entities.append(PellaBridgeIndexSensor(coord, entry.entry_id, idx))
        entities.append(PellaRawStatusSensor(coord, entry.entry_id, idx))

    async_add_entities(entities, update_before_add=False)

    @callback
    def _on_update() -> None:
        existing = {e.unique_id for e in hass.data.setdefault(f"{DOMAIN}_sens_{entry.entry_id}", [])}
        new = []
        for idx in coord.data:
            for cls in (PellaBatterySensor, PellaBridgeIndexSensor, PellaRawStatusSensor):
                ent = cls(coord, entry.entry_id, idx)
                if ent.unique_id not in existing:
                    new.append(ent)
        if new:
            async_add_entities(new, update_before_add=False)

    coord.async_add_listener(_on_update)


class _BaseSensor(SensorEntity):
    def __init__(self, coord: PellaCoordinator, entry_id: str, idx: int):
        self.coordinator = coord
        self._entry_id = entry_id
        self._idx = idx
        coord.hass.data.setdefault(f"{DOMAIN}_sens_{entry_id}", []).append(self)

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


class PellaBatterySensor(_BaseSensor):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def unique_id(self) -> str:
        base = self._dev.point_id if self._dev and self._dev.point_id else f"point_{self._idx:03d}"
        return f"{self._entry_id}_battery_{base}"

    @property
    def name(self) -> str:
        if self._dev:
            return f"{self._dev.name} Battery"
        return f"Point {self._idx:03d} Battery"

    @property
    def native_value(self) -> int | None:
        if not self._dev or not self._dev.battery_hex:
            return None
        s = self._dev.battery_hex.strip()
        if not (s.startswith("$") and len(s) == 3):
            return None
        try:
            v = int(s[1:], 16)
            return max(0, min(100, v))
        except ValueError:
            return None


class PellaBridgeIndexSensor(_BaseSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        base = self._dev.point_id if self._dev and self._dev.point_id else f"point_{self._idx:03d}"
        return f"{self._entry_id}_bridge_index_{base}"

    @property
    def name(self) -> str:
        if self._dev:
            return f"{self._dev.name} Bridge Index"
        return f"Point {self._idx:03d} Bridge Index"

    @property
    def native_value(self) -> int:
        return int(self._idx)



class PellaRawStatusSensor(_BaseSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        base = self._dev.point_id if self._dev and self._dev.point_id else f"point_{self._idx:03d}"
        return f"{self._entry_id}_rawstatus_{base}"

    @property
    def name(self) -> str:
        if self._dev:
            return f"{self._dev.name} Raw Status"
        return f"Point {self._idx:03d} Raw Status"

    @property
    def native_value(self) -> str | None:
        if not self._dev:
            return None
        return self._dev.status_hex
