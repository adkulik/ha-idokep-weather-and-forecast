"""Binary sensor platform for idokep."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)

from .coordinator import CURRENT, HOURLY
from .entity import IdokepEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import IdokepDataUpdateCoordinator
    from .data import IdokepConfigEntry

ENTITY_DESCRIPTIONS = (
    BinarySensorEntityDescription(
        key="storm_expected_1h",
        translation_key="storm_expected_1h",
        device_class=BinarySensorDeviceClass.SAFETY,
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: IdokepConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary_sensor platform."""
    async_add_entities(
        IdokepBinarySensor(
            coordinator=entry.runtime_data.coordinator,
            entity_description=entity_description,
        )
        for entity_description in ENTITY_DESCRIPTIONS
    )


class IdokepBinarySensor(IdokepEntity, BinarySensorEntity):
    """idokep binary_sensor class."""

    @property
    def has_entity_name(self) -> bool:
        """Return True if the entity has a name."""
        return True

    def __init__(
        self,
        coordinator: IdokepDataUpdateCoordinator,
        entity_description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary_sensor class."""
        # The storm comes from the current weather and the hourly forecast pages
        super().__init__(coordinator, frozenset({CURRENT, HOURLY}))
        self.entity_description = entity_description
        # Set unique_id for entity persistence after restart
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )

    @property
    def available(self) -> bool:
        """Return False without a value: unavailable rather than unknown."""
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """
        Return true if it's stormy now, or in the current or next hour.

        Now: the current weather (fetched every 10 minutes; while that
        fails, the forecast's running hour); the hours: the hourly forecast.
        On if either says so; off only if both are known, else None.
        """
        values = [
            self.coordinator.data.get(key) for key in ("storm_now", "storm_expected")
        ]
        if any(values):
            return True
        if None in values:
            return None
        return False
