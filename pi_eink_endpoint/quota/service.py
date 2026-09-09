"""Reusable display coordination owned by quota providers.

Providers compose :class:`QuotaDisplay` instead of inheriting its mutable
state. Authentication and quota retrieval remain provider responsibilities;
this object only persists the display preference, schedules callbacks, and
chooses full or partial panel updates.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


FULL_REFRESH_EVERY = 4


def _image_pixels(image):
    """Return pixels across the supported Pillow versions."""
    flattened = getattr(image, "get_flattened_data", None)
    return flattened() if flattened is not None else image.getdata()


class QuotaDisplay:
    """State and panel-update policy shared by one quota provider."""

    def __init__(
        self,
        enqueue_image: Callable[..., Any],
        *,
        state_path: Path,
        timezone_name: str,
        interval: float,
        prepare: Callable[[], Awaitable[None]],
        refresh_quota: Callable[[], Awaitable[None]],
        monotonic: Callable[[], float] | None = None,
    ):
        self.enqueue_image = enqueue_image
        self.state_path = Path(state_path)
        self.timezone_name = timezone_name
        self.interval = interval
        self._prepare = prepare
        self._refresh_quota = refresh_quota
        self.monotonic = monotonic or __import__("time").monotonic
        self.status = "idle"
        self.display_enabled = False
        self.quota = None
        self.last_error = None
        self.login_id = None
        self.verification_url = None
        self.user_code = None
        self.next_update_at = None
        self._periodic_task = self._refresh_task = self._start_task = None
        self._refresh_pending = False
        self._quota_refresh_count = 0
        self._force_full_refresh = self._quota_screen_visible = False
        self._last_quota_image = None
        self.closed = False

    async def start(self, *, activate: bool = True):
        """Load the saved preference and, unless deferred, start its tasks."""
        self.display_enabled = self._load_display_enabled()
        if activate and self.display_enabled:
            self._begin_display()

    def snapshot(self):
        def window(value):
            return None if value is None else {
                "remaining_percent": value.remaining_percent,
                "resets_at": value.resets_at.isoformat() if value.resets_at else None,
            }

        quota = self.quota
        return {
            "status": self.status,
            "display_enabled": self.display_enabled,
            "login_pending": self.login_id is not None,
            "quota": None if quota is None else {
                "five_hour": window(quota.five_hour),
                "weekly": window(quota.weekly),
                "available_resets": quota.available_resets,
                "fetched_at": quota.fetched_at.isoformat(),
                "stale": quota.stale,
            },
            "last_error": self.last_error,
            "next_update_at": self.next_update_at.isoformat() if self.next_update_at else None,
            "timezone": self.timezone_name,
        }

    def start_display(self, *, prepare: bool = True):
        if not self.display_enabled:
            self.display_enabled = True
            self._save_display_enabled()
        if prepare:
            self._begin_display()
        else:
            self._begin_periodic()
        return self.snapshot()

    def stop_display(self):
        """Stop this provider's pending and periodic screen updates."""
        if self.display_enabled:
            self.display_enabled = False
            self._save_display_enabled()
        self.next_update_at = None
        for task in self.tasks():
            if task is not None and not task.done():
                task.cancel()

    def refresh(self):
        if self.login_id is not None or self.status == "starting_login":
            return False
        # A manual refresh is also an explicit choice of this screen.  Keep
        # that choice independently of the HTTP controller so callers of the
        # provider service cannot accidentally perform a one-shot update.
        if not self.display_enabled:
            self.display_enabled = True
            self._save_display_enabled()
        self._begin_periodic()
        self._force_full_refresh = True
        self.schedule_refresh()
        return True

    def schedule_refresh(self):
        # Do not lose a timer or manual refresh that arrives while the previous
        # provider request is still running.  The single runner coalesces such
        # requests and performs one more fetch as soon as the in-flight fetch
        # finishes.
        self._refresh_pending = True
        if not self._task_is_active(self._refresh_task):
            self._refresh_task = self.spawn(self._run_refreshes())

    async def _run_refreshes(self):
        while self._refresh_pending and not self.closed and self.display_enabled:
            self._refresh_pending = False
            await self._refresh_quota()

    def show_login(self, image):
        """Replace the quota frame, so the next quota image is a base frame."""
        self._quota_screen_visible = False
        self._quota_refresh_count = 0
        self._force_full_refresh = False
        self._last_quota_image = None
        self.enqueue_image(image, partial=False)

    def show_quota(self, image):
        """Apply the panel refresh policy to an already-rendered quota frame."""
        full = (not self._quota_screen_visible or self._force_full_refresh or
                self._has_black_to_white_transition(image) or
                self._quota_refresh_count + 1 >= FULL_REFRESH_EVERY)
        self._quota_refresh_count = 0 if full else self._quota_refresh_count + 1
        self._force_full_refresh = False
        self._quota_screen_visible = True
        self._last_quota_image = image.convert("1").copy()
        self.enqueue_image(image, partial=not full)

    def _has_black_to_white_transition(self, image):
        """Return whether a partial waveform would need to erase black pixels.

        The 2.9-inch V3's partial waveform leaves visible residue when pixels
        transition from black to white. Its partial API covers the entire
        panel, so a base refresh is the reliable way to clean affected gauge
        and changing-number areas.
        """
        if self._last_quota_image is None:
            return False
        current = image.convert("1")
        if current.size != self._last_quota_image.size:
            return True
        return any(
            previous == 0 and next_pixel != 0
            for previous, next_pixel in zip(
                _image_pixels(self._last_quota_image), _image_pixels(current)
            )
        )

    def spawn(self, coroutine):
        return asyncio.create_task(coroutine)

    def _begin_display(self):
        if not self._task_is_active(self._start_task):
            self._start_task = self.spawn(self._prepare())
        self._begin_periodic()

    def _begin_periodic(self):
        if not self._task_is_active(self._periodic_task):
            self._periodic_task = self.spawn(self._periodic())

    @staticmethod
    def _task_is_active(task):
        """Treat cancellation requests as stopped before the loop processes them."""
        return task is not None and not task.done() and not task.cancelling()

    async def _periodic(self):
        due = self.monotonic() + self.interval
        self._set_next_update(due)
        while not self.closed and self.display_enabled:
            await asyncio.sleep(max(0, due - self.monotonic()))
            if self.closed or not self.display_enabled:
                break
            self.schedule_refresh()
            due += self.interval
            while due <= self.monotonic():
                due += self.interval
            self._set_next_update(due)

    def _set_next_update(self, due):
        seconds = max(0, due - self.monotonic())
        self.next_update_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).astimezone(
            ZoneInfo(self.timezone_name)
        )

    def tasks(self):
        return (self._start_task, self._periodic_task, self._refresh_task)

    def _load_display_enabled(self):
        try:
            return json.loads(self.state_path.read_text()).get("display_enabled") is True
        except (OSError, ValueError, AttributeError):
            return False

    def _save_display_enabled(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path.parent.chmod(0o700)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"display_enabled": self.display_enabled}))
        temporary.chmod(0o600)
        temporary.replace(self.state_path)
