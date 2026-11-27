"""
The setup, import and Configure flows, run by Home Assistant's flow managers.

Every error is followed by a corrected try that finishes the flow.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry,
    category_registry,
    device_registry,
    entity_registry,
    floor_registry,
    frame,
    issue_registry,
    label_registry,
)
from slugify import slugify

from custom_components.idokep_weather_and_forecast.api import (
    IdokepApiClientCommunicationError,
)
from custom_components.idokep_weather_and_forecast.const import (
    DOMAIN,
    ENTRY_TYPE,
    RADAR,
    RADAR_UNIQUE_ID,
)
from custom_components.idokep_weather_and_forecast.station import StationDataError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

PACKAGE = "custom_components.idokep_weather_and_forecast"
TRANSLATIONS = (
    Path(__file__).parent.parent / PACKAGE.replace(".", "/") / "translations"
)
INPUT = {
    "name": "Home",
    "location": "Debrecen-Józsa",
    "station": ["jozsamet"],
    "merge_method": "average",
    "max_age": 90,
}
OPTIONS = {"station": ["jozsamet"], "merge_method": "average", "max_age": 90}
# The settlements the fake site knows
SETTLEMENTS = ("Budapest", "Debrecen-Józsa", "Eger")

# What goes wrong: the input that differs, the site's behaviour, the form's error
ERRORS = [
    pytest.param({"name": " "}, {}, {"name": "name"}, id="no-name"),
    pytest.param(
        {},
        {"weather_error": IdokepApiClientCommunicationError("down")},
        {"base": "connection"},
        id="site-down",
    ),
    pytest.param(
        {}, {"temperature": ""}, {"base": "connection"}, id="no-weather-on-page"
    ),
    pytest.param(
        {"location": "Nincsilyen"},
        {},
        {"base": "unknown_location"},
        id="no-such-place",
    ),
    pytest.param(
        {},
        {"weather_error": RuntimeError("bug")},
        {"base": "unknown"},
        id="unexpected",
    ),
    pytest.param(
        {},
        {"station_error": StationDataError("unknown code")},
        {"station": "station"},
        id="no-such-station",
    ),
    pytest.param(
        {},
        {"station_error": IdokepApiClientCommunicationError("down")},
        {"station": "station_connection"},
        id="station-down",
    ),
    pytest.param(
        {},
        {"station_error": RuntimeError("bug")},
        {"station": "unknown"},
        id="station-unexpected",
    ),
]


class FakeSite:
    """What idokep.hu answers to the flows' checks."""

    def __init__(self) -> None:
        """Answer every check until told otherwise."""
        self.working()

    def working(self) -> None:
        """Make every check pass."""
        self.temperature = "8 \u2103"
        self.weather_error: Exception | None = None
        self.station_error: Exception | None = None

    async def current_page(self, url: str) -> str:
        """Stand in for a settlement's current weather page."""
        if self.weather_error:
            raise self.weather_error
        asked = slugify(url.rsplit("/", 1)[1])
        # Like the site: Budapest's page for a name it doesn't know
        served = next(
            (name for name in SETTLEMENTS if slugify(name) == asked), "Budapest"
        )
        return (
            f"<title>{served} időjárás - Időkép</title>"
            f'<div class="current-temperature">{self.temperature}</div>'
        )

    async def station(self, _session: object, _code: str) -> None:
        """Stand in for a station's file."""
        if self.station_error:
            raise self.station_error


@pytest.fixture
def site() -> Iterator[FakeSite]:
    """Replace the flows' downloads."""
    fake = FakeSite()
    with (
        patch(f"{PACKAGE}.config_flow.async_create_clientsession"),
        patch(f"{PACKAGE}.api.HttpClient.get_html", fake.current_page),
        patch(f"{PACKAGE}.config_flow.fetch_station", fake.station),
    ):
        yield fake


@pytest.fixture
async def hass(tmp_path: Path) -> AsyncIterator[HomeAssistant]:
    """Run a Home Assistant whose entries start without doing anything."""
    instance = HomeAssistant(str(tmp_path))
    instance.config.skip_pip = True
    loader.async_setup(instance)
    frame.async_setup(instance)
    instance.config_entries = config_entries.ConfigEntries(instance, {})
    await instance.config_entries.async_initialize()
    for registry in (
        category_registry,
        floor_registry,
        label_registry,
        area_registry,
        device_registry,
        entity_registry,
        issue_registry,
    ):
        if hasattr(registry, "async_setup"):
            registry.async_setup(instance)
        await registry.async_load(instance)
    with (
        patch(f"{PACKAGE}.async_setup_entry", AsyncMock(return_value=True)),
        patch(f"{PACKAGE}.async_unload_entry", AsyncMock(return_value=True)),
    ):
        yield instance
        await instance.async_stop(force=True)


