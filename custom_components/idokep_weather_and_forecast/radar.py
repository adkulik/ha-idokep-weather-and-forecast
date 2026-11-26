"""
National radar animation for the Radar service entry.

idokep.hu publishes the national radar as six JPEG frames with the map
already drawn in: /radar/radar0.jpg (newest) ... /radar/radar5.jpg (oldest),
about five minutes apart. They are turned into one looping GIF (oldest to
newest). A new frame lands in radar0.jpg first; the other files follow it one
by one over the next minutes, so for a while two of them can be the same.

To keep traffic low, radar0.jpg is checked with a conditional request
("only if changed": the server answers 304 with no body). A new frame becomes
downloadable about a minute after each 5-minute mark, so it is checked at
x:00:59, x:05:59, ... x:55:59; if no new frame is there yet (or the check
fails), again every minute (x:01:59, x:02:59, ...) until one arrives. When a
new frame appears, only that frame is downloaded and the older ones shift
along; all six are downloaded only at start-up and after a failure (a frame
repeated there is kept once, so the animation can start with fewer frames).

After a failure there is no animation until a download works again, which is
tried every minute.
"""

from __future__ import annotations

import asyncio
import io
import socket
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import aiohttp
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from PIL import Image

from .api import _BROWSER_HEADERS, IdokepApiClientCommunicationError
from .failures import FailureLog

if TYPE_CHECKING:
    from datetime import datetime
    from logging import Logger

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

FRAME_URL = "https://www.idokep.hu/radar/radar{index}.jpg"
# idokep.hu's radar page (the radar device's "Visit" link)
RADAR_PAGE_URL = "https://www.idokep.hu/radar"
# radar0.jpg is the newest frame, radar5.jpg the oldest
NEWEST_INDEX = 0
FRAME_COUNT = 6
TIMEOUT = 10
# Animation timing: each frame, then a longer pause on the newest
FRAME_MS = 1000
LAST_FRAME_MS = 2000
# Regular checks on every 5th minute; in between only while waiting for a
# frame (the timer fires every minute, at second 59)
CHECK_EVERY_MINUTES = 5
# The radar*.jpg files (840×626) are horizontally squeezed; idokep.hu shows
# the same map 970×648 (its radar video). Frames are widened to that aspect.
MAP_ASPECT = 970 / 648


@dataclass
class RadarImage:
    """The current radar animation."""

    gif: bytes
    # Time of the newest frame (from the server's Last-Modified header)
    updated: datetime


def _unsqueeze(image: Image.Image) -> Image.Image:
    """Return the frame widened to the map's real aspect (height kept)."""
    width = round(image.height * MAP_ASPECT)
    if width == image.width:
        return image
    return image.resize((width, image.height), Image.Resampling.LANCZOS)


def build_gif(frames: list[bytes]) -> bytes:
    """Return a looping GIF of the JPEG frames (given oldest first)."""
    images = [
        _unsqueeze(Image.open(io.BytesIO(frame)).convert("RGB")) for frame in frames
    ]
    # One palette for all frames, so the map doesn't flicker between frames
    sheet = Image.new("RGB", (images[0].width, images[0].height * len(images)))
    for i, image in enumerate(images):
        sheet.paste(image, (0, i * images[0].height))
    palette = sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    quantized = [
        image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images
    ]
    buffer = io.BytesIO()
    quantized[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=quantized[1:],
        duration=[FRAME_MS] * (len(images) - 1) + [LAST_FRAME_MS],
        loop=0,
        optimize=True,
        disposal=1,
    )
    return buffer.getvalue()


@dataclass
class Frame:
    """A downloaded frame."""

    data: bytes
    last_modified: str | None


