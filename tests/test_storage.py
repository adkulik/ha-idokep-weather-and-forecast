"""Tests for saving page data to disk and restoring what is still valid."""

from __future__ import annotations

import json
import zoneinfo
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.util import dt as dt_util

from custom_components.idokep_weather_and_forecast.const import (
    CURRENT,
    HOURLY,
    HUMIDITY_MAP,
    PRESSURE_MAP,
    UV_MAP,
)
from custom_components.idokep_weather_and_forecast.storage import (
    forecast_as_of,
    restore,
    serialize,
    storage_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

BUDAPEST = zoneinfo.ZoneInfo("Europe/Budapest")
NOW = datetime(2026, 10, 29, 20, 30, tzinfo=BUDAPEST)

CURRENT_DATA = {"temperature": 13, "condition": "cloudy"}
FORECAST_DATA = {
    "hourly_forecast": [
        {"datetime": "2026-10-29T19:00:00+01:00", "temperature": 10},
        {"datetime": "2026-10-29T20:00:00+01:00", "temperature": 9},
        {"datetime": "2026-10-29T21:00:00+01:00", "temperature": 8},
    ],
    "wind_bearing": 12,
    "wind_strength": "moderate_breeze",
    "wind_speed": 20,
    "wind_gust_speed": 35,
    "precipitation": 0.81,
    "precipitation_probability": 25,
    "alerts": [{"type": "wind", "level": "yellow"}],
    "warnings": [{"type": "thunderstorm", "level": "orange"}],
    "warning_level": "orange",
    "daily_forecast": [
        {"datetime": "2026-10-28T00:00:00+01:00", "temperature": 15},
        {"datetime": "2026-10-29T00:00:00+01:00", "temperature": 16},
        {"datetime": "2026-10-30T00:00:00+01:00", "temperature": 14},
    ],
    "temperature_max_today": 16,
    "temperature_min_today": 6,
}


@pytest.fixture(autouse=True)
def budapest_time_zone() -> Iterator[None]:
    """Run tests with Home Assistant set to the Budapest time zone."""
    original = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(BUDAPEST)
    yield
    dt_util.set_default_time_zone(original)


def saved(data: dict, fetched: datetime) -> dict:
    """Return page data as it is written to disk (through JSON)."""
    return json.loads(
        json.dumps({"fetched": fetched.isoformat(), "data": serialize(data)})
    )


def test_storage_key() -> None:
    """One file per config entry."""
    assert storage_key("abc123") == "idokep_weather_and_forecast.abc123"


def test_serialize_returns_a_json_safe_copy() -> None:
    """The page data is plain data; serialize copies it."""
    data = serialize(FORECAST_DATA)
    assert data == FORECAST_DATA
    assert data is not FORECAST_DATA
    json.dumps(data)  # must not raise


def test_restore_keeps_only_valid_alerts() -> None:
    """Saved alerts come back as {"type", "level"}; unknown types are dropped."""
    data = {
        "alerts": [
            # As saved by 1.12.0 and earlier: extra fields
            {"level": "yellow", "type": "wind", "description": "Szél", "icon_url": None},
            {"level": "red", "type": "storm", "description": "Vihar", "icon_url": None},
            # As saved by 1.12.1: Hungarian type names
            {"type": "kod", "level": "yellow"},
            {"type": "fog", "level": "none"},
            "not an alert",
        ],
        "alerts_by_level": {"yellow": [{"type": "wind"}], "orange": [], "red": []},
    }
    result = restore(HOURLY, saved(data, NOW - timedelta(minutes=5)), NOW)
    assert result["alerts"] == [{"type": "wind", "level": "yellow"}]
    assert "alerts_by_level" not in result


class TestRestoreCurrent:
    """Current conditions page."""

    @pytest.mark.parametrize("age", [timedelta(minutes=40), timedelta(days=2)])
    def test_restores_everything_however_old(self, age: timedelta) -> None:
        """The values come back until the next successful fetch replaces them."""
        item = saved(CURRENT_DATA, NOW - age)
        assert restore(CURRENT, item, NOW) == CURRENT_DATA


class TestRestoreForecast:
    """Forecast page: hourly and daily forecast, alerts, wind, today's max/min."""

    def test_fresh_keeps_everything_still_valid(self) -> None:
        """19:00 is over at 20:30; 20:00 still runs; yesterday's day is dropped."""
        item = saved(FORECAST_DATA, NOW - timedelta(minutes=30))
        result = restore(HOURLY, item, NOW)
        assert [h["datetime"][11:16] for h in result["hourly_forecast"]] == [
            "20:00",
            "21:00",
        ]
        assert [d["datetime"] for d in result["daily_forecast"]] == [
            "2026-10-29T00:00:00+01:00",
            "2026-10-30T00:00:00+01:00",
        ]
        assert result["wind_bearing"] == 12
        assert result["wind_strength"] == "moderate_breeze"
        assert result["wind_speed"] == 20
        assert result["wind_gust_speed"] == 35
        assert result["precipitation"] == 0.81
        assert result["precipitation_probability"] == 25
        assert result["alerts"] == FORECAST_DATA["alerts"]
        assert result["warnings"] == FORECAST_DATA["warnings"]
        assert result["warning_level"] == "orange"
        assert result["temperature_max_today"] == 16
        assert result["temperature_min_today"] == 6

    def test_old_same_day_keeps_forecasts_and_todays_values(self) -> None:
        """
        Alerts and warnings are kept however old.

        The current hour's wind and precipitation would come from its item;
        this one has none, and no storm.
        """
        item = saved(FORECAST_DATA, NOW - timedelta(hours=4))
        assert restore(HOURLY, item, NOW) == {
            "hourly_forecast": FORECAST_DATA["hourly_forecast"][1:],
            "daily_forecast": FORECAST_DATA["daily_forecast"][1:],
            "temperature_max_today": 16,
            "temperature_min_today": 6,
            "alerts": FORECAST_DATA["alerts"],
            "warnings": FORECAST_DATA["warnings"],
            "warning_level": "orange",
            "storm_expected": False,
        }

    def test_next_day_drops_todays_values(self) -> None:
        """Yesterday's max/min are not today's; past hours and days go too."""
        item = saved(FORECAST_DATA, NOW - timedelta(hours=3))
        tomorrow = NOW + timedelta(hours=5)  # 01:30 on the 30th
        assert restore(HOURLY, item, tomorrow) == {
            "daily_forecast": FORECAST_DATA["daily_forecast"][2:],
            "alerts": FORECAST_DATA["alerts"],
            "warnings": FORECAST_DATA["warnings"],
            "warning_level": "orange",
        }

    def test_all_past_keeps_only_the_alerts(self) -> None:
        """Of a forecast that is completely over, only the alerts and warnings stay."""
        item = saved(FORECAST_DATA, NOW - timedelta(days=2))
        assert restore(HOURLY, item, NOW + timedelta(days=2)) == {
            "alerts": FORECAST_DATA["alerts"],
            "warnings": FORECAST_DATA["warnings"],
            "warning_level": "orange",
        }

    def test_invalid_daily_dates_dropped(self) -> None:
        """Daily entries without a real date are not restored."""
        data = {"daily_forecast": [{"datetime": "y"}, {"datetime": "2026-10-30T00:00:00+01:00"}]}
        assert restore(HOURLY, saved(data, NOW - timedelta(hours=3)), NOW) == {
            "daily_forecast": [{"datetime": "2026-10-30T00:00:00+01:00"}]
        }

    def test_old_daily_items_updated(self) -> None:
        """
        A daily item saved by an earlier version is brought up to date.

        Its date-only datetime becomes local midnight, its precipitation
        probability goes.
        """
        data = {
            "daily_forecast": [
                {"datetime": "2026-10-30", "precipitation": 3, "precipitation_probability": 70}
            ]
        }
        assert restore(HOURLY, saved(data, NOW - timedelta(hours=3)), NOW) == {
            "daily_forecast": [{"datetime": "2026-10-30T00:00:00+01:00", "precipitation": 3}]
        }


@pytest.mark.parametrize(
    "item",
    [{}, {"fetched": "not a date", "data": {}}, {"fetched": NOW.isoformat()}],
)
def test_broken_saved_data_restores_nothing(item: dict) -> None:
    """A damaged file never breaks setup."""
    assert restore(CURRENT, item, NOW) == {}


def test_unknown_source_restores_nothing() -> None:
    """Data for a page that is no longer used (e.g. the old "daily") is ignored."""
    assert restore("daily", saved({"daily_forecast": []}, NOW), NOW) == {}


def test_restore_skips_hourly_items_without_a_valid_time() -> None:
    """Hourly items without a time, or with a broken one, are dropped."""
    valid = {"datetime": "2026-10-29T20:00:00+01:00", "temperature": 10}
    saved = {
        "fetched": (NOW - timedelta(minutes=30)).isoformat(),
        "data": {
            "hourly_forecast": [{"temperature": 1}, {"datetime": "broken"}, valid],
        },
    }
    assert restore(HOURLY, saved, NOW)["hourly_forecast"] == [valid]


@pytest.mark.parametrize(
    ("source", "data"),
    [
        (HUMIDITY_MAP, {"humidity": 59.0, "dew_point": 2.0}),
        (PRESSURE_MAP, {"pressure": 1029.2}),
        (UV_MAP, {"uv_index": 0.3}),
    ],
)
def test_restore_maps_however_old(source: str, data: dict) -> None:
    """The values from the maps come back, however old."""
    fresh = {"fetched": (NOW - timedelta(minutes=30)).isoformat(), "data": data}
    old = {"fetched": (NOW - timedelta(days=2)).isoformat(), "data": data}
    assert restore(source, fresh, NOW) == data
    assert restore(source, old, NOW) == data


def test_restore_storm_value_only_if_fresh() -> None:
    """The storm value is kept for an hour; without the hourly items it's gone."""
    data = {"storm_expected": True}
    fresh = {"fetched": (NOW - timedelta(minutes=30)).isoformat(), "data": data}
    old = {"fetched": (NOW - timedelta(minutes=90)).isoformat(), "data": data}
    assert restore(HOURLY, fresh, NOW)["storm_expected"] is True
    assert "storm_expected" not in restore(HOURLY, old, NOW)


def _saved_by_an_earlier_version() -> dict:
    """Return forecast data with the old midpoint speed and no gust."""
    return {
        "fetched": (NOW - timedelta(minutes=10)).isoformat(),
        "data": {
            "wind_strength": "gentle_breeze",
            "wind_speed": 16,
            "hourly_forecast": [
                {
                    "datetime": "2026-10-29T20:00:00+01:00",
                    "wind_strength": "gentle_breeze",
                    "native_wind_speed": 16,
                },
                {"datetime": "2026-10-29T21:00:00+01:00", "native_wind_speed": 9},
            ],
        },
    }


def test_restore_recalculates_the_wind_estimates() -> None:
    """Saved estimates are replaced by the current ones from the wind strength."""
    result = restore(HOURLY, _saved_by_an_earlier_version(), NOW)

    assert result["wind_speed"] == 12
    assert result["wind_gust_speed"] == 21
    assert result["hourly_forecast"][0]["native_wind_speed"] == 12
    assert result["hourly_forecast"][0]["native_wind_gust_speed"] == 21


def test_restore_keeps_items_without_a_known_wind_strength() -> None:
    """Without a known strength there is nothing to recalculate from."""
    saved = _saved_by_an_earlier_version()
    saved["data"]["wind_strength"] = "unknown_word"

    result = restore(HOURLY, saved, NOW)

    assert result["wind_speed"] == 16
    assert "wind_gust_speed" not in result
    assert result["hourly_forecast"][1] == {
        "datetime": "2026-10-29T21:00:00+01:00",
        "native_wind_speed": 9,
    }


def test_restore_after_an_hour_takes_the_current_hour_from_its_item() -> None:
    """
    Once old, the saved current values are not used.

    They are taken from the forecast's item for the hour now running (20:00 at
    20:30), as a fetch would.
    """
    saved = {
        "fetched": (NOW - timedelta(minutes=90)).isoformat(),
        "data": {
            "wind_strength": "fresh_breeze",
            "wind_bearing": 90,
            "precipitation": 2.5,
            "storm_expected": True,
            "hourly_forecast": [
                {
                    "datetime": "2026-10-29T20:00:00+01:00",
                    "wind_strength": "gentle_breeze",
                    "wind_bearing": 180,
                    "precipitation": 0.2,
                    "precipitation_probability": 40,
                },
                {"datetime": "2026-10-29T21:00:00+01:00", "storm": True},
            ],
        },
    }

    result = restore(HOURLY, saved, NOW)

    assert result["wind_strength"] == "gentle_breeze"
    assert result["wind_bearing"] == 180
    assert result["wind_speed"] == 12
    assert result["wind_gust_speed"] == 21
    assert result["precipitation"] == 0.2
    assert result["precipitation_probability"] == 40
    # The next hour is stormy
    assert result["storm_expected"] is True


def test_restore_within_the_hour_of_the_fetch_keeps_the_current_values() -> None:
    """Fetched at 20:05, restored at 20:55: still the same hour."""
    data = {
        "wind_bearing": 90,
        "precipitation": 2.5,
        "hourly_forecast": [
            {"datetime": "2026-10-29T20:00:00+01:00", "wind_bearing": 180},
        ],
    }
    fetched = NOW.replace(minute=5)
    result = forecast_as_of(data, fetched, NOW.replace(minute=55))
    assert result["wind_bearing"] == 90
    assert result["precipitation"] == 2.5


def test_restore_in_the_next_hour_moves_the_current_values_on() -> None:
    """Fetched at 19:55, only 35 minutes ago at 20:30, but for the 19:00 hour."""
    data = {
        "wind_bearing": 90,
        "precipitation": 2.5,
        "storm_expected": False,
        "hourly_forecast": [
            {"datetime": "2026-10-29T19:00:00+01:00", "wind_bearing": 90},
            {"datetime": "2026-10-29T20:00:00+01:00", "wind_bearing": 180},
            {"datetime": "2026-10-29T21:00:00+01:00", "storm": True},
        ],
    }
    result = forecast_as_of(data, NOW - timedelta(minutes=35), NOW)
    assert result["wind_bearing"] == 180
    assert "precipitation" not in result
    assert result["storm_expected"] is True
    assert [h["datetime"][11:16] for h in result["hourly_forecast"]] == [
        "20:00",
        "21:00",
    ]


def test_forecast_as_of_is_repeatable() -> None:
    """Applied again to its own result (as on every update), nothing changes."""
    item = saved(FORECAST_DATA, NOW - timedelta(minutes=90))
    once = restore(HOURLY, item, NOW)
    fetched = datetime.fromisoformat(item["fetched"])
    assert forecast_as_of(once, fetched, NOW) == once


def test_restore_after_an_hour_without_the_current_hour() -> None:
    """Without an item for the hour now running there are no current values."""
    saved = {
        "fetched": (NOW - timedelta(minutes=90)).isoformat(),
        "data": {
            "wind_strength": "fresh_breeze",
            "hourly_forecast": [
                {"datetime": "2026-10-29T21:00:00+01:00", "wind_strength": "calm"},
            ],
        },
    }

    result = restore(HOURLY, saved, NOW)

    assert "wind_strength" not in result
    assert "wind_speed" not in result
    assert result["storm_expected"] is False


def test_restore_alerts_and_warnings_however_old() -> None:
    """Alerts and warnings stay until the next successful fetch replaces them."""
    data = {
        "alerts": [{"type": "wind", "level": "yellow"}],
        "warnings": [{"type": "fog", "level": "yellow"}],
        "warning_level": "yellow",
    }
    old = {"fetched": (NOW - timedelta(days=2)).isoformat(), "data": data}
    assert restore(HOURLY, old, NOW) == data