async def start(hass: HomeAssistant, source: str = config_entries.SOURCE_USER) -> Any:
    """Start a config flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source}
    )
    await hass.async_block_till_done()
    return result


async def submit(hass: HomeAssistant, result: Any, user_input: dict) -> Any:
    """Fill in the setup form."""
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()
    return result


async def add_entry(hass: HomeAssistant, **changes: Any) -> Any:
    """Add a settlement through the setup flow and return its entry."""
    result = await submit(hass, await start(hass), {**INPUT, **changes})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    return result["result"]


async def test_user_flow_creates_an_entry(hass: HomeAssistant, site: FakeSite) -> None:
    """The settlement is the data and unique ID, the station settings are options."""
    result = await start(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await submit(hass, result, INPUT)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "Home"
    assert entry.unique_id == "debrecen-jozsa"
    assert dict(entry.data) == {"location": "Debrecen-Józsa"}
    assert dict(entry.options) == OPTIONS


@pytest.mark.parametrize(("changes", "behaviour", "errors"), ERRORS)
async def test_user_flow_recovers_from_an_error(
    hass: HomeAssistant,
    site: FakeSite,
    changes: dict,
    behaviour: dict,
    errors: dict,
) -> None:
    """The error is shown; once put right, the same flow creates the entry."""
    for name, value in behaviour.items():
        setattr(site, name, value)

    result = await submit(hass, await start(hass), {**INPUT, **changes})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == errors
    assert not hass.config_entries.async_entries(DOMAIN)

    site.working()
    result = await submit(hass, result, INPUT)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "debrecen-jozsa"


async def test_spaces_around_the_settlement_are_left_out(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """A pasted name with spaces around it is the settlement itself."""
    result = await submit(
        hass, await start(hass), {**INPUT, "location": "  Debrecen-Józsa "}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert dict(result["result"].data) == {"location": "Debrecen-Józsa"}
    assert result["result"].unique_id == "debrecen-jozsa"


async def test_the_sites_spelling_is_stored(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """A name typed without accents is saved as the site writes it."""
    entry = await add_entry(hass, location="debrecen-jozsa")

    assert dict(entry.data) == {"location": "Debrecen-Józsa"}
    assert entry.unique_id == "debrecen-jozsa"


@pytest.mark.parametrize(
    ("stored", "typed", "saved"),
    [
        pytest.param("Debrecen-Józsa", "eger", "Eger", id="new-settlement"),
        pytest.param(
            "Debrecen-Józsa",
            "debrecen-jozsa",
            "Debrecen-Józsa",
            id="same-settlement",
        ),
        pytest.param(
            "debrecen-jozsa",
            "DEBRECEN-JOZSA",
            "Debrecen-Józsa",
            id="saved-without-accents",
        ),
    ],
)
async def test_options_flow_stores_the_sites_spelling(
    hass: HomeAssistant, site: FakeSite, stored: str, typed: str, saved: str
) -> None:
    """Configure saves a changed name as the site writes it."""
    entry = await add_entry(hass)
    hass.config_entries.async_update_entry(entry, data={"location": stored})
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**INPUT, "location": typed}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert dict(entry.data) == {"location": saved}
    assert entry.unique_id == slugify(saved)


async def test_a_settlement_is_added_only_once(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """A second entry of the same settlement is aborted."""
    await add_entry(hass)

    result = await submit(hass, await start(hass), {**INPUT, "name": "Again"})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert unique_ids(hass) == ["debrecen-jozsa", RADAR_UNIQUE_ID]


async def test_the_same_entry_sent_twice_is_added_once(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """The second of two forms still being checked is aborted, with a text."""
    forms = [await start(hass), await start(hass)]
    checked = asyncio.Event()

    async def station(_session: object, _code: str) -> None:
        await checked.wait()

    with patch(f"{PACKAGE}.config_flow.fetch_station", station):
        sent = [asyncio.create_task(submit(hass, form, INPUT)) for form in forms]
        await asyncio.sleep(0)
        checked.set()
        results = await asyncio.gather(*sent)

    created, aborted = sorted(results, key=lambda r: r["type"] == FlowResultType.ABORT)
    assert created["type"] == FlowResultType.CREATE_ENTRY
    assert aborted["type"] == FlowResultType.ABORT
    assert aborted["reason"] == "already_in_progress"
    assert unique_ids(hass) == ["debrecen-jozsa", RADAR_UNIQUE_ID]
    # A newer Home Assistant has its own text; 2026.7.0 looks for the integration's
    for language in ("en", "hu"):
        path = TRANSLATIONS / f"{language}.json"
        texts = json.loads(path.read_text(encoding="utf-8"))
        assert texts["config"]["abort"][aborted["reason"]]


def unique_ids(hass: HomeAssistant) -> list[str]:
    """Return the entries' unique IDs."""
    return sorted(e.unique_id for e in hass.config_entries.async_entries(DOMAIN))


