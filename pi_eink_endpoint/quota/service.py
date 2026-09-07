"""Shared state, scheduling, persistence, and panel refresh behavior."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from zoneinfo import ZoneInfo


FULL_REFRESH_EVERY = 4


class QuotaDisplayService:
    """Provider-neutral coordination; subclasses implement auth and retrieval."""

    def _init_display(self, enqueue_image, *, state_path: Path, timezone_name: str,
                      interval: float, monotonic=None):
        self.enqueue_image = enqueue_image
        self.state_path = Path(state_path)
        self.timezone_name = timezone_name
        self.interval = interval
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
        self._quota_refresh_count = 0
        self._force_full_refresh = self._quota_screen_visible = False
        self._closed = False

    async def start(self):
        self.display_enabled = self._load_display_enabled()
        if self.display_enabled:
            self._begin_display()

    def snapshot(self):
        def window(value):
            return None if value is None else {
                "remaining_percent": value.remaining_percent,
                "resets_at": value.resets_at.isoformat() if value.resets_at else None,
            }
        quota = self.quota
        return {"status": self.status, "display_enabled": self.display_enabled,
                "login_pending": self.login_id is not None,
                "quota": None if quota is None else {
                    "five_hour": window(quota.five_hour), "weekly": window(quota.weekly),
                    "available_resets": quota.available_resets,
                    "fetched_at": quota.fetched_at.isoformat(), "stale": quota.stale},
                "last_error": self.last_error,
                "next_update_at": self.next_update_at.isoformat() if self.next_update_at else None,
                "timezone": self.timezone_name}

    def start_display(self):
        if not self.display_enabled:
            self.display_enabled = True
            self._save_display_enabled()
            self._begin_display()
        return self.snapshot()

    def refresh(self):
        if not self.display_enabled or self.login_id is not None or self.status in {"auth_required", "starting_login"}:
            return False
        self._force_full_refresh = True
        self._schedule_refresh()
        return True

    def _begin_display(self):
        if self._start_task is None or self._start_task.done():
            self._start_task = self._spawn(self._prepare_display())
        if self._periodic_task is None or self._periodic_task.done():
            self._periodic_task = self._spawn(self._periodic())

    def _schedule_refresh(self):
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._spawn(self._refresh())

    def _panel_update(self, image):
        full = (not self._quota_screen_visible or self._force_full_refresh or
                self._quota_refresh_count + 1 >= FULL_REFRESH_EVERY)
        self._quota_refresh_count = 0 if full else self._quota_refresh_count + 1
        self._force_full_refresh = False
        self._quota_screen_visible = True
        self.enqueue_image(image, partial=not full)

    async def _periodic(self):
        due = self.monotonic() + self.interval
        self._set_next_update(due)
        while not self._closed and self.display_enabled:
            await asyncio.sleep(max(0, due - self.monotonic()))
            if self._closed or not self.display_enabled:
                break
            self._schedule_refresh()
            due += self.interval
            while due <= self.monotonic():
                due += self.interval
            self._set_next_update(due)

    def _set_next_update(self, due):
        seconds = max(0, due - self.monotonic())
        self.next_update_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).astimezone(ZoneInfo(self.timezone_name))

    def _spawn(self, coroutine):
        return asyncio.create_task(coroutine)

    def _load_display_enabled(self):
        try:
            return json.loads(self.state_path.read_text()).get("display_enabled") is True
        except (OSError, ValueError, AttributeError):
            return False

    def _save_display_enabled(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path.parent.chmod(0o700)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"display_enabled": True}))
        temporary.chmod(0o600)
        temporary.replace(self.state_path)
