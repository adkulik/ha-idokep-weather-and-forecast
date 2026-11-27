"""Tests for logging a failing download once."""

# The tests reach into the coordinators' logs
# pylint: disable=protected-access

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from custom_components.idokep_weather_and_forecast.const import CURRENT
from custom_components.idokep_weather_and_forecast.coordinator import (
    IdokepDataUpdateCoordinator,
    RadarCoordinator,
    StationCoordinator,
)
from custom_components.idokep_weather_and_forecast.failures import (
    FailureLog,
    FailureRecords,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger("idokep_test")
_ENTRY = Mock(entry_id="entry1", data={"location": "Eger"})


def test_logs_of_a_name_share_the_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A new log of the same download does not repeat the warning."""
    caplog.set_level(logging.INFO)
    records = FailureRecords()
    FailureLog(_LOGGER, "Forecast", records).failed("down")

    again = FailureLog(_LOGGER, "Forecast", records)
    again.failed("still down")
    again.succeeded()
    # A log without records starts anew
    FailureLog(_LOGGER, "Radar").failed("down")
    FailureLog(_LOGGER, "Radar").failed("down")

    assert [r.getMessage() for r in caplog.records] == [
        "Forecast failed: down",
        "Forecast works again",
        "Radar failed: down",
        "Radar failed: down",
    ]


def test_closed_records_log_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """A download finishing after its entry has unloaded is not logged."""
    caplog.set_level(logging.INFO)
    records = FailureRecords()
    radar = FailureLog(_LOGGER, "Radar", records)
    radar.failed("offline")
    records.closed = True

    radar.succeeded()
    FailureLog(_LOGGER, "Forecast", records).failed("timeout")
    records.warn_once(_LOGGER, "Unknown %r", "a")

    assert [r.getMessage() for r in caplog.records] == ["Radar failed: offline"]


def test_held_warnings_wait_for_the_release(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """While held, warnings are debug messages; the release logs them."""
    caplog.set_level(logging.DEBUG)
    records = FailureRecords()
    records.hold()
    radar = FailureLog(_LOGGER, "Radar", records)
    error = ValueError("broken")

    radar.failed(error, exc_info=error)
    radar.failed("still broken")
    records.warn_once(_LOGGER, "Unknown %r", "a")

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("DEBUG", "Radar failed: broken"),
        ("DEBUG", "Unknown 'a'"),
    ]

    caplog.clear()
    records.release()
    radar.succeeded()

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "Radar failed: broken"),
        ("WARNING", "Unknown 'a'"),
        ("WARNING", "Radar works again"),
    ]
    assert caplog.records[0].exc_info[1] is error


def test_warn_once(caplog: pytest.LogCaptureFixture) -> None:
    """A warning is logged once for each set of arguments."""
    records = FailureRecords()

    for value in ("a", "a", "b"):
        records.warn_once(_LOGGER, "Unknown %r", value)

    assert [r.getMessage() for r in caplog.records] == [
        "Unknown 'a'",
        "Unknown 'b'",
    ]


@pytest.mark.parametrize(
    ("log_of", "what"),
    [
        pytest.param(
            lambda records: IdokepDataUpdateCoordinator(
                Mock(), _LOGGER, "test", _ENTRY, records
            )._failures[CURRENT],
            "Időkép current weather download for Eger",
            id="page",
        ),
        pytest.param(
            lambda records: RadarCoordinator(
                Mock(), _LOGGER, _ENTRY, Mock(), records
            )._failures,
            "Időkép radar download",
            id="radar",
        ),
        pytest.param(
            lambda records: StationCoordinator(
                Mock(), _LOGGER, _ENTRY, Mock(), ["jozsamet"], records=records
            )._failures["jozsamet"],
            "Időkép station jozsamet download",
            id="station",
        ),
    ],
)
async def test_coordinators_keep_failing_downloads_in_the_records(
    caplog: pytest.LogCaptureFixture,
    log_of: Callable[[FailureRecords | None], FailureLog],
    what: str,
) -> None:
    """Warned once per records; after a reload, with new ones, once again."""
    caplog.set_level(logging.INFO)
    records = FailureRecords()
    log_of(records).failed("down")
    log_of(records).failed("still down")
    assert records.failing == {what}

    # After a reload: no "works again" for a download that works at once
    log_of(FailureRecords()).succeeded()
    log_of(FailureRecords()).failed("down")
    # Without records a coordinator keeps its own
    log_of(None).failed("down")

    assert [r.getMessage() for r in caplog.records] == [f"{what} failed: down"] * 3
