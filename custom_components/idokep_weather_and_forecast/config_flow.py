"""Adds config flow for Idokep."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from slugify import slugify

from .api import (
    IdokepApiClient,
    IdokepApiClientError,
    IdokepApiClientUnknownLocationError,
)
from .const import (
    ADD_RADAR,
    DEFAULT_MAX_AGE,
    DOMAIN,
    ENTRY_TYPE,
    LOCATION,
    LOGGER,
    MAX_AGE,
    MAX_MAX_AGE,
    MERGE_METHOD,
    MERGE_METHODS,
    MERGE_PRIORITY,
    MIN_MAX_AGE,
    RADAR,
    RADAR_TITLE,
    RADAR_UNIQUE_ID,
    STATION,
)
from .station import (
    StationDataError,
    fetch_station,
    max_age_minutes,
    merge_method,
    parse_codes,
    station_codes,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _clearable(key: str, value: str | None) -> vol.Optional:
    """Return a field filled in with a value that can also be left empty."""
    # Not a default, which would come back when the field is cleared
    return vol.Optional(
        key, description={"suggested_value": value} if value else None
    )


def _text_selector() -> selector.TextSelector:
    """Return a one-line text field."""
    return selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
    )


def _location_field(value: str | None) -> dict[vol.Optional, Any]:
    """Return the location name field of a form (empty: no forecast)."""
    return {_clearable(LOCATION, value): _text_selector()}


def _station_fields(
    codes: list[str], method: str, max_age: int
) -> dict[vol.Optional, Any]:
    """Return the optional station codes, merge method and age limit fields."""
    return {
        vol.Optional(
            STATION,
            description={"suggested_value": codes} if codes else None,
        ): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT, multiple=True
            ),
        ),
        vol.Optional(MERGE_METHOD, default=method): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=MERGE_METHODS,
                translation_key=MERGE_METHOD,
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
        vol.Optional(MAX_AGE, default=max_age): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_MAX_AGE,
                max=MAX_MAX_AGE,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            ),
        ),
    }


def _unique_id(location: str, codes: list[str]) -> str:
    """Return an entry's unique ID: the settlement, without one the stations."""
    if location:
        return slugify(location)
    # slugify never gives "_", so this can't be a settlement's ID
    return "_".join(["stations", *sorted(slugify(code) for code in codes)])


def _has_radar(hass: HomeAssistant) -> bool:
    """
    Return True if there is a national Radar service entry.

    A disabled or ignored one counts too.
    """
    return any(
        entry.unique_id == RADAR_UNIQUE_ID
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


def _radar_field(hass: HomeAssistant, checked: bool) -> dict[vol.Optional, Any]:
    """Return the Radar checkbox, left out while there is a Radar entry."""
    if _has_radar(hass):
        return {}
    # A plain bool is shown as a checkbox, a BooleanSelector as a switch
    return {vol.Optional(ADD_RADAR, default=checked): bool}


@callback
def _async_add_radar(hass: HomeAssistant) -> None:
    """Add the national Radar service entry if there is none."""
    if _has_radar(hass):
        return
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_IMPORT}
        )
    )


async def _async_check_location(
    hass: HomeAssistant, location: str
) -> tuple[str, str | None]:
    """
    Return the location name to store and the form error.

    A name idokep.hu knows comes back as the site spells it, without an error;
    any other name comes back as given.
    """
    client = IdokepApiClient(session=async_create_clientsession(hass))
    try:
        # Only the current conditions page is needed to validate the location
        return await client.async_validate_location(location), None
    except IdokepApiClientUnknownLocationError:
        return location, "unknown_location"
    except (ValueError, IdokepApiClientError) as exception:
        LOGGER.error(exception)
        return location, "connection"
    except Exception:  # pylint: disable=broad-exception-caught
        LOGGER.exception("Unexpected error validating location")
        return location, "unknown"


