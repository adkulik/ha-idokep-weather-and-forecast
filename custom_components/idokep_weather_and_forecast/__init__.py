"""
Custom integration to integrate idokep with Home Assistant.

For more details about this integration, please refer to
https://github.com/ludeeus/integration_blueprint
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store

from .api import (
    IdokepApiClient,
    IdokepApiClientCommunicationError,
    IdokepApiClientUnknownLocationError,
)
from .const import (
    DOMAIN,
    ENTRY_TYPE,
    LOCATION,
    LOGGER,
    MAX_AGE,
    MERGE_METHOD,
    RADAR,
    STATION,
)
from .coordinator import (
    IdokepDataUpdateCoordinator,
    RadarCoordinator,
    StationCoordinator,
)
from .data import IdokepData, RadarData
from .failures import FailureRecords
from .station import (
    max_age_minutes,
    merge_method,
    station_codes,
    station_identifier,
)
from .storage import STORAGE_VERSION, station_storage_key, storage_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import TypeIs

    from homeassistant.core import HomeAssistant

    from .data import AnyIdokepConfigEntry, IdokepConfigEntry, RadarConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.WEATHER,
]
# The national Radar service entry only has the radar image
RADAR_PLATFORMS: list[Platform] = [Platform.IMAGE]
# What entries of version 1.1 (up to 1.13.0) may keep in data instead of options
SETTINGS = (STATION, MERGE_METHOD, MAX_AGE)


def _is_radar(entry: AnyIdokepConfigEntry) -> TypeIs[RadarConfigEntry]:
    """Return True for the Radar service entry."""
    return entry.data.get(ENTRY_TYPE) == RADAR


async def async_migrate_entry(hass: HomeAssistant, entry: AnyIdokepConfigEntry) -> bool:
    """Migrate an entry saved by an earlier version of the integration."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        await _async_rename_weather_id(hass, entry)
        _async_move_settings_to_options(hass, entry)
    return True


async def _async_rename_weather_id(
    hass: HomeAssistant, entry: AnyIdokepConfigEntry
) -> None:
    """
    Take the platform's name out of the weather entity's unique ID (1.2).

    The entity keeps its entity ID, name, settings and history.
    """
    old = f"{entry.entry_id}_weather"

    @callback
    def rename(entity: er.RegistryEntry) -> dict[str, str] | None:
        if entity.unique_id != old:
            return None
        return {"new_unique_id": f"{entry.entry_id}_forecast"}

    await er.async_migrate_entries(hass, entry.entry_id, rename)


@callback
def _async_move_settings_to_options(
    hass: HomeAssistant, entry: AnyIdokepConfigEntry
) -> None:
    """
    Move the station settings from the entry's data to its options (1.2).

    Settings already saved in the options are newer, so they are kept.
    """
    data = dict(entry.data)
    settings = {key: data.pop(key) for key in SETTINGS if key in data}
    hass.config_entries.async_update_entry(
        entry, data=data, options={**settings, **entry.options}, minor_version=2
    )


def _remove_device(
    hass: HomeAssistant, entry: IdokepConfigEntry, identifier: tuple[str, str]
) -> None:
    """Remove a device (and its entities) left from before."""
    registry = dr.async_get(hass)
    # Searched among the entry's own devices: HA 2026.7 has no lookup by
    # identifier and config entry, and newer ones deprecate the one without it
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if identifier in device.identifiers:
            registry.async_remove_device(device.id)


def _remove_old_alert_sensors(hass: HomeAssistant, entry: IdokepConfigEntry) -> None:
    """Delete the alert binary sensors that the Alert level sensor replaced."""
    registry = er.async_get(hass)
    for key in ("weather_alert", "alert_yellow", "alert_orange", "alert_red"):
        if entity_id := registry.async_get_entity_id(
            Platform.BINARY_SENSOR, DOMAIN, f"{entry.entry_id}_{key}"
        ):
            registry.async_remove(entity_id)


async def _async_require_connection(check: Callable[[], Awaitable[None]]) -> None:
    """
    Raise ConfigEntryNotReady while check can't reach idokep.hu.

    Home Assistant then reports the reason and retries the setup, less and less
    often (at most every 10 minutes), so nothing is logged here.
    """
    try:
        await check()
    except IdokepApiClientCommunicationError as error:
        raise ConfigEntryNotReady(str(error)) from error


async def _async_check_location(
    client: IdokepApiClient, location: str, *, has_forecast: bool
) -> None:
    """
    Stop the setup of a location with the wrong weather or nothing to show.

    For a name idokep.hu doesn't know (an earlier version could save one) it
    would show Budapest's weather: ConfigEntryError, so there is no retry until
    the name is corrected in Configure. If neither page's title can tell, the
    setup goes on with a saved forecast for now, or if a page is served.
    """
    try:
        await client.async_validate_location(location)
    except IdokepApiClientUnknownLocationError as error:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unknown_location",
            translation_placeholders={"location": location},
        ) from error
    except Exception:  # pylint: disable=broad-exception-caught
        # A failed download or an unreadable page; the first refresh reports it
        if not has_forecast:
            await _async_require_connection(
                partial(client.async_check_connection, location)
            )


