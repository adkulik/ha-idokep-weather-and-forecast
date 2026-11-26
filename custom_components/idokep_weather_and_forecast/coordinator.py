"""DataUpdateCoordinator for idokep."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import IdokepApiClientCommunicationError
from .calculations import apparent_temperature
from .const import (
    CURRENT,
    HOURLY,
    HUMIDITY_MAP,
    PRESSURE_MAP,
    UV_MAP,
)
from .failures import FailureLog
from .storage import (
    SAVE_DELAY,
    STORAGE_VERSION,
    forecast_as_of,
    restored_forecast,
    running_hour,
    serialize,
    storage_key,
    today_item,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from homeassistant.core import HomeAssistant

    from .data import IdokepConfigEntry

# Key a successful fetch of each source must contain
REQUIRED_KEYS = {
    CURRENT: "temperature",
    HOURLY: "hourly_forecast",
    HUMIDITY_MAP: "humidity",
    PRESSURE_MAP: "pressure",
    UV_MAP: "uv_index",
}


# The entities reading each page, by the end of their unique ID. Needed at
# startup, before the entities listen, and for the sensors' listener contexts.
SOURCE_ENTITIES = {
    CURRENT: (
        "weather",
        "temperature",
        "condition",
        "cloud_coverage",
        "apparent_temperature",
        "storm_expected_1h",
    ),
    HOURLY: (
        "weather",
        "apparent_temperature",
        "temperature_max_today",
        "temperature_min_today",
        "wind_bearing",
        "wind_direction",
        "wind_gust_speed",
        "wind_strength",
        "wind_speed",
        "precipitation",
        "precipitation_probability",
        "alert_level",
        "warning_level",
        "storm_expected_1h",
    ),
    HUMIDITY_MAP: ("humidity", "dew_point", "apparent_temperature"),
    PRESSURE_MAP: ("pressure",),
    UV_MAP: ("uv_index",),
}


# After a restart or reload every source that fails is retried every minute
# until it first works
STARTUP_RETRY_INTERVAL = timedelta(minutes=1)


def next_ten_minutes(now: datetime) -> datetime:
    """Return the next x:00, x:10, x:20, x:30, x:40 or x:50 after now."""
    # Budapest's UTC offset is whole hours, so UTC boundaries match local ones
    now = dt_util.as_utc(now)
    floored = now.replace(minute=now.minute - now.minute % 10, second=0, microsecond=0)
    return floored + timedelta(minutes=10)


def next_full_hour(now: datetime) -> datetime:
    """Return the next x:00 after now."""
    now = dt_util.as_utc(now)
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def floor_to_minute(now: datetime) -> datetime:
    """Drop seconds so timer jitter can't push a due check past its minute."""
    return now.replace(second=0, microsecond=0)


