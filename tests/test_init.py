"""Unit tests for the Időkép integration __init__.py."""

# Tests reach into the integration's private helpers
# pylint: disable=protected-access

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from custom_components.idokep_weather_and_forecast import (
    PLATFORMS,
    async_migrate_entry,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
    binary_sensor,
    sensor,
    weather,
)
from custom_components.idokep_weather_and_forecast.api import (
    IdokepApiClientCommunicationError,
    IdokepApiClientUnknownLocationError,
)
from custom_components.idokep_weather_and_forecast.const import DOMAIN, ENTRY_TYPE, RADAR
from custom_components.idokep_weather_and_forecast.data import IdokepData
from custom_components.idokep_weather_and_forecast.failures import FailureRecords

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(autouse=True)
def entity_registry() -> Mock:
    """Patch the entity registry (no old alert sensor left by default)."""
    with patch(
        "custom_components.idokep_weather_and_forecast.er.async_get"
    ) as mock_er_get:
        registry = mock_er_get.return_value
        registry.async_get_entity_id.return_value = None
        yield registry


@pytest.fixture(autouse=True)
def store_class() -> Mock:
    """Patch Store: setup removes the station file left by 1.12.7-1.12.8."""
    with patch("custom_components.idokep_weather_and_forecast.Store") as mock_store:
        mock_store.return_value.async_remove = AsyncMock()
        yield mock_store


