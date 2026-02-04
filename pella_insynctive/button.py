from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PellaCoordinator


@dataclass(frozen=True, kw_only=True)
class PellaButtonEntityDescription(ButtonEntityDescription):
    press_fn: str


DESCRIPTIONS: tuple[PellaButtonEntityDescription, ...] = (
    PellaButtonEntityDescription(
        key="refresh_battery",
        name="Refresh Battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn="async_refresh_point_battery",
    ),
    PellaButtonEntityDescription(
        key="refresh_status",
        name="Refresh Status",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn="async_refresh_point_status",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: PellaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = []
    for idx in coord.data.keys():
        for desc in DESCRIPTIONS:
            entities.append(PellaPointButton(coord, entry.entry_id, idx, desc))

    async_add_entities(entities)


class PellaPointButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PellaCoordinator, entry_id: str, idx: int, description: PellaButtonEntityDescription) -> None:
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._idx = idx
        self.entity_description = description
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        dev = self.coordinator.data.get(self._idx)
        base = dev.point_id if dev and dev.point_id else f"point_{self._idx:03d}"
        return f"{self._entry_id}_{self.entity_description.key}_{base}"

    @property
    def device_info(self):
        # Attach to the point device (not the bridge)
        return self.coordinator.point_device_info(self._idx)

    @property
    def name(self) -> str:
        dev = self.coordinator.data.get(self._idx)
        dev_name = dev.name if dev else f"Point {self._idx:03d}"
        return f"{dev_name} {self.entity_description.name}"

    async def async_press(self) -> None:
        fn = getattr(self.coordinator, self.entity_description.press_fn)
        await fn(self._idx)
