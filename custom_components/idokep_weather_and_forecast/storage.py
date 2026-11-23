"""
Saving the last good data of each idokep.hu page to disk.

The coordinator keeps one file per config entry under .storage/ (Home
Assistant's Store helper). After a restart the saved data is restored, but
only what is still valid:

- forecasts: hours and days that have not passed yet
- today's max/min: only if saved today
- alerts and warnings: only if fetched within ALERTS_MAX_AGE
- the current wind, precipitation and storm value: if fetched in the hour
  now running, else from the forecast's item for that hour
- everything else (current conditions, humidity, pressure): only if fetched
  within MAX_AGE, since its age cannot be checked otherwise

The coordinator also applies the forecast rules (forecast_as_of) to the kept
forecast while the page can't be downloaded, so the current hour moves on.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .api import (
    ALERT_LEVELS,
    ALERT_TYPES,
    CURRENT_HOUR_KEYS,
    HourlyForecastParser,
    TimeUtils,
    WindStrengthMapper,
    current_hour_values,
)
from .const import (
    CURRENT,
    DOMAIN,
    HOURLY,
    HUMIDITY_MAP,
    PRESSURE_MAP,
    UV_MAP,
)

STORAGE_VERSION = 1
# Restored data older than this is only used where it can be checked by date
MAX_AGE = timedelta(hours=1)
# The hourly cards' alerts are for the 3 hours after the fetch; the banners'
# warnings are kept as long
ALERTS_MAX_AGE = timedelta(hours=3)
# The forecast page's values for the hour now running
CURRENT_KEYS = (*(key for _, key in CURRENT_HOUR_KEYS), "storm_expected")
# Delay before writing, so several page fetches end in one write
SAVE_DELAY = 10

# Values whose validity is a calendar day rather than an age
TODAY_KEYS = ("temperature_max_today", "temperature_min_today")


def storage_key(entry_id: str) -> str:
    """Return the Store key (file name under .storage/) for a config entry."""
    return f"{DOMAIN}.{entry_id}"


def station_storage_key(entry_id: str) -> str:
    """Return the Store key of a config entry's weather station readings."""
    return f"{DOMAIN}.{entry_id}.station"


def serialize(data: dict[str, Any]) -> dict[str, Any]:
    """Return page data in a JSON-safe form (a copy: it's all plain data)."""
    return dict(data)


def restore(source: str, saved: dict[str, Any], now: datetime) -> dict[str, Any]:
    """
    Return the still valid part of a page's saved data.

    saved is {"fetched": ISO time, "data": page data}; now is an aware
    datetime. An empty result means nothing worth restoring.
    """
    try:
        fetched = datetime.fromisoformat(saved["fetched"])
        data = dict(saved["data"])
    except (KeyError, TypeError, ValueError):
        return {}
    restorer = _RESTORERS.get(source)
    if restorer is None:
        return {}
    return restorer(data, fetched, now)


def _restore_current(
    data: dict[str, Any], fetched: datetime, now: datetime
) -> dict[str, Any]:
    """Restore current conditions: only if fresh."""
    return data if now - fetched <= MAX_AGE else {}


