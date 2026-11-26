"""Időkép Weather Entity for Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.weather import (
    ATTR_CONDITION_CLEAR_NIGHT,
    ATTR_CONDITION_SUNNY,
    Forecast,
    WeatherEntity,
)
from homeassistant.components.weather.const import WeatherEntityFeature
from homeassistant.const import (
    STATE_UNKNOWN,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
)
from homeassistant.core import callback
from homeassistant.helpers import sun

from .const import (
    ATTRIBUTION,
    DOMAIN,
    FORECAST_DEVICE,
    LOGGER,
    MANUFACTURER,
)
from .coordinator import CURRENT, HOURLY
from .entity import IdokepEntity, device_name_fields, forecast_page_url

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import IdokepDataUpdateCoordinator
    from .data import IdokepConfigEntry

# Hourly forecast fields only used inside the integration: for the storm sensor
# and for the Wind strength sensor
INTERNAL_FIELDS = frozenset({"storm", "wind_strength"})


class IdokepWeatherEntity(IdokepEntity, WeatherEntity):  # pylint: disable=too-many-public-methods
    """Idokep Weather entity."""

    # The only entity with an attribution
    _attr_attribution = ATTRIBUTION

    @property
    def device_info(self) -> dict:
        """Return device information for the integration."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.config_entry.entry_id)},
            **device_name_fields(self.coordinator.config_entry, FORECAST_DEVICE),
            "manufacturer": MANUFACTURER,
            "model": "Weather forecast",
            "entry_type": "service",
            "configuration_url": forecast_page_url(self.coordinator.config_entry),
        }

    @property
    def has_entity_name(self) -> bool:
        """Return True if the entity has a name."""
        return True

    def __init__(self, coordinator: IdokepDataUpdateCoordinator) -> None:
        """Initialize the IdokepWeatherEntity with the given coordinator."""
        # Current conditions; forecast page for hourly/daily forecast and wind.
        # The station maps' values are shown too, but those pages are fetched
        # only for their own sensors.
        super().__init__(coordinator, frozenset({CURRENT, HOURLY}))
        # Main entity of the device: named just like the device. The unique ID
        # doesn't contain the settlement, so it stays when the settlement is
        # changed in Configure.
        self._attr_name = None
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_weather"
        self._attr_supported_features = (
            WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
        )
        LOGGER.debug(
            "Initialized weather entity with unique_id: %s", self._attr_unique_id
        )

    @property
    def available(self) -> bool:
        """
        Return False without a condition: unavailable rather than unknown.

        The condition is the entity's state.
        """
        return super().available and self._current("condition") not in (
            None,
            STATE_UNKNOWN,
        )

    def _current(self, key: str) -> Any:
        """
        Return a current condition from the current weather page.

        While that page can't be downloaded, the value is the one of the
        forecast's running hour.
        """
        data = self.coordinator.data
        return data.get("forecast_now", data).get(key)

    @property
    def condition(self) -> str | None:
        """Return the current weather condition."""
        condition = self._current("condition")
        if condition == ATTR_CONDITION_SUNNY and not sun.is_up(self.hass):
            return ATTR_CONDITION_CLEAR_NIGHT
        return condition

    @property
    def native_temperature(self) -> float | None:
        """Return the current temperature in native units."""
        temp = self._current("temperature")
        return float(temp) if temp is not None else None

    @property
    def wind_bearing(self) -> float | None:
        """Return the wind bearing for the current hour."""
        return self.coordinator.data.get("wind_bearing")

    @property
    def native_wind_speed(self) -> float | None:
        """Return the estimated wind speed for the current hour (km/h)."""
        return self.coordinator.data.get("wind_speed")

    @property
    def native_apparent_temperature(self) -> float | None:
        """Return the temperature it feels like, from the sensor's calculation."""
        return self.coordinator.data.get("apparent_temperature")

    @property
    def native_wind_gust_speed(self) -> float | None:
        """Return the estimated gust for the current hour (km/h)."""
        return self.coordinator.data.get("wind_gust_speed")

    @property
    def native_wind_speed_unit(self) -> str:
        """Return the unit of the (estimated) wind speed."""
        return UnitOfSpeed.KILOMETERS_PER_HOUR

    @property
    def native_temperature_unit(self) -> str:
        """Return the unit of measurement for temperature."""
        return "°C"

    @property
    def humidity(self) -> float | None:
        """Return the average humidity of the settlement's stations (%)."""
        return self.coordinator.data.get("humidity")

    @property
    def cloud_coverage(self) -> int | None:
        """Return the cloud coverage (%), estimated from the description."""
        return self._current("cloud_coverage")

    @property
    def native_dew_point(self) -> float | None:
        """Return the average dew point of the settlement's stations (°C)."""
        return self.coordinator.data.get("dew_point")

    @property
    def native_pressure(self) -> float | None:
        """Return the average sea-level pressure of the settlement's stations."""
        return self.coordinator.data.get("pressure")

    @property
    def native_pressure_unit(self) -> str:
        """Return the unit of the pressure."""
        return UnitOfPressure.HPA

    @property
    def uv_index(self) -> float | None:
        """Return the average UV index of the settlement's stations."""
        return self.coordinator.data.get("uv_index")

    @property
    def native_precipitation_unit(self) -> str:
        """Return the unit of the forecast and current-hour precipitation."""
        return UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def extra_state_attributes(self) -> dict[str, float] | None:
        """
        Return the current hour's precipitation (mm).

        HA's weather entity has no attribute for it; the Precipitation
        sensor has the same value.
        """
        precipitation = self.coordinator.data.get("precipitation")
        if precipitation is None:
            return None
        return {"precipitation": precipitation}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write state and push fresh forecasts to forecast subscribers."""
        super()._handle_coordinator_update()
        self.coordinator.config_entry.async_create_task(
            self.hass, self.async_update_listeners(None)
        )

    async def async_forecast_hourly(self) -> list[Forecast]:
        """Return the hourly forecast (without the parser's internal fields)."""
        return [
            {key: value for key, value in item.items() if key not in INTERNAL_FIELDS}
            for item in self.coordinator.data.get("hourly_forecast", [])
        ]

    async def async_forecast_daily(self) -> list[Forecast]:
        """Return the daily forecast."""
        return self.coordinator.data.get("daily_forecast", [])


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: IdokepConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the weather entity."""
    weather_entity = IdokepWeatherEntity(entry.runtime_data.coordinator)
    async_add_entities([weather_entity])