async def _async_station_error(
    hass: HomeAssistant, codes: list[str]
) -> tuple[str, str] | None:
    """Return (form error, station code) for the first unusable code, or None."""
    session = async_create_clientsession(hass)
    for code in codes:
        try:
            await fetch_station(session, code)
        except StationDataError:
            return "station", code
        except IdokepApiClientError as exception:
            LOGGER.error(exception)
            return "station_connection", code
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Unexpected error validating station %s", code)
            return "unknown", code
    return None


class IdokepFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Idokep."""

    VERSION = 1
    # See async_migrate_entry
    MINOR_VERSION = 2

    def is_matching(self, _other_flow: config_entries.ConfigFlow) -> bool:
        """Return True if config flows match."""
        return False

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow (station code of a location entry)."""
        return IdokepOptionsFlow(config_entry)

    @classmethod
    def async_supports_options_flow(
        cls, config_entry: config_entries.ConfigEntry
    ) -> bool:
        """Only location entries have options (not the Radar entry)."""
        return config_entry.data.get(ENTRY_TYPE) != RADAR

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle a flow initialized by the user.

        The form adds an entry with a settlement's forecast, weather stations
        or both. Without a Radar entry it has a checkbox that adds one too, or
        only that one if the name, the settlement and the stations are empty.
        """
        errors: dict[str, str] = {}
        placeholders = {"code": ""}
        name = location = ""
        codes: list[str] = []
        method = MERGE_PRIORITY
        max_age = DEFAULT_MAX_AGE
        add_radar = False
        checked = True
        if user_input is not None:
            name = (user_input.get(CONF_NAME) or "").strip()
            location = (user_input.get(LOCATION) or "").strip()
            codes = parse_codes(user_input.get(STATION))
            method = user_input.get(MERGE_METHOD, MERGE_PRIORITY)
            # The number field gives a float
            max_age = int(user_input.get(MAX_AGE, DEFAULT_MAX_AGE))
            # Not sent by a form that had no checkbox
            add_radar = user_input.get(ADD_RADAR, False)
            checked = user_input.get(ADD_RADAR, True)
            if add_radar and not (name or location or codes):
                return await self._async_create_radar_entry()
            if not name:
                errors[CONF_NAME] = "name"
            elif not (location or codes):
                # The longer message also tells how to add only the radar
                errors["base"] = (
                    "nothing" if _has_radar(self.hass) else "nothing_or_radar"
                )
            else:
                error = None
                if location:
                    location, error = await _async_check_location(self.hass, location)
                if error:
                    errors["base"] = error
                elif problem := await _async_station_error(self.hass, codes):
                    errors[STATION], placeholders["code"] = problem
            if not errors:
                await self.async_set_unique_id(
                    unique_id=_unique_id(location, codes)
                )
                self._abort_if_unique_id_configured()
                if add_radar:
                    _async_add_radar(self.hass)
                # The settlement is data, the station settings are options
                options = {MERGE_METHOD: method, MAX_AGE: max_age}
                if codes:
                    options[STATION] = codes
                # The name is the entry's title (and so the device names)
                return self.async_create_entry(
                    title=name,
                    data={LOCATION: location} if location else {},
                    options=options,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    _clearable(CONF_NAME, name): _text_selector(),
                    **_location_field(location),
                    **_station_fields(codes, method, max_age),
                    **_radar_field(self.hass, checked),
                },
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_import(
        self, _import_data: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """
        Create the Radar service entry (no form).

        Started by the setup form when it adds another entry too.
        """
        return await self._async_create_radar_entry()

    async def _async_create_radar_entry(self) -> config_entries.ConfigFlowResult:
        """Create the Radar service entry, unless there is one."""
        await self.async_set_unique_id(RADAR_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=RADAR_TITLE, data={ENTRY_TYPE: RADAR})


class IdokepOptionsFlow(config_entries.OptionsFlowWithReload):
    """Change a location entry's name, location and weather station."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the name, the location and the station settings."""
        errors: dict[str, str] = {}
        placeholders = {"code": ""}
        name = self._entry.title
        old_location = self._entry.data.get(LOCATION, "")
        old_codes = station_codes(self._entry)
        location, codes = old_location, old_codes
        method = merge_method(self._entry)
        max_age = max_age_minutes(self._entry)
        if user_input is not None:
            name = (user_input.get(CONF_NAME) or "").strip()
            location = (user_input.get(LOCATION) or "").strip()
            codes = parse_codes(user_input.get(STATION))
            method = user_input.get(MERGE_METHOD, MERGE_PRIORITY)
            max_age = int(user_input.get(MAX_AGE, DEFAULT_MAX_AGE))
            if not name:
                errors[CONF_NAME] = "name"
            if not (location or codes):
                errors["base"] = "nothing"
            elif location and location != old_location:
                location, location_errors = await self._async_check_new_location(
                    location
                )
                errors.update(location_errors)
            if not errors and (
                problem := await _async_station_error(self.hass, codes)
            ):
                errors[STATION], placeholders["code"] = problem
            # With nothing to wait for before saving: another form may have
            # taken the settlement or the stations during the checks above
            unique_id = _unique_id(location, codes)
            if (
                not errors
                and unique_id != _unique_id(old_location, old_codes)
                and self._used_by_another_entry(unique_id)
            ):
                if location:
                    errors[LOCATION] = "already_configured"
                else:
                    errors[STATION] = "stations_configured"
            if not errors:
                options = {STATION: codes, MERGE_METHOD: method, MAX_AGE: max_age}
                self._async_update_entry(name, location, options)
                # Reloaded afterwards, which adds or removes the station device
                return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=name or vol.UNDEFINED
                    ): _text_selector(),
                    **_location_field(location),
                    **_station_fields(codes, method, max_age),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _used_by_another_entry(self, unique_id: str) -> bool:
        """Return True if another entry has the unique ID."""
        return any(
            entry.unique_id == unique_id and entry.entry_id != self._entry.entry_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    async def _async_check_new_location(
        self, location: str
    ) -> tuple[str, dict[str, str]]:
        """Return a new location name as to be stored, and its form errors."""
        if self._used_by_another_entry(_unique_id(location, [])):
            return location, {LOCATION: "already_configured"}
        location, error = await _async_check_location(self.hass, location)
        return location, {"base": error} if error else {}

    def _async_update_entry(
        self, name: str, location: str, options: dict[str, Any]
    ) -> None:
        """Apply a new name and location (the stations are saved as options)."""
        entry = self._entry
        changes: dict[str, Any] = {}
        if name != entry.title:
            self._async_rename_devices(entry.title, name)
            changes["title"] = name
        old_location = entry.data.get(LOCATION, "")
        if location != old_location:
            data = {key: value for key, value in entry.data.items() if key != LOCATION}
            if location:
                data[LOCATION] = location
            changes["data"] = data
        unique_id = _unique_id(location, options[STATION])
        if unique_id != _unique_id(old_location, station_codes(entry)):
            changes["unique_id"] = unique_id
        if not changes:
            return
        self.hass.config_entries.async_update_entry(entry, **changes)
        if options == dict(entry.options):
            # Options unchanged, so the automatic reload won't happen
            self.hass.config_entries.async_schedule_reload(entry.entry_id)

    def _async_rename_devices(self, old: str, new: str) -> None:
        """
        Put the new entry name into the entry's device names.

        Covers "<name> Forecast" and "<name> Weather", also when renamed by
        the user, as long as the name still contains the old entry name.
        """
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            registry, self._entry.entry_id
        ):
            changes = {
                field: value.replace(old, new)
                for field in ("name", "name_by_user")
                if (value := getattr(device, field)) and old in value
            }
            if changes:
                registry.async_update_device(device.id, **changes)
