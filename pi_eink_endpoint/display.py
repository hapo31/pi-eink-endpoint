"""Waveshare e-ink driver adapter.

The HTTP application owns queueing; this component owns the hardware protocol
and image conversion required by a single EPD driver.
"""

from __future__ import annotations

import io
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageOps


class WaveshareDisplay:
    """Compose an EPD factory with the supported display update modes."""

    def __init__(self, epd_factory: Callable):
        self._epd_factory = epd_factory

    def show_text(self, data: dict):
        epd = self._epd_factory()
        epd.init()
        image = Image.new("1", (epd.height, epd.width), 255)
        ImageDraw.Draw(image).text((10, 10), data.get("text", "Hello E-ink"), fill=0)
        epd.display(epd.getbuffer(image))
        epd.sleep()
        return {"message": "E-ink display updated", "data": data}

    def show_image(self, binary_image: bytes):
        epd = self._epd_factory()
        epd.init()
        source = Image.open(io.BytesIO(binary_image)).convert("L")
        # The driver rotates landscape images 90 degrees into panel coordinates.
        display_size = (epd.height, epd.width)
        fitted = ImageOps.contain(source, display_size, Image.Resampling.LANCZOS)
        image = Image.new("L", display_size, 255)
        image.paste(fitted, ((image.width - fitted.width) // 2, (image.height - fitted.height) // 2))
        image = image.point(lambda value: (0x00, 0x80, 0xC0, 0xFF)[value * 4 // 256])
        epd.Init_4Gray()
        epd.display_4Gray(epd.getbuffer_4Gray(image))
        epd.sleep()
        return {"message": "E-ink display updated from image"}

    def show_monochrome(self, image: Image.Image, *, partial: bool = False):
        epd = self._epd_factory()
        epd.init()
        buffer = epd.getbuffer(image.convert("1"))
        if partial:
            epd.display_Partial(buffer)
        else:
            # Populate both controller buffers before any later partial refreshes.
            epd.display_Base(buffer)
        epd.sleep()
        return {"message": "E-ink monochrome display updated", "partial": partial}