class TestIdokepInit:
    """Test cases for Időkép integration initialization."""

    def test_platforms_constant(self) -> None:
        """Test that PLATFORMS constant is correctly defined."""
        expected_platforms = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.WEATHER]
        assert expected_platforms == PLATFORMS

    @pytest.mark.asyncio
    async def test_async_setup_entry_success(self) -> None:
        """Test successful setup of config entry."""
        # Mock dependencies
        mock_hass = Mock(spec=HomeAssistant)
        mock_entry = Mock(spec=ConfigEntry)
        mock_entry.domain = DOMAIN
        mock_entry.entry_id = "test_entry_id"
        mock_entry.data = {"location": "Budapest"}
        mock_entry.options = {}

        # Mock runtime data assignment
        mock_entry.runtime_data = None
        mock_entry.async_on_unload = Mock()

        # Mock coordinator
        mock_coordinator = AsyncMock()
        mock_coordinator.async_refresh = AsyncMock()

        # Mock API client
        mock_client = Mock(async_validate_location=AsyncMock())

        with (
            patch(
                "custom_components.idokep_weather_and_forecast.IdokepDataUpdateCoordinator"
            ) as mock_coordinator_class,
            patch(
                "custom_components.idokep_weather_and_forecast.async_get_clientsession"
            ) as mock_get_session,
            patch(
                "custom_components.idokep_weather_and_forecast.IdokepApiClient"
            ) as mock_api_client_class,
            patch(
                "custom_components.idokep_weather_and_forecast.async_track_time_change",
                return_value="unsub_timer",
            ) as mock_track_time,
            patch("custom_components.idokep_weather_and_forecast.dr.async_get"),
            # No station device left from before
            patch(
                "custom_components.idokep_weather_and_forecast.dr."
                "async_entries_for_config_entry",
                return_value=[],
            ),
        ):
            # Configure mocks
            mock_coordinator_class.return_value = mock_coordinator
            mock_get_session.return_value = "session"
            mock_api_client_class.return_value = mock_client

            # Mock forward_entry_setups
            mock_hass.config_entries.async_forward_entry_setups = AsyncMock(
                return_value=True
            )

            # Call the function
            result = await async_setup_entry(mock_hass, mock_entry)

            # Assertions
            assert result is True
            mock_coordinator_class.assert_called_once()
            # One set of records for the coordinator, the client and the entry
            assert mock_entry.runtime_data.coordinator is mock_coordinator
            assert mock_entry.runtime_data.client is mock_client
            records = mock_entry.runtime_data.failures
            assert mock_coordinator_class.call_args.kwargs["records"] is records
            assert mock_api_client_class.call_args.kwargs["records"] is records
            assert records.waiting is None
            # Not a first refresh, which would fail the setup
            mock_coordinator.async_refresh.assert_awaited_once()
            mock_coordinator.async_config_entry_first_refresh.assert_not_called()
            # Only the setup form adds the Radar entry
            mock_hass.config_entries.flow.async_init.assert_not_called()
            # The saved forecast is read before the first fetch
            call_names = [c[0] for c in mock_coordinator.mock_calls]
            assert call_names.index("async_restore") < call_names.index(
                "async_refresh"
            )
            mock_hass.config_entries.async_forward_entry_setups.assert_called_once_with(
                mock_entry, PLATFORMS
            )
            # Schedules are checked every minute at second 59, stopped on unload
            mock_track_time.assert_called_once_with(
                mock_hass, mock_coordinator.async_tick, second=59
            )
            # A scheduled save is written at unload
            assert mock_entry.async_on_unload.call_args_list == [
                call(mock_coordinator.async_close_storage),
                call("unsub_timer"),
            ]

    @staticmethod
    def _location_entry(options: dict) -> tuple[Mock, Mock]:
        """Return (hass, entry) mocks for a location entry's setup."""
        mock_hass = Mock(spec=HomeAssistant)
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
        mock_entry = Mock(spec=ConfigEntry)
        mock_entry.domain = DOMAIN
        mock_entry.entry_id = "entry1"
        mock_entry.data = {"location": "Debrecen-Józsa"}
        mock_entry.options = options
        mock_entry.async_on_unload = Mock()
        return mock_hass, mock_entry

    @pytest.mark.asyncio
    async def test_async_setup_entry_with_station(self, store_class: Mock) -> None:
        """A station code adds the station coordinator and its timer."""
        mock_hass, mock_entry = self._location_entry({"station": ["jozsamet"]})
        station = Mock()
        station.async_refresh = AsyncMock()

        with (
            patch(
                "custom_components.idokep_weather_and_forecast.IdokepDataUpdateCoordinator",
                return_value=AsyncMock(),
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.async_get_clientsession"
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.IdokepApiClient",
                return_value=Mock(async_validate_location=AsyncMock()),
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.StationCoordinator",
                return_value=station,
            ) as mock_station_class,
            patch(
                "custom_components.idokep_weather_and_forecast.async_track_time_change"
            ) as mock_track_time,
        ):
            assert await async_setup_entry(mock_hass, mock_entry)

        assert mock_station_class.call_args.kwargs["codes"] == ["jozsamet"]
        assert mock_station_class.call_args.kwargs["method"] == "priority"
        assert mock_station_class.call_args.kwargs["max_age"] == 60
        assert mock_entry.runtime_data.station is station
        assert (
            mock_station_class.call_args.kwargs["records"]
            is mock_entry.runtime_data.failures
        )
        # A failed station fetch must not fail the entry: no first refresh
        station.async_refresh.assert_awaited_once()
        # The station readings saved by 1.12.7-1.12.8 are removed
        store_class.assert_called_once_with(
            mock_hass, 1, "idokep_weather_and_forecast.entry1.station"
        )
        store_class.return_value.async_remove.assert_awaited_once()
        mock_track_time.assert_any_call(mock_hass, station.async_tick, second=59)

    @pytest.mark.asyncio
    async def test_async_setup_entry_deletes_old_alert_sensors(
        self, entity_registry: Mock
    ) -> None:
        """The binary sensors replaced by Alert level are deleted on upgrade."""
        mock_hass, mock_entry = self._location_entry({"station": ""})
        entity_registry.async_get_entity_id.side_effect = lambda _p, _d, unique_id: (
            f"binary_sensor.{unique_id}" if "alert" in unique_id else None
        )

        with (
            patch(
                "custom_components.idokep_weather_and_forecast.IdokepDataUpdateCoordinator",
                return_value=AsyncMock(),
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.async_get_clientsession"
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.IdokepApiClient",
                return_value=Mock(async_validate_location=AsyncMock()),
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.async_track_time_change"
            ),
            patch("custom_components.idokep_weather_and_forecast.dr.async_get"),
        ):
            assert await async_setup_entry(mock_hass, mock_entry)

        removed = {c.args[0] for c in entity_registry.async_remove.call_args_list}
        assert removed == {
            "binary_sensor.entry1_weather_alert",
            "binary_sensor.entry1_alert_yellow",
            "binary_sensor.entry1_alert_orange",
            "binary_sensor.entry1_alert_red",
        }

    @pytest.mark.asyncio
    async def test_async_setup_entry_station_removed_in_options(self) -> None:
        """An emptied station code removes the station device."""
        mock_hass, mock_entry = self._location_entry({"station": ""})

        with (
            patch(
                "custom_components.idokep_weather_and_forecast.IdokepDataUpdateCoordinator",
                return_value=AsyncMock(),
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.async_get_clientsession"
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.IdokepApiClient",
                return_value=Mock(async_validate_location=AsyncMock()),
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.StationCoordinator"
            ) as mock_station_class,
            patch(
                "custom_components.idokep_weather_and_forecast.async_track_time_change"
            ),
            patch(
                "custom_components.idokep_weather_and_forecast.dr.async_get"
            ) as mock_dr_get,
            patch(
                "custom_components.idokep_weather_and_forecast.dr."
                "async_entries_for_config_entry",
                return_value=[
                    Mock(id="forecast", identifiers={(DOMAIN, "entry1")}),
                    Mock(id="station", identifiers={(DOMAIN, "entry1_station")}),
                ],
            ) as mock_entry_devices,
        ):
            registry = mock_dr_get.return_value
            assert await async_setup_entry(mock_hass, mock_entry)

        mock_station_class.assert_not_called()
        assert mock_entry.runtime_data.station is None
        mock_entry_devices.assert_called_once_with(registry, "entry1")
        # Only the station device goes
        registry.async_remove_device.assert_called_once_with("station")

    @pytest.mark.asyncio
    async def test_async_unload_entry_success(self) -> None:
        """Test successful unloading of config entry."""
        # Mock dependencies
        mock_hass = Mock(spec=HomeAssistant)
        mock_entry = Mock(spec=ConfigEntry)
        mock_entry.data = {"location": "Budapest"}
        mock_entry.runtime_data = Mock(failures=FailureRecords())

        # Mock unload_platforms
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        # Call the function
        result = await async_unload_entry(mock_hass, mock_entry)

        # Assertions
        assert result is True
        mock_hass.config_entries.async_unload_platforms.assert_called_once_with(
            mock_entry, PLATFORMS
        )
        # A download that finishes later is not logged
        assert mock_entry.runtime_data.failures.closed

    @pytest.mark.asyncio
    async def test_async_unload_entry_failure(self) -> None:
        """Test unloading failure."""
        # Mock dependencies
        mock_hass = Mock(spec=HomeAssistant)
        mock_entry = Mock(spec=ConfigEntry)
        mock_entry.data = {"location": "Budapest"}
        mock_entry.runtime_data = Mock(failures=FailureRecords())

        # Mock unload_platforms to return False
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        # Call the function
        result = await async_unload_entry(mock_hass, mock_entry)

        # Assertions
        assert result is False
        # The entry goes on, and so does its logging
        assert not mock_entry.runtime_data.failures.closed

    @pytest.mark.asyncio
    async def test_async_remove_entry_deletes_saved_data(self) -> None:
        """Removing the entry deletes its files under .storage/."""
        mock_hass = Mock(spec=HomeAssistant)
        mock_entry = Mock(spec=ConfigEntry)
        mock_entry.entry_id = "entry1"

        with patch(
            "custom_components.idokep_weather_and_forecast.Store"
        ) as mock_store_class:
            mock_store_class.return_value.async_remove = AsyncMock()
            await async_remove_entry(mock_hass, mock_entry)

        assert [c.args for c in mock_store_class.call_args_list] == [
            (mock_hass, 1, "idokep_weather_and_forecast.entry1"),
            (mock_hass, 1, "idokep_weather_and_forecast.entry1.station"),
        ]
        assert mock_store_class.return_value.async_remove.await_count == 2