async def _async_prepare_forecast(
    hass: HomeAssistant,
    entry: IdokepConfigEntry,
    location: str,
    failures: FailureRecords,
) -> IdokepDataUpdateCoordinator:
    """
    Return the forecast's coordinator, ready for its first refresh.

    An unknown settlement fails the setup; with neither a saved forecast for now
    nor a page to download, it is retried.
    """
    coordinator = IdokepDataUpdateCoordinator(
        hass=hass,
        logger=LOGGER,
        name=DOMAIN,
        config_entry=entry,
        records=failures,
    )
    client = IdokepApiClient(session=async_get_clientsession(hass), records=failures)
    entry.runtime_data.client = client
    entry.runtime_data.coordinator = coordinator

    # The forecast saved before the restart, to complete the first download
    await coordinator.async_restore()
    # Run at unload, and if the setup fails from here on
    entry.async_on_unload(coordinator.async_close_storage)

    await _async_check_location(
        client, location, has_forecast=coordinator.has_current_forecast
    )
    return coordinator


async def _async_remove_forecast(
    hass: HomeAssistant, entry: IdokepConfigEntry
) -> None:
    """Remove the Forecast device and the saved forecast left from before."""
    _remove_device(hass, entry, (DOMAIN, entry.entry_id))
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnyIdokepConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    if _is_radar(entry):
        return await _async_setup_radar_entry(hass, entry)

    # New for every setup; no warnings until it is clear that the setup is
    # not put off
    failures = FailureRecords()
    failures.hold()
    entry.runtime_data = IdokepData(failures=failures)

    coordinator = None
    if location := entry.data.get(LOCATION):
        coordinator = await _async_prepare_forecast(hass, entry, location, failures)
    else:
        await _async_remove_forecast(hass, entry)

    station = None
    if codes := station_codes(entry):
        station = StationCoordinator(
            hass=hass,
            logger=LOGGER,
            config_entry=entry,
            session=async_get_clientsession(hass),
            codes=codes,
            method=merge_method(entry),
            max_age=max_age_minutes(entry),
            records=failures,
        )
        entry.runtime_data.station = station
        # Without a forecast the stations are all there is to show
        if coordinator is None:
            await _async_require_connection(station.async_check_connection)
    else:
        _remove_device(hass, entry, station_identifier(entry))
    failures.release()

    # Not a first refresh, which would fail the setup for any page: what can't
    # be downloaded is unavailable and retried every minute
    if coordinator is not None:
        await coordinator.async_refresh()
    if station is not None:
        await station.async_refresh()
    _remove_old_alert_sensors(hass, entry)
    # 1.12.7 and 1.12.8 saved the station readings; the file is not used anymore
    await Store(hass, STORAGE_VERSION, station_storage_key(entry.entry_id)).async_remove()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Check the per-page schedules every minute at second 59 (fetches on
    # x:00:59, x:10:59, ...)
    if coordinator is not None:
        entry.async_on_unload(
            async_track_time_change(hass, coordinator.async_tick, second=59)
        )
    if station is not None:
        # Station: every minute at second 59 (fetches on x:00:59, x:05:59, ...)
        entry.async_on_unload(
            async_track_time_change(hass, station.async_tick, second=59)
        )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: AnyIdokepConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    platforms = RADAR_PLATFORMS if _is_radar(entry) else PLATFORMS
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        # A download that finishes after this is not logged
        entry.runtime_data.failures.closed = True
    return unloaded


async def _async_setup_radar_entry(
    hass: HomeAssistant,
    entry: RadarConfigEntry,
) -> bool:
    """Set up the national Radar service entry."""
    # New for every setup; no warnings until it is clear that the setup is
    # not put off
    failures = FailureRecords()
    failures.hold()
    coordinator = RadarCoordinator(
        hass=hass,
        logger=LOGGER,
        config_entry=entry,
        session=async_get_clientsession(hass),
        records=failures,
    )
    entry.runtime_data = RadarData(coordinator=coordinator, failures=failures)

    # Without the radar there is nothing to show: the setup is retried
    await _async_require_connection(coordinator.async_check_connection)
    failures.release()
    # Downloads all frames and builds the first animation; if that fails, it
    # is retried every minute
    await coordinator.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, RADAR_PLATFORMS)
    # Every minute at second 59: checks on x:00:59, x:05:59, ... and retries in
    # between until the new frame arrives (cheap "304" while unchanged)
    entry.async_on_unload(
        async_track_time_change(hass, coordinator.async_tick, second=59)
    )
    return True


async def async_remove_entry(
    hass: HomeAssistant,
    entry: AnyIdokepConfigEntry,
) -> None:
    """Delete the entry's saved data when the integration entry is removed."""
    # The station file only if left by 1.12.7-1.12.8 (e.g. a disabled entry)
    for key in (storage_key(entry.entry_id), station_storage_key(entry.entry_id)):
        await Store(hass, STORAGE_VERSION, key).async_remove()