@dataclass
class SourceSchedule:
    """When a data source is fetched next."""

    next_regular: Callable[[datetime], datetime]
    retry_interval: timedelta
    # None means due immediately (nothing fetched yet)
    next_due: datetime | None = None
    # False until the first successful fetch since the start
    succeeded: bool = False

    def is_due(self, now: datetime) -> bool:
        """Return True if the source should be fetched at now."""
        return self.next_due is None or now >= self.next_due

    def mark_success(self, now: datetime) -> None:
        """Schedule the next regular fetch."""
        self.succeeded = True
        self.next_due = self.next_regular(now)

    def mark_failure(self, now: datetime) -> None:
        """
        Schedule a retry: every minute while nothing has worked yet.

        The regular fetch comes in any case, if it is earlier than the retry.
        """
        interval = self.retry_interval if self.succeeded else STARTUP_RETRY_INTERVAL
        self.next_due = min(now + interval, self.next_regular(now))


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class IdokepDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Class to manage fetching data from the API.

    Each idokep.hu page has its own schedule:
    - current conditions (/idojaras/): every 10 minutes (x:00:59, x:10:59,
      ...), retry every minute
    - forecast (/elorejelzes/: hourly and daily forecast, alerts, today's
      max/min): every full hour (x:00:59), retry every 10 minutes
    - humidity map (/para: humidity, dew point): every 10 minutes, retry every
      minute
    - pressure map (/legnyomas: pressure): every 10 minutes, retry every minute
    - UV map (/uv: UV index): every 10 minutes, retry every minute

    After a restart or reload, a page that hasn't been fetched yet is retried
    every minute, also the forecast. A page that fails has no data until it is
    fetched again; its failure and its recovery are each logged once. The
    exception is the forecast: while its page fails, the last downloaded one
    (after a restart the saved one) is followed hour by hour, without alerts
    and warnings. While the current page fails, the forecast's running hour
    stands in for the weather entity's conditions and the storm now.

    There is no update_interval; async_tick is called every minute at second
    59 (the schedules count in whole minutes, so x:00:59 is x:00) and
    refreshes only when a source used by an enabled entity is due. A page
    whose entities are all disabled is not fetched and its data is dropped.
    """

    config_entry: IdokepConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        logger: Logger,
        name: str,
        config_entry: IdokepConfigEntry,
    ) -> None:
        """Initialize the data update coordinator."""
        super().__init__(
            hass,
            logger=logger,
            name=name,
            update_interval=None,
            config_entry=config_entry,
        )
        self.config_entry = config_entry
        self.schedules: dict[str, SourceSchedule] = {
            CURRENT: SourceSchedule(next_ten_minutes, timedelta(minutes=1)),
            HOURLY: SourceSchedule(next_full_hour, timedelta(minutes=10)),
            HUMIDITY_MAP: SourceSchedule(next_ten_minutes, timedelta(minutes=1)),
            PRESSURE_MAP: SourceSchedule(next_ten_minutes, timedelta(minutes=1)),
            UV_MAP: SourceSchedule(next_ten_minutes, timedelta(minutes=1)),
        }
        # Each source's last result; empty after a failure
        self._source_data: dict[str, dict[str, Any]] = {
            source: {} for source in self.schedules
        }
        location = config_entry.data["location"]
        self._failures = {
            source: FailureLog(logger, f"Fetching {source} data for {location}")
            for source in self.schedules
        }
        # The last downloaded forecast, saved under .storage/ (set up by
        # async_restore), and whether the page has failed since
        self._last_forecast: dict[str, Any] = {}
        self._forecast_failed = False
        self._store: Store | None = None

    async def async_restore(self) -> None:
        """
        Load the forecast saved before the last restart.

        Called before the first refresh. It is shown while the forecast page
        can't be downloaded, and the parser takes from it the parts a
        downloaded page no longer has, the current hour and today (see storage).
        """
        self._store = Store(
            self.hass, STORAGE_VERSION, storage_key(self.config_entry.entry_id)
        )
        saved = await self._store.async_load() or {}
        location = self.config_entry.data["location"]
        if saved.get("location", location) != location:
            # Saved before the location was changed in the options
            self.logger.debug("Saved data is for %s, not used", saved["location"])
            return

        self._last_forecast = restored_forecast(saved)
        client = self.config_entry.runtime_data.client
        today = dt_util.as_local(dt_util.utcnow()).date()
        if item := today_item(self._last_forecast, today):
            client.restore_today_forecast(item)
        if hourly := self._last_forecast["hourly_forecast"]:
            client.restore_hourly_forecast(hourly)

    def _data_to_save(self) -> dict[str, Any]:
        """Return the data to write to disk."""
        return serialize(self.config_entry.data["location"], self._last_forecast)

    def needed_sources(self) -> set[str]:
        """
        Return the sources used by at least one enabled entity.

        Entities register the pages they read as their listener context.
        Disabled entities (also those of a disabled device) never listen, so
        their pages drop out; with no entity listening nothing is needed.
        """
        needed: set[str] = set()
        for sources in self.async_contexts():
            needed |= sources
        return needed

    def _sources_at_startup(self) -> set[str]:
        """
        Return the sources to fetch before any entity listens.

        A page is left out if all of its entities are registered as disabled;
        one not registered yet is new, so it is enabled by default. Current
        conditions are always fetched, since the entry starts from them.
        """
        entry_id = self.config_entry.entry_id
        registered = {
            entity.unique_id: entity
            for entity in er.async_entries_for_config_entry(
                er.async_get(self.hass), entry_id
            )
        }
        disabled = {
            source
            for source, keys in SOURCE_ENTITIES.items()
            if all(
                (entity := registered.get(f"{entry_id}_{key}")) is not None
                and entity.disabled
                for key in keys
            )
        }
        return set(self.schedules) - (disabled - {CURRENT})

    async def async_tick(self, now: datetime) -> None:
        """Refresh if a needed source is due (called every minute at second 59)."""
        # Honour the entry's "Enable polling for updates" system option, as
        # Home Assistant's own polling does
        if self.config_entry.pref_disable_polling:
            return
        now = floor_to_minute(now)
        if any(self.schedules[source].is_due(now) for source in self.needed_sources()):
            await self.async_refresh()

    def _fetchers(self) -> dict[str, Callable[[str], Awaitable[dict[str, Any]]]]:
        """Return the API call for each source."""
        client = self.config_entry.runtime_data.client
        return {
            CURRENT: client.async_get_current_weather,
            HOURLY: client.async_get_hourly_forecast,
            HUMIDITY_MAP: client.async_get_humidity_map,
            PRESSURE_MAP: client.async_get_pressure_map,
            UV_MAP: client.async_get_uv_map,
        }

    async def _async_update_data(self) -> dict:
        """
        Fetch the sources that are due and merge them with the others.

        A source that fails has no data until it is fetched again, so its
        entities are unavailable. A failing forecast page is replaced by the
        last forecast as it stands now.
        """
        location = self.config_entry.data["location"]
        now = floor_to_minute(dt_util.utcnow())
        # The first refresh runs during setup, before any entity listens; it
        # fetches every page with an enabled entity so the entry starts with data
        wanted = self._sources_at_startup() if self.data is None else self.needed_sources()
        due = [
            source
            for source, schedule in self.schedules.items()
            if source in wanted and schedule.is_due(now)
        ]
        fetchers = self._fetchers()
        results = await asyncio.gather(
            *(fetchers[source](location) for source in due),
            return_exceptions=True,
        )

        for source, result in zip(due, results, strict=True):
            self._take_result(source, result, now)

        # A page nobody reads anymore must not serve its old values (e.g. the
        # weather entity's humidity)
        for source in self.schedules.keys() - wanted:
            self._source_data[source] = {}

        # Applied at every update, so it moves on with the hours
        if self._forecast_failed and HOURLY in wanted:
            self._source_data[HOURLY] = forecast_as_of(self._last_forecast, now)

        data: dict[str, Any] = {}
        for source in self.schedules:
            data.update(self._source_data[source])
        # Without the current page, the forecast's running hour stands in for
        # the weather entity's conditions and for whether it's stormy now
        if not self._source_data[CURRENT] and (
            item := running_hour(self._source_data[HOURLY], now)
        ):
            data["forecast_now"] = item
            data["storm_now"] = bool(item.get("storm"))
        # Recalculated at every update, from the pages' latest values
        speed = data.get("wind_speed")
        if speed is not None:
            feels_like = apparent_temperature(
                data.get("temperature"), data.get("humidity"), speed
            )
            if feels_like is not None:
                data["apparent_temperature"] = feels_like
        return data

    def _take_result(
        self, source: str, result: dict[str, Any] | BaseException, now: datetime
    ) -> None:
        """Keep a source's fetched data and schedule its next fetch."""
        schedule = self.schedules[source]
        failures = self._failures[source]
        succeeded = False
        if isinstance(result, IdokepApiClientCommunicationError):
            failures.failed(result)
        elif isinstance(result, BaseException):
            # Unexpected, e.g. a parser error: with the traceback
            failures.failed(repr(result), exc_info=result)
        elif REQUIRED_KEYS[source] not in result:
            failures.failed(f"no {source} data on the page")
        else:
            succeeded = True

        if source == HOURLY:
            self._forecast_failed = not succeeded
        if not succeeded:
            # Nothing old is shown for a source that failed (for the forecast,
            # see _async_update_data)
            self._source_data[source] = {}
            schedule.mark_failure(now)
            return
        failures.succeeded()
        schedule.mark_success(now)
        self._source_data[source] = result
        if source == HOURLY:
            self._last_forecast = result
            if self._store is not None:
                self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