def without(user_input: dict, *keys: str) -> dict:
    """Return the input as sent with these fields left empty."""
    return {key: value for key, value in user_input.items() if key not in keys}


# What the form sends with everything left empty
EMPTY = without(INPUT, "name", "location", "station")
STATION_ONLY = without(INPUT, "location")


def radar_checkbox(result: Any) -> Any:
    """Return the form's radar field, or None if it has none."""
    return next((k for k in result["data_schema"].schema if k == "add_radar"), None)


async def test_the_radar_is_checked_by_default(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """Adding a settlement adds the Radar entry; then the checkbox is gone."""
    result = await start(hass)
    marker = radar_checkbox(result)
    assert marker.default() is True
    # A checkbox: Home Assistant shows a selector as a switch
    assert result["data_schema"].schema[marker] is bool

    await submit(hass, result, INPUT)
    result = await start(hass)

    assert radar_checkbox(result) is None

    await submit(hass, result, {**INPUT, "name": "Capital", "location": "Budapest"})

    assert unique_ids(hass) == ["budapest", "debrecen-jozsa", RADAR_UNIQUE_ID]


async def test_the_checkbox_returns_when_the_radar_is_deleted(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """Deleting the Radar entry offers it again."""
    radar = (await start(hass, config_entries.SOURCE_IMPORT))["result"]
    assert radar_checkbox(await start(hass)) is None

    await hass.config_entries.async_remove(radar.entry_id)

    assert radar_checkbox(await start(hass)) is not None


async def test_a_form_without_the_checkbox_adds_no_radar(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """The Radar entry deleted while the form is open stays deleted."""
    radar = (await start(hass, config_entries.SOURCE_IMPORT))["result"]
    result = await start(hass)
    await hass.config_entries.async_remove(radar.entry_id)

    # Refused once: the form shown again has the checkbox, checked
    result = await submit(hass, result, EMPTY)

    assert result["errors"] == {"name": "name"}
    assert radar_checkbox(result).default() is True

    await hass.config_entries.flow.async_configure(
        result["flow_id"], {**INPUT, "add_radar": False}
    )
    await hass.async_block_till_done()

    assert unique_ids(hass) == ["debrecen-jozsa"]


async def test_the_open_form_adds_nothing_it_did_not_show(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """A complete form without the checkbox adds only its own entry."""
    radar = (await start(hass, config_entries.SOURCE_IMPORT))["result"]
    result = await start(hass)
    await hass.config_entries.async_remove(radar.entry_id)

    await submit(hass, result, INPUT)

    assert unique_ids(hass) == ["debrecen-jozsa"]


async def test_the_radar_added_meanwhile_is_not_repeated(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """An open empty form finds the Radar entry already there."""
    result = await start(hass)
    await start(hass, config_entries.SOURCE_IMPORT)

    result = await submit(hass, result, EMPTY)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert unique_ids(hass) == [RADAR_UNIQUE_ID]


async def test_the_radar_can_be_left_out(hass: HomeAssistant, site: FakeSite) -> None:
    """Without the checkbox no Radar entry is made."""
    entry = await add_entry(hass, add_radar=False)

    assert unique_ids(hass) == ["debrecen-jozsa"]
    assert "add_radar" not in entry.data
    assert "add_radar" not in entry.options


async def test_a_disabled_radar_stays_as_it_is(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """A disabled Radar entry counts: no checkbox, nothing added or enabled."""
    radar = (await start(hass, config_entries.SOURCE_IMPORT))["result"]
    await hass.config_entries.async_set_disabled_by(
        radar.entry_id, config_entries.ConfigEntryDisabler.USER
    )
    assert radar_checkbox(await start(hass)) is None

    await add_entry(hass)

    assert unique_ids(hass) == ["debrecen-jozsa", RADAR_UNIQUE_ID]
    assert radar.disabled_by is config_entries.ConfigEntryDisabler.USER


async def test_a_refused_entry_adds_no_radar(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """The Radar entry is only added together with an accepted entry."""
    await add_entry(hass, add_radar=False)

    result = await submit(hass, await start(hass), INPUT)

    assert result["type"] == FlowResultType.ABORT
    assert unique_ids(hass) == ["debrecen-jozsa"]


async def test_the_empty_form_adds_only_the_radar(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """Name, settlement and stations left empty: just the Radar entry."""
    result = await submit(hass, await start(hass), EMPTY)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Radar"
    assert dict(result["result"].data) == {ENTRY_TYPE: RADAR}
    assert unique_ids(hass) == [RADAR_UNIQUE_ID]

    # Now there is no checkbox, and the empty form is just incomplete
    result = await submit(hass, await start(hass), EMPTY)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"name": "name"}
    assert unique_ids(hass) == [RADAR_UNIQUE_ID]


@pytest.mark.parametrize(
    ("radar", "error"), [(False, "nothing_or_radar"), (True, "nothing")]
)
async def test_the_error_mentions_the_radar_only_if_it_can_be_added(
    hass: HomeAssistant, site: FakeSite, radar: bool, error: str
) -> None:
    """A name alone is not enough, with or without a Radar entry."""
    if radar:
        await start(hass, config_entries.SOURCE_IMPORT)

    result = await submit(hass, await start(hass), {**EMPTY, "name": "Home"})

    assert result["errors"] == {"base": error}


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        pytest.param(
            {**EMPTY, "name": "Home"},
            {"base": "nothing_or_radar"},
            id="only-a-name",
        ),
        pytest.param({**EMPTY, "add_radar": False}, {"name": "name"}, id="no-radar"),
        pytest.param(
            {**EMPTY, "name": "Home", "location": "  ", "station": [" "]},
            {"base": "nothing_or_radar"},
            id="blank",
        ),
        pytest.param(without(INPUT, "name"), {"name": "name"}, id="name-left-out"),
        pytest.param(
            without(INPUT, "name", "location"), {"name": "name"}, id="stations-only"
        ),
    ],
)
async def test_the_form_needs_a_name_and_something_to_show(
    hass: HomeAssistant, site: FakeSite, user_input: dict, errors: dict
) -> None:
    """The error is shown; the completed form creates the entry."""
    result = await submit(hass, await start(hass), user_input)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == errors
    assert not hass.config_entries.async_entries(DOMAIN)

    result = await submit(hass, result, INPUT)

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_an_entry_without_a_forecast(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """Only stations: no settlement is checked or saved."""
    site.weather_error = RuntimeError("not asked for")

    result = await submit(hass, await start(hass), STATION_ONLY)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "Home"
    assert entry.unique_id == "stations_jozsamet"
    assert not entry.data
    assert dict(entry.options) == OPTIONS


async def test_stations_without_a_forecast_are_added_only_once(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """The same stations in any order are refused; others and a settlement's aren't."""
    both = {**STATION_ONLY, "station": ["jozsamet", "olig13"], "add_radar": False}
    await submit(hass, await start(hass), both)

    result = await submit(
        hass, await start(hass), {**both, "station": ["olig13", "JozsaMet"]}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    await submit(hass, await start(hass), {**both, "station": ["olig13"]})
    await add_entry(hass, station=["jozsamet", "olig13"], add_radar=False)

    assert unique_ids(hass) == [
        "debrecen-jozsa",
        "stations_jozsamet_olig13",
        "stations_olig13",
    ]


async def configure(hass: HomeAssistant, entry: Any, user_input: dict) -> Any:
    """Send the Configure form."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()
    return result


def configured(**changes: Any) -> dict:
    """Return the Configure form's input (it has no radar checkbox)."""
    return {**INPUT, **changes}


async def test_configure_removes_and_adds_the_forecast(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """Clearing the settlement leaves the stations; it can be filled in again."""
    entry = await add_entry(hass)

    result = await configure(hass, entry, STATION_ONLY)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert not entry.data
    assert entry.unique_id == "stations_jozsamet"

    site.weather_error = RuntimeError("not asked for")
    result = await configure(
        hass, entry, {**STATION_ONLY, "station": ["olig13"], "name": "Garden"}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.title == "Garden"
    assert entry.unique_id == "stations_olig13"
    assert entry.options["station"] == ["olig13"]

    site.working()
    result = await configure(hass, entry, configured(location="eger", station=[]))

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert dict(entry.data) == {"location": "Eger"}
    assert entry.unique_id == "eger"
    assert entry.options["station"] == []


async def test_configure_reloads_when_only_the_settlement_is_cleared(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """The options stay the same, so the reload is asked for."""
    entry = await add_entry(hass)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await configure(hass, entry, STATION_ONLY)

    reload.assert_called_once_with(entry.entry_id)


async def test_configure_needs_something_to_show(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """The settlement and the stations can't both be cleared."""
    entry = await add_entry(hass)

    result = await configure(hass, entry, without(INPUT, "location", "station"))

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "nothing"}
    assert dict(entry.data) == {"location": "Debrecen-Józsa"}
    assert dict(entry.options) == OPTIONS


async def test_configure_refuses_stations_of_another_entry(
    hass: HomeAssistant, site: FakeSite
) -> None:
    """Two entries without a forecast can't have the same stations."""
    await submit(hass, await start(hass), STATION_ONLY)
    entry = await add_entry(hass)

    result = await configure(hass, entry, STATION_ONLY)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"station": "stations_configured"}
    assert entry.unique_id == "debrecen-jozsa"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**STATION_ONLY, "station": ["olig13"]}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.unique_id == "stations_olig13"


async def test_import_creates_the_radar_entry_once(hass: HomeAssistant) -> None:
    """The import flow needs no form; a second one is aborted."""
    result = await start(hass, config_entries.SOURCE_IMPORT)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == RADAR_UNIQUE_ID
    assert dict(entry.data) == {ENTRY_TYPE: RADAR}
    assert not entry.supports_options

    result = await start(hass, config_entries.SOURCE_IMPORT)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("changes", "behaviour", "errors"),
    [
        *ERRORS,
        pytest.param(
            {"location": "Budapest"},
            {},
            {"location": "already_configured"},
            id="used-by-another-entry",
        ),
    ],
)
async def test_options_flow_recovers_from_an_error(
    hass: HomeAssistant,
    site: FakeSite,
    changes: dict,
    behaviour: dict,
    errors: dict,
) -> None:
    """The error leaves the entry as it was; the corrected form saves."""
    entry = await add_entry(hass)
    await add_entry(hass, name="Capital", location="Budapest")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    for name, value in behaviour.items():
        setattr(site, name, value)
    # A new settlement, so that it is checked too
    new = {**INPUT, "location": "Eger", "max_age": 30}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**new, **changes}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == errors
    assert entry.unique_id == "debrecen-jozsa"
    assert dict(entry.options) == OPTIONS

    site.working()
    result = await hass.config_entries.options.async_configure(result["flow_id"], new)
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.unique_id == "eger"
    assert dict(entry.data) == {"location": "Eger"}
    assert dict(entry.options) == {**OPTIONS, "max_age": 30}


@pytest.mark.parametrize("taken_by", ["setup", "configure"])
async def test_options_flow_leaves_a_settlement_taken_meanwhile(
    hass: HomeAssistant, site: FakeSite, taken_by: str
) -> None:
    """Another form takes the settlement while this one is still checking."""
    entry = await add_entry(hass)
    other = await add_entry(hass, name="Capital", location="Budapest")
    checking, answered = asyncio.Event(), asyncio.Event()

    async def slow_station(_session: object, _code: str) -> None:
        checking.set()
        await answered.wait()

    eger = {**INPUT, "location": "Eger"}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(f"{PACKAGE}.config_flow.fetch_station", slow_station):
        saving = asyncio.create_task(
            hass.config_entries.options.async_configure(result["flow_id"], eger)
        )
        await checking.wait()
        # Without a station, so the other form doesn't have to wait
        if taken_by == "setup":
            await add_entry(hass, name="New", location="Eger", station=[])
        else:
            second = await hass.config_entries.options.async_init(other.entry_id)
            await hass.config_entries.options.async_configure(
                second["flow_id"], {**eger, "name": "Capital", "station": []}
            )
        answered.set()
        result = await saving
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"location": "already_configured"}
    assert entry.unique_id == "debrecen-jozsa"
    assert dict(entry.data) == {"location": "Debrecen-Józsa"}
    assert [
        e.unique_id for e in hass.config_entries.async_entries(DOMAIN)
    ].count("eger") == 1

    # With a free settlement the same form then saves
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**INPUT, "max_age": 30}
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.unique_id == "debrecen-jozsa"
    assert entry.options["max_age"] == 30
