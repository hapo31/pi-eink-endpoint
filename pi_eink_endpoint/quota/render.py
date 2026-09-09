"""Composable monochrome quota screens for the 296 x 128 panel."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import textwrap

import qrcode
from PIL import Image, ImageDraw, ImageFont

from .models import Quota, Window


DISPLAY_SIZE = (296, 128)
ASSETS = Path(__file__).parents[1] / "assets"


@dataclass(frozen=True)
class ScreenBrand:
    """Provider-specific decoration for otherwise provider-neutral frames."""

    title: str
    icon_name: str | None = None


class MonochromeCanvas:
    """Own the Pillow drawing driver for one panel-sized image."""

    def __init__(self, brand: ScreenBrand):
        self.brand = brand
        self.image = Image.new("1", DISPLAY_SIZE, 1)
        self.draw = ImageDraw.Draw(self.image)
        self._paste_icon()

    def text(self, xy, value: str, size: int, *, fill=0):
        self.draw.text(xy, value, font=ImageFont.truetype("DejaVuSans.ttf", size), fill=fill)

    def title(self, suffix=""):
        x = 30 if self.brand.icon_name else 6
        self.text((x, 6), self.brand.title + suffix, 15)

    def _paste_icon(self):
        if self.brand.icon_name is None:
            return
        # The XBM is deliberately bundled rather than fetched during a display update.
        with Image.open(ASSETS / self.brand.icon_name) as source:
            self.image.paste(source.convert("1"), (6, 4))


class QuotaFrame:
    """Draw the common quota frame into a supplied monochrome canvas."""

    def render(self, canvas: MonochromeCanvas, quota: Quota | None, timezone_name: str,
               *, error: str | None = None):
        canvas.title()
        canvas.text((245, 7), timezone_name, 9)
        if quota is None:
            canvas.text((10, 42), error or "Quota unavailable", 16)
            return
        self._gauge(canvas, 30, "5h", quota.five_hour)
        self._gauge(canvas, 70, "Week", quota.weekly)
        resets = "--" if quota.available_resets is None else str(quota.available_resets)
        canvas.text((6, 109), f"Resets: {resets}", 11)
        prefix = "Failed " if quota.stale else "Updated "
        canvas.text((150, 109), prefix + quota.fetched_at.strftime("%H:%M"), 11)
        if error:
            canvas.text((6, 94), error, 9)

    def _gauge(self, canvas: MonochromeCanvas, y: int, label: str, window: Window | None):
        x, width, height = 48, 145, 10
        canvas.text((6, y - 2), label, 13)
        canvas.draw.rectangle((x, y, x + width, y + height), outline=0)
        if window is not None and window.remaining_percent is not None:
            fill_width = round((width - 2) * window.remaining_percent / 100)
            if fill_width:
                canvas.draw.rectangle((x + 1, y + 1, x + fill_width, y + height - 1), fill=0)
        canvas.text((203, y - 3), self._format_percent(window), 14)
        canvas.text((48, y + 11), self._format_reset(window), 10)

    @staticmethod
    def _format_reset(window: Window | None) -> str:
        if window is None or window.resets_at is None:
            return "Reset unavailable"
        return "Reset " + window.resets_at.strftime("%m/%d %H:%M")

    @staticmethod
    def _format_percent(window: Window | None) -> str:
        if window is None or window.remaining_percent is None:
            return "--"
        return f"{window.remaining_percent:.0f}%"


class LoginFrame:
    """Draw the common QR-code login frame into a supplied canvas."""

    def render(self, canvas: MonochromeCanvas, verification_url: str | None,
               user_code: str | None = None, *, error: str | None = None):
        canvas.title(" LOGIN")
        if not verification_url:
            canvas.text((10, 43), error or "Login required", 16)
            return
        qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(verification_url)
        qr.make(fit=True)
        modules = len(qr.get_matrix())
        qr.box_size = max(1, min(3, 102 // modules))
        # Never resize after generation: QR modules stay square integer pixels.
        canvas.image.paste(qr.make_image(fill_color="black", back_color="white").convert("1"), (7, 25))
        manual_url = verification_url.removeprefix("https://").removeprefix("http://")
        canvas.text((124, 28), "Or open this URL:", 11)
        lines = textwrap.wrap(manual_url, width=25, break_long_words=True,
                              break_on_hyphens=False)
        for index, line in enumerate(lines[:3]):
            canvas.text((124, 43 + index * 12), line, 10)
        canvas.text((124, 82), "Then enter code:" if user_code else "Complete login", 10)
        if user_code:
            canvas.text((124, 96), user_code, 18)
        if error:
            canvas.text((124, 110), error[:27], 8)


class QuotaScreenRenderer:
    """Compose the drawing driver with independently reusable screen frames."""

    def __init__(self, *, title="QUOTA", icon_name=None,
                 quota_frame: QuotaFrame | None = None, login_frame: LoginFrame | None = None):
        self.brand = ScreenBrand(title, icon_name)
        self.quota_frame = quota_frame or QuotaFrame()
        self.login_frame = login_frame or LoginFrame()

    def render_quota(self, quota: Quota | None, timezone_name: str, *, error: str | None = None):
        canvas = MonochromeCanvas(self.brand)
        self.quota_frame.render(canvas, quota, timezone_name, error=error)
        return canvas.image

    def render_login(self, verification_url: str | None, user_code: str | None = None,
                     *, error: str | None = None):
        canvas = MonochromeCanvas(self.brand)
        self.login_frame.render(canvas, verification_url, user_code, error=error)
        return canvas.image


# These function adapters retain the original shared rendering API for callers that need it.
def render_quota(quota: Quota | None, timezone_name: str, *, title="QUOTA", icon_name=None,
                 error: str | None = None):
    return QuotaScreenRenderer(title=title, icon_name=icon_name).render_quota(
        quota, timezone_name, error=error
    )


def render_login(verification_url: str | None, user_code: str | None = None, *, title="LOGIN",
                 icon_name=None, error: str | None = None):
    return QuotaScreenRenderer(title=title, icon_name=icon_name).render_login(
        verification_url, user_code, error=error
    )
