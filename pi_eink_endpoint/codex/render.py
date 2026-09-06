"""Small, monochrome Codex screens for the 296 x 128 panel."""

from __future__ import annotations

from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

from .models import Quota, Window


DISPLAY_SIZE = (296, 128)
ASSETS = Path(__file__).parents[1] / "assets"


def _font(size: int):
    # Bookworm's fonts-dejavu-core installs this explicitly named font.
    return ImageFont.truetype("DejaVuSans.ttf", size)


def _text(draw: ImageDraw.ImageDraw, xy, value: str, size: int, *, fill=0):
    draw.text(xy, value, font=_font(size), fill=fill)


def _format_reset(window: Window | None) -> str:
    if window is None or window.resets_at is None:
        return "Reset unavailable"
    return "Reset " + window.resets_at.strftime("%m/%d %H:%M")


def _format_percent(window: Window | None) -> str:
    if window is None or window.remaining_percent is None:
        return "--"
    return f"{window.remaining_percent:.0f}%"


def _paste_icon(image: Image.Image):
    # The XBM is deliberately bundled rather than fetched during a display update.
    with Image.open(ASSETS / "codex-icon.xbm") as source:
        icon = source.convert("1")
    image.paste(icon, (6, 4))


def _gauge(draw: ImageDraw.ImageDraw, y: int, label: str, window: Window | None):
    x, width, height = 48, 145, 10
    _text(draw, (6, y - 2), label, 13)
    draw.rectangle((x, y, x + width, y + height), outline=0)
    if window is not None and window.remaining_percent is not None:
        fill_width = round((width - 2) * window.remaining_percent / 100)
        if fill_width:
            draw.rectangle((x + 1, y + 1, x + fill_width, y + height - 1), fill=0)
    _text(draw, (203, y - 3), _format_percent(window), 14)
    _text(draw, (48, y + 11), _format_reset(window), 10)


def render_quota(quota: Quota | None, timezone_name: str, *, error: str | None = None) -> Image.Image:
    """Render a quota snapshot; unavailable values remain visibly unavailable."""
    image = Image.new("1", DISPLAY_SIZE, 1)
    draw = ImageDraw.Draw(image)
    _paste_icon(image)
    _text(draw, (30, 6), "CODEX", 15)
    _text(draw, (245, 7), timezone_name, 9)
    if quota is None:
        _text(draw, (10, 42), error or "Quota unavailable", 16)
        return image
    _gauge(draw, 30, "5h", quota.five_hour)
    _gauge(draw, 70, "Week", quota.weekly)
    resets = "--" if quota.available_resets is None else str(quota.available_resets)
    _text(draw, (6, 109), f"Resets: {resets}", 11)
    updated = quota.fetched_at.strftime("%H:%M")
    prefix = "Failed " if quota.stale else "Updated "
    _text(draw, (150, 109), prefix + updated, 11)
    if error:
        _text(draw, (6, 94), error, 9)
    return image


def render_login(verification_url: str | None, user_code: str | None, *, error: str | None = None) -> Image.Image:
    """Render an integer-module QR code with a readable manual fallback."""
    image = Image.new("1", DISPLAY_SIZE, 1)
    draw = ImageDraw.Draw(image)
    _paste_icon(image)
    _text(draw, (30, 7), "CODEX LOGIN", 14)
    if not verification_url or not user_code:
        _text(draw, (10, 43), error or "Login required", 16)
        return image
    qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(verification_url)
    qr.make(fit=True)
    modules = len(qr.get_matrix())
    box_size = max(1, min(3, 108 // modules))
    qr.box_size = box_size
    qr_image = qr.make_image(fill_color=0, back_color=1).convert("1")
    # Never resize after generation: QR modules stay square integer pixels.
    image.paste(qr_image, (7, 25))
    _text(draw, (124, 33), "Open URL, enter code:", 10)
    _text(draw, (124, 48), user_code, 18)
    _text(draw, (124, 76), "Use another device", 10)
    _text(draw, (124, 89), "if QR cannot scan.", 10)
    # URLs can be too wide, but keeping their beginning makes a manual fallback possible.
    _text(draw, (7, 116), verification_url[:50], 8)
    if error:
        _text(draw, (124, 103), error[:27], 8)
    return image
