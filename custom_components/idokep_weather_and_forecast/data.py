"""Custom types for idokep."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .failures import FailureRecords

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api import IdokepApiClient
    from .coordinator import (
        IdokepDataUpdateCoordinator,
        RadarCoordinator,
        StationCoordinator,
    )


@dataclass
class IdokepData:
    """Data for the Idokep integration."""

    # The settlement's forecast (both None without a settlement)
    client: IdokepApiClient | None = None
    coordinator: IdokepDataUpdateCoordinator | None = None
    # The optional weather station's measurements (None without a station)
    station: StationCoordinator | None = None
    # What is logged already, so that it is not logged again
    failures: FailureRecords = field(default_factory=FailureRecords)


@dataclass
class RadarData:
    """Data for the national Radar service entry."""

    coordinator: RadarCoordinator
    # What is logged already, so that it is not logged again
    failures: FailureRecords = field(default_factory=FailureRecords)


# Type aliases for the location entries and the Radar entry
if TYPE_CHECKING:
    IdokepConfigEntry = ConfigEntry[IdokepData]
    RadarConfigEntry = ConfigEntry[RadarData]
    # Either of them, where both kinds are handled
    AnyIdokepConfigEntry = IdokepConfigEntry | RadarConfigEntry
