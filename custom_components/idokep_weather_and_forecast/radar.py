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
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp
from PIL import Image

from .api import _BROWSER_HEADERS, IdokepApiClientCommunicationError

if TYPE_CHECKING:
    from datetime import datetime

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