def forecast_as_of(
    data: dict[str, Any], fetched: datetime, now: datetime
) -> dict[str, Any]:
    """
    Return the forecast page's data fetched at fetched as it stands at now.

    Used both on restore and while the page can't be downloaded. Hours not
    yet over, days from today on, today's max/min if fetched today, alerts
    and warnings for ALERTS_MAX_AGE. The current wind, precipitation and
    storm value are kept within the hour they were fetched in, after that
    taken from the item of the hour now running.
    """
    result: dict[str, Any] = {}
    today = dt_util.as_local(now).date()

    if hourly := _hours_not_over(data.get("hourly_forecast", []), now):
        result["hourly_forecast"] = [_with_wind_estimates(item) for item in hourly]
    if daily := _days_from(data.get("daily_forecast", []), today):
        result["daily_forecast"] = daily

    if dt_util.as_local(fetched).date() == today:
        result.update({key: data[key] for key in TODAY_KEYS if key in data})

    if now - fetched <= ALERTS_MAX_AGE:
        for key in ("alerts", "warnings"):
            if key in data:
                result[key] = _valid_alerts(data[key])
        if "warning_level" in data:
            result["warning_level"] = data["warning_level"]

    if _hour_start(fetched) == _hour_start(now):
        for key in CURRENT_KEYS:
            if key in data:
                result[key] = data[key]
        strength = result.get("wind_strength")
        if strength in WindStrengthMapper.SPEEDS:
            result["wind_speed"] = WindStrengthMapper.SPEEDS[strength]
            result["wind_gust_speed"] = WindStrengthMapper.GUSTS[strength]
    elif hourly := result.get("hourly_forecast"):
        # Fetched for an earlier hour, but the hour now running is still in
        # the forecast: take them from its item, as a fetch would
        result.update(current_hour_values(_item_of_hour(hourly, now)))
        result["storm_expected"] = HourlyForecastParser.storm_expected(hourly, now)

    return result


def _hour_start(moment: datetime) -> datetime:
    """Return the start of moment's hour (in UTC: Budapest's offset is whole hours)."""
    return dt_util.as_utc(moment).replace(minute=0, second=0, microsecond=0)


def _item_of_hour(items: list[dict], now: datetime) -> dict | None:
    """Return the hourly item whose hour contains now (None if it is missing)."""
    for item in items:
        start = datetime.fromisoformat(item["datetime"])
        if start <= now < start + timedelta(hours=1):
            return item
    return None


def _valid_alerts(items: list[Any]) -> list[dict[str, str]]:
    """
    Return the saved alerts in the current form.

    Extra fields saved by older versions are left out, and alerts of
    unknown types (older versions' type names) are dropped.
    """
    return [
        {"type": item["type"], "level": item["level"]}
        for item in items
        if isinstance(item, dict)
        and item.get("type") in ALERT_TYPES
        and item.get("level") in ALERT_LEVELS[1:]
    ]


def _with_wind_estimates(item: dict[str, Any]) -> dict[str, Any]:
    """
    Return an hourly item with its wind speed and gust from its wind strength.

    They are only estimates from the strength, so data saved by an earlier
    version may have other estimates, or none.
    """
    strength = item.get("wind_strength")
    if strength not in WindStrengthMapper.SPEEDS:
        return item
    return {
        **item,
        "native_wind_speed": WindStrengthMapper.SPEEDS[strength],
        "native_wind_gust_speed": WindStrengthMapper.GUSTS[strength],
    }


def _hours_not_over(items: list[dict], now: datetime) -> list[dict]:
    """Return the hourly items whose hour has not ended yet."""
    result = []
    for item in items:
        try:
            start = datetime.fromisoformat(item["datetime"])
        except (KeyError, TypeError, ValueError):
            continue
        if start.tzinfo is not None and start + timedelta(hours=1) > now:
            result.append(item)
    return result


def _days_from(items: list[dict], today: date) -> list[dict]:
    """
    Return the daily items dated today or later.

    Earlier versions saved a date-only datetime and a precipitation
    probability (the page shows none per day): the date becomes local
    midnight, the probability goes.
    """
    result = []
    for item in items:
        try:
            day = date.fromisoformat(str(item["datetime"])[:10])
        except (KeyError, TypeError, ValueError):
            continue
        if day >= today:
            kept = {k: v for k, v in item.items() if k != "precipitation_probability"}
            result.append({**kept, "datetime": TimeUtils.day_start(day)})
    return result


_RESTORERS = {
    CURRENT: _restore_current,
    HUMIDITY_MAP: _restore_current,
    PRESSURE_MAP: _restore_current,
    UV_MAP: _restore_current,
    HOURLY: forecast_as_of,
}