# ---------------------------------------------------------------------------
# The settlement's check: the wrong weather or nothing to show
# ---------------------------------------------------------------------------

DOWN = IdokepApiClientCommunicationError("Error fetching the page")
UNKNOWN = IdokepApiClientUnknownLocationError("idokep.hu served Budapest for Egr")


def location_parts(*, saved: bool, validate: AsyncMock, check: AsyncMock) -> tuple:
    """Return (coordinator, patches) for a location entry's setup."""
    coordinator = AsyncMock()
    coordinator.has_current_forecast = saved
    client = Mock(async_validate_location=validate, async_check_connection=check)
    package = "custom_components.idokep_weather_and_forecast"
    return coordinator, (
        patch(f"{package}.IdokepDataUpdateCoordinator", return_value=coordinator),
        patch(f"{package}.IdokepApiClient", return_value=client),
        patch(f"{package}.async_get_clientsession"),
        patch(f"{package}.async_track_time_change"),
        patch(f"{package}.dr.async_get"),
    )


async def set_up(hass: Mock, entry: Mock, patches: tuple) -> bool:
    """Run the setup with the patches."""
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        return await async_setup_entry(hass, entry)


async def test_setup_is_retried_while_there_is_nothing_to_show(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Home Assistant gets the reason; the integration logs nothing itself."""
    hass, entry = TestIdokepInit._location_entry({})
    entry.data = {"location": "Eger"}
    validate = AsyncMock(side_effect=DOWN)
    check = AsyncMock(side_effect=DOWN)
    coordinator, patches = location_parts(saved=False, validate=validate, check=check)

    for _ in range(3):
        with pytest.raises(ConfigEntryNotReady, match="Error fetching the page"):
            await set_up(hass, entry, patches)
        coordinator, patches = location_parts(
            saved=False, validate=validate, check=check
        )

    coordinator.async_refresh.assert_not_awaited()
    hass.config_entries.async_forward_entry_setups.assert_not_awaited()
    check.assert_awaited_with("Eger")

    validate.side_effect = check.side_effect = None
    assert await set_up(hass, entry, patches)

    coordinator.async_refresh.assert_awaited_once()
    assert not caplog.records


@pytest.mark.parametrize("saved", [True, False])
async def test_setup_of_an_unknown_settlement_fails(
    saved: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """Not retried, and a saved forecast doesn't help: it is another town's."""
    hass, entry = TestIdokepInit._location_entry({})
    entry.data = {"location": "Egr"}
    validate = AsyncMock(side_effect=UNKNOWN)
    check = AsyncMock()
    coordinator, patches = location_parts(saved=saved, validate=validate, check=check)

    with pytest.raises(ConfigEntryError) as raised:
        await set_up(hass, entry, patches)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "unknown_location"
    assert raised.value.translation_placeholders == {"location": "Egr"}
    validate.assert_awaited_once_with("Egr")
    check.assert_not_awaited()
    coordinator.async_refresh.assert_not_awaited()
    hass.config_entries.async_forward_entry_setups.assert_not_awaited()
    assert not caplog.records


async def test_setup_of_a_known_settlement_needs_no_other_check() -> None:
    """The current page names the settlement: the setup goes on."""
    hass, entry = TestIdokepInit._location_entry({})
    entry.data = {"location": "Eger"}
    validate = AsyncMock(return_value="Eger")
    check = AsyncMock()
    coordinator, patches = location_parts(saved=False, validate=validate, check=check)

    assert await set_up(hass, entry, patches)

    validate.assert_awaited_once_with("Eger")
    check.assert_not_awaited()
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.parametrize(
    "error", [DOWN, RuntimeError("bug")], ids=["no-download", "unreadable"]
)
@pytest.mark.parametrize("saved", [True, False])
async def test_setup_goes_on_while_the_current_page_cannot_tell(
    saved: bool, error: Exception
) -> None:
    """With a saved forecast right away, else when a page is served."""
    hass, entry = TestIdokepInit._location_entry({})
    entry.data = {"location": "Eger"}
    validate = AsyncMock(side_effect=error)
    check = AsyncMock()
    coordinator, patches = location_parts(saved=saved, validate=validate, check=check)

    assert await set_up(hass, entry, patches)

    assert check.await_count == (0 if saved else 1)
    coordinator.async_refresh.assert_awaited_once()


def station_only(check: AsyncMock) -> tuple[Mock, Mock, Mock, tuple]:
    """Return (hass, entry, station, patches) for an entry without a forecast."""
    hass, entry = TestIdokepInit._location_entry({"station": ["jozsamet"]})
    entry.data = {}
    station = Mock(async_check_connection=check, async_refresh=AsyncMock())
    package = "custom_components.idokep_weather_and_forecast"
    return (
        hass,
        entry,
        station,
        (
            patch(f"{package}.StationCoordinator", return_value=station),
            patch(f"{package}.IdokepDataUpdateCoordinator"),
            patch(f"{package}.async_get_clientsession"),
            patch(f"{package}.async_track_time_change", return_value="unsub"),
            patch(f"{package}.dr.async_get"),
        ),
    )


async def test_setup_without_a_forecast(store_class: Mock) -> None:
    """Only the stations: what is left of a forecast is removed."""
    hass, entry, station, patches = station_only(AsyncMock())
    devices = [
        Mock(id="forecast", identifiers={(DOMAIN, "entry1")}),
        Mock(id="station", identifiers={(DOMAIN, "entry1_station")}),
    ]

    with (
        patches[0],
        patches[2],
        patch(
            "custom_components.idokep_weather_and_forecast.dr."
            "async_entries_for_config_entry",
            return_value=devices,
        ),
        patches[1] as forecast,
        patches[3] as track,
        patches[4] as registry,
    ):
        assert await async_setup_entry(hass, entry)

    forecast.assert_not_called()
    assert entry.runtime_data.coordinator is None
    assert entry.runtime_data.client is None
    assert entry.runtime_data.station is station
    station.async_check_connection.assert_awaited_once()
    station.async_refresh.assert_awaited_once()
    assert entry.runtime_data.failures.waiting is None
    registry.return_value.async_remove_device.assert_called_once_with("forecast")
    # The saved forecast, then the station file of 1.12.7-1.12.8
    assert [c.args[2] for c in store_class.call_args_list] == [
        "idokep_weather_and_forecast.entry1",
        "idokep_weather_and_forecast.entry1.station",
    ]
    assert store_class.return_value.async_remove.await_count == 2
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, PLATFORMS
    )
    track.assert_called_once_with(hass, station.async_tick, second=59)
    entry.async_on_unload.assert_called_once_with("unsub")


async def test_setup_without_a_forecast_is_retried_while_stations_are_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing to show: Home Assistant gets the reason and tries again."""
    hass, entry, station, patches = station_only(AsyncMock(side_effect=DOWN))

    with patch(
        "custom_components.idokep_weather_and_forecast.dr."
        "async_entries_for_config_entry",
        return_value=[],
    ):
        with pytest.raises(ConfigEntryNotReady, match="Error fetching the page"):
            await set_up(hass, entry, patches)

    station.async_refresh.assert_not_awaited()
    hass.config_entries.async_forward_entry_setups.assert_not_awaited()
    assert not caplog.records


@pytest.mark.parametrize("platform", [binary_sensor, sensor, weather])
async def test_no_forecast_entities_without_a_forecast(platform: ModuleType) -> None:
    """The platforms add nothing for the forecast."""
    entry = Mock(runtime_data=IdokepData())
    add = Mock()

    await platform.async_setup_entry(Mock(), entry, add)

    add.assert_not_called()


@pytest.mark.parametrize("language", ["en", "hu"])
def test_the_setup_error_is_translated(language: str) -> None:
    """The message names the settlement saved in the entry."""
    path = (
        Path(__file__).parent.parent
        / "custom_components"
        / DOMAIN
        / "translations"
        / f"{language}.json"
    )
    exceptions = json.loads(path.read_text(encoding="utf-8"))["exceptions"]

    assert "{location}" in exceptions["unknown_location"]["message"]


async def test_radar_setup_is_retried_while_it_cannot_download() -> None:
    """The Radar entry has nothing saved, so it needs the download."""
    hass, entry = TestIdokepInit._location_entry({})
    entry.data = {ENTRY_TYPE: RADAR}
    radar = AsyncMock()
    radar.async_check_connection.side_effect = DOWN

    with (
        patch("custom_components.idokep_weather_and_forecast.RadarCoordinator", return_value=radar),
        patch("custom_components.idokep_weather_and_forecast.async_get_clientsession"),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)

    radar.async_refresh.assert_not_awaited()
    hass.config_entries.async_forward_entry_setups.assert_not_awaited()


@pytest.mark.parametrize("radar", [False, True], ids=["location", "radar"])
async def test_warnings_wait_until_the_setup_goes_on(
    radar: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """A put-off setup logs nothing; the one that goes on logs its warnings."""
    hass, entry = TestIdokepInit._location_entry({})
    entry.data = {ENTRY_TYPE: RADAR} if radar else {"location": "Eger"}
    check = AsyncMock(side_effect=DOWN)
    coordinator, patches = location_parts(saved=False, validate=check, check=check)
    coordinator.async_check_connection = check
    kept: list[FailureRecords] = []

    def create(**kwargs: object) -> AsyncMock:
        records = kwargs["records"]
        kept.append(records)
        # Comes up before it is clear whether the setup goes on
        records.warn_once(logging.getLogger(__name__), "Something odd")
        return coordinator

    name = "RadarCoordinator" if radar else "IdokepDataUpdateCoordinator"
    with (
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(f"custom_components.idokep_weather_and_forecast.{name}", side_effect=create),
    ):
        for _ in range(2):
            with pytest.raises(ConfigEntryNotReady):
                await async_setup_entry(hass, entry)
        assert not caplog.records

        check.side_effect = None
        assert await async_setup_entry(hass, entry)

    assert [r.getMessage() for r in caplog.records] == ["Something odd"]
    # Every setup starts anew, and its entry keeps the records
    assert len({id(records) for records in kept}) == 3
    assert entry.runtime_data.failures is kept[-1]


# ---------------------------------------------------------------------------
# Entries saved by earlier versions
# ---------------------------------------------------------------------------

SETTINGS = {"station": "jozsamet", "merge_method": "average", "max_age": 90}


@pytest.mark.parametrize(
    ("data", "options", "new_data", "new_options"),
    [
        pytest.param(
            {"location": "Eger", **SETTINGS},
            {},
            {"location": "Eger"},
            SETTINGS,
            id="never-configured",
        ),
        pytest.param(
            {"location": "Eger", **SETTINGS},
            {"station": [], "max_age": 30},
            {"location": "Eger"},
            {"station": [], "merge_method": "average", "max_age": 30},
            id="configure-is-newer",
        ),
        pytest.param(
            {"location": "Eger"}, {}, {"location": "Eger"}, {}, id="no-station"
        ),
        pytest.param({ENTRY_TYPE: RADAR}, {}, {ENTRY_TYPE: RADAR}, {}, id="radar"),
    ],
)
async def test_migration_moves_the_settings_to_the_options(
    data: dict, options: dict, new_data: dict, new_options: dict
) -> None:
    """The station settings become options; those saved there already win."""
    hass = Mock()
    entry = Mock(
        version=1, minor_version=1, entry_id="entry1", data=data, options=options
    )

    with patch(
        "custom_components.idokep_weather_and_forecast.er.async_migrate_entries"
    ):
        assert await async_migrate_entry(hass, entry)

    hass.config_entries.async_update_entry.assert_called_once_with(
        entry, data=new_data, options=new_options, minor_version=2
    )


@pytest.mark.parametrize(
    ("unique_id", "expected"),
    [
        ("entry1_weather", {"new_unique_id": "entry1_forecast"}),
        ("entry1_temperature", None),
        ("entry1_station_weather", None),
    ],
)
async def test_migration_renames_the_weather_unique_id(
    unique_id: str, expected: dict | None
) -> None:
    """Only the weather entity's unique ID changes."""
    entry = Mock(version=1, minor_version=1, entry_id="entry1", data={}, options={})

    with patch(
        "custom_components.idokep_weather_and_forecast.er.async_migrate_entries"
    ) as migrate:
        await async_migrate_entry(Mock(), entry)

    rename = migrate.await_args.args[2]
    assert rename(Mock(unique_id=unique_id)) == expected


@pytest.mark.parametrize(
    ("version", "minor_version", "migrated"), [(1, 2, True), (2, 1, False)]
)
async def test_nothing_to_migrate(
    version: int, minor_version: int, migrated: bool
) -> None:
    """A current entry is left alone; one of a newer version is refused."""
    hass = Mock()
    entry = Mock(version=version, minor_version=minor_version)

    assert await async_migrate_entry(hass, entry) is migrated

    hass.config_entries.async_update_entry.assert_not_called()
