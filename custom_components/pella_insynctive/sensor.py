from __future__ import annotations

import re
from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorEntityDescription
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .coordinator import PellaCoordinator

RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@dataclass(frozen=True, kw_only=True)
class PellaBridgeSensorDescription(SensorEntityDescription):
    attr: str
    parse_ipv4: bool = False


BRIDGE_DESCRIPTIONS: tuple[PellaBridgeSensorDescription, ...] = (
    PellaBridgeSensorDescription(
        key="connection_host",
        name="Assigned IP",
        entity_category=EntityCategory.DIAGNOSTIC,
        attr="configured_host",
    ),
    PellaBridgeSensorDescription(
        key="static_ip",
        name="Static IP",
        entity_category=EntityCategory.DIAGNOSTIC,
        attr="static_ip",
        parse_ipv4=True,
    ),
    PellaBridgeSensorDescription(
        key="netmask",
        name="Netmask",
        entity_category=EntityCategory.DIAGNOSTIC,
        attr="netmask",
        parse_ipv4=True,
    ),
    PellaBridgeSensorDescription(
        key="gateway",
        name="Gateway",
        entity_category=EntityCategory.DIAGNOSTIC,
        attr="gateway",
        parse_ipv4=True,
    ),
    PellaBridgeSensorDescription(
        key="dns",
        name="DNS",
        entity_category=EntityCategory.DIAGNOSTIC,
        attr="dns",
        parse_ipv4=True,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coord: PellaCoordinator = hass.data[DOMAIN][entry.entry_id]
    storage_key = f"{DOMAIN}_sensor_ids_{entry.entry_id}"
    added_unique_ids: set[str] = hass.data.setdefault(storage_key, set())

    def _make_new_sensors() -> list[SensorEntity]:
        new_entities: list[SensorEntity] = []

        for desc in BRIDGE_DESCRIPTIONS:
            entity = PellaBridgeInfoSensor(coord, entry.entry_id, desc)
            if entity.unique_id not in added_unique_ids:
                new_entities.append(entity)
                added_unique_ids.add(entity.unique_id)

        for idx in coord.data:
            for cls in (PellaBatterySensor, PellaBridgeIndexSensor, PellaRawStatusSensor):
                entity = cls(coord, entry.entry_id, idx)
                if entity.unique_id not in added_unique_ids:
                    new_entities.append(entity)
                    added_unique_ids.add(entity.unique_id)

        return new_entities

    async_add_entities(_make_new_sensors(), update_before_add=False)

    @callback
    def _on_update() -> None:
        new_entities = _make_new_sensors()
        if new_entities:
            async_add_entities(new_entities, update_before_add=False)

    entry.async_on_unload(coord.async_add_listener(_on_update))


class PellaBridgeInfoSensor(SensorEntity):
    def __init__(
        self,
        coord: PellaCoordinator,
        entry_id: str,
        description: PellaBridgeSensorDescription,
    ) -> None:
        self.coordinator = coord
        self._entry_id = entry_id
        self.entity_description = description
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_bridge_{self.entity_description.key}"

    @property
    def name(self) -> str:
        return self.entity_description.name

    @property
    def device_info(self):
        return self.coordinator.bridge_device_info()

    @property
    def native_value(self) -> str | None:
        value = getattr(self.coordinator.bridge_info, self.entity_description.attr, None)
        if value is None:
            return None

        value = str(value).strip()
        if not value:
            return None

        if not self.entity_description.parse_ipv4:
            return value

        match = RE_IPV4.search(value)
        if not match:
            return None

        return match.group(0)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))


class _BasePointSensor(SensorEntity):
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


class PellaBatterySensor(_BasePointSensor):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def unique_id(self) -> str:
        return self.coordinator.point_unique_id(self._idx, "battery")

    @property
    def name(self) -> str:
        return "Battery"

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


class PellaBridgeIndexSensor(_BasePointSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return self.coordinator.point_unique_id(self._idx, "bridge_index")

    @property
    def name(self) -> str:
        return "Bridge Index"

    @property
    def native_value(self) -> int:
        return int(self._idx)


class PellaRawStatusSensor(_BasePointSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return self.coordinator.point_unique_id(self._idx, "rawstatus")

    @property
    def name(self) -> str:
        return "Raw Status"

    @property
    def native_value(self) -> str | None:
        if not self._dev:
            return None
        return getattr(self._dev, "raw_status_hex", None) or self._dev.status_hex
