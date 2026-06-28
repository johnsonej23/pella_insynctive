from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
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
    storage_key = f"{DOMAIN}_button_{entry.entry_id}"
    hass.data.setdefault(storage_key, [])

    def _make_new_buttons() -> list[ButtonEntity]:
        existing = {entity.unique_id for entity in hass.data[storage_key]}
        new_entities: list[ButtonEntity] = []

        for idx in coord.data:
            for desc in DESCRIPTIONS:
                entity = PellaPointButton(coord, entry.entry_id, idx, desc)
                if entity.unique_id not in existing:
                    new_entities.append(entity)
                    hass.data[storage_key].append(entity)
                    existing.add(entity.unique_id)

        return new_entities

    async_add_entities(_make_new_buttons())

    @callback
    def _on_update() -> None:
        new_entities = _make_new_buttons()
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coord.async_add_listener(_on_update))


class PellaPointButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PellaCoordinator,
        entry_id: str,
        idx: int,
        description: PellaButtonEntityDescription,
    ) -> None:
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
        # Attach to the point device, not the bridge.
        return self.coordinator.point_device_info(self._idx)

    @property
    def name(self) -> str:
        dev = self.coordinator.data.get(self._idx)
        dev_name = dev.name if dev else f"Point {self._idx:03d}"
        return f"{dev_name} {self.entity_description.name}"

    async def async_press(self) -> None:
        fn = getattr(self.coordinator, self.entity_description.press_fn)
        await fn(self._idx)