async def fetch_frame(
    session: aiohttp.ClientSession, index: int, if_modified_since: str | None = None
) -> Frame | None:
    """
    Download radar{index}.jpg; return None if unchanged since if_modified_since.

    Raises IdokepApiClientCommunicationError on network/HTTP failure.
    """
    headers = dict(_BROWSER_HEADERS)
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    url = FRAME_URL.format(index=index)
    try:
        async with (
            asyncio.timeout(TIMEOUT),
            session.get(url, headers=headers) as response,
        ):
            if response.status == 304:  # HTTP Not Modified
                return None
            response.raise_for_status()
            return Frame(await response.read(), response.headers.get("Last-Modified"))
    except (TimeoutError, aiohttp.ClientError, socket.gaierror) as exception:
        msg = f"Error fetching {url} - {exception}"
        raise IdokepApiClientCommunicationError(msg) from exception


def _parse_http_date(value: str | None) -> datetime | None:
    """Return an HTTP date header as an aware datetime."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


class RadarCoordinator(DataUpdateCoordinator[RadarImage | None]):
    """Keep the national radar animation up to date."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: Logger,
        config_entry: ConfigEntry,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=logger,
            name="idokep_weather_and_forecast_radar",
            update_interval=None,
            config_entry=config_entry,
        )
        self._session = session
        self._failures = FailureLog(logger, "Radar update")
        # Frames oldest first; the newest frame's Last-Modified value
        self._frames: deque[bytes] = deque(maxlen=FRAME_COUNT)
        self._last_modified: str | None = None
        self._last_new_frame: datetime | None = None
        # True once this 5-minute slot's new frame has arrived (no retries
        # needed until the next slot); start-up already has the latest frame
        self._got_frame_this_slot = True

    async def async_tick(self, now: datetime) -> None:
        """
        Check for a new frame (called every minute at second 59).

        On x:00:59, x:05:59, ... always; on the other minutes only to retry
        while this slot's new frame hasn't arrived yet.
        """
        # Same rules as the forecast pages: honour "Enable polling for
        # updates" and do nothing while the radar entity is disabled
        if self.config_entry.pref_disable_polling or not self._listeners:
            return
        if now.minute % CHECK_EVERY_MINUTES == 0:
            self._got_frame_this_slot = False
        elif self._got_frame_this_slot:
            return
        await self.async_refresh()

    async def _async_update_data(self) -> RadarImage | None:
        """
        Download what changed and rebuild the animation.

        None after a failure: nothing old is shown, and the next try downloads
        all frames again.
        """
        try:
            await self._update_frames()
            image = self.data
            if image is None or image.updated != self._newest_time():
                gif = await self.hass.async_add_executor_job(
                    build_gif, list(self._frames)
                )
                image = RadarImage(gif=gif, updated=self._newest_time())
        # OSError and ValueError: a frame that isn't a readable picture
        except (IdokepApiClientCommunicationError, OSError, ValueError) as exception:
            self._failures.failed(exception)
            self._frames.clear()
            self._last_modified = None
            self._got_frame_this_slot = False
            return None
        self._failures.succeeded()
        return image

    async def _update_frames(self) -> None:
        """Fetch the newest frame if it changed and shift the others along."""
        newest = await fetch_frame(self._session, NEWEST_INDEX, self._last_modified)
        if newest is None:
            return  # unchanged (304)
        if self._frames and newest.data == self._frames[-1]:
            # File rewritten with the same picture: not a new frame
            self._last_modified = newest.last_modified
            return

        now = dt_util.utcnow()
        if self._frames:
            # The previous newest frame is now radar1: just add the new one
            self._frames.append(newest.data)
        else:
            # Start-up or after a failure: get the older frames too
            older = await asyncio.gather(
                *(
                    fetch_frame(self._session, i)
                    for i in range(FRAME_COUNT - 1, NEWEST_INDEX, -1)
                )
            )
            for data in [frame.data for frame in older if frame is not None] + [
                newest.data
            ]:
                # Files still being shifted can repeat a frame: keep it once
                if not self._frames or data != self._frames[-1]:
                    self._frames.append(data)
        self._last_modified = newest.last_modified
        self._last_new_frame = now
        self._got_frame_this_slot = True

    def _newest_time(self) -> datetime:
        """Return the newest frame's time (falls back to when it was fetched)."""
        return (
            _parse_http_date(self._last_modified)
            or self._last_new_frame
            or dt_util.utcnow()
        )
