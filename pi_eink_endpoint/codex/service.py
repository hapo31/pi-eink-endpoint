"""Stateful login, quota retrieval, and periodic refresh coordination."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Callable

from zoneinfo import ZoneInfo

from .client import AppServerClient, AppServerError
from .models import Quota, normalize_quota
from .render import render_login, render_quota


logger = logging.getLogger(__name__)

AUTH_MODES = {"chatgpt", "chatgptAuthTokens"}


class CodexService:
    """Own one App Server and avoid overlapping quota fetches or login attempts."""

    def __init__(
        self,
        client: AppServerClient,
        enqueue_image: Callable,
        *,
        state_path: Path,
        timezone_name: str = "Asia/Tokyo",
        interval: float = 900,
        monotonic: Callable[[], float] | None = None,
    ):
        self.client = client
        self.enqueue_image = enqueue_image
        self.state_path = Path(state_path)
        self.timezone_name = timezone_name
        self.interval = interval
        self.monotonic = monotonic or __import__("time").monotonic
        self.status = "idle"
        self.display_enabled = False
        self.quota: Quota | None = None
        self.last_error: str | None = None
        self.login_id: str | None = None
        self.verification_url: str | None = None
        self.user_code: str | None = None
        self.next_update_at: datetime | None = None
        self._notification_task: asyncio.Task | None = None
        self._periodic_task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._start_task: asyncio.Task | None = None
        self._closed = False

    async def start(self):
        self.display_enabled = self._load_display_enabled()
        if self.display_enabled:
            self._begin_display()

    def snapshot(self) -> dict:
        quota = self.quota
        window = lambda value: None if value is None else {
            "remaining_percent": value.remaining_percent,
            "resets_at": value.resets_at.isoformat() if value.resets_at else None,
        }
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

    def start_display(self) -> dict:
        if self.display_enabled:
            return self.snapshot()
        self.display_enabled = True
        self._save_display_enabled()
        self._begin_display()
        return self.snapshot()

    def start_login(self) -> dict:
        if self.login_id is None and self.status != "starting_login":
            self.status = "starting_login"
            self._spawn(self._ensure_login())
        return self.snapshot()

    def refresh(self) -> bool:
        if not self.display_enabled or self.login_id is not None or self.status in {"auth_required", "starting_login"}:
            return False
        self._schedule_refresh()
        return True

    def _begin_display(self):
        if self._start_task is None or self._start_task.done():
            self._start_task = self._spawn(self._prepare_display())
        if self._periodic_task is None or self._periodic_task.done():
            self._periodic_task = self._spawn(self._periodic())

    async def _prepare_display(self):
        try:
            account = await self._account()
        except Exception:
            self._record_error("Codex unavailable")
            return
        if account.get("type") in AUTH_MODES:
            self.status = "idle"
            self._schedule_refresh()
        else:
            await self._ensure_login()

    async def _ensure_login(self):
        if self.login_id is not None or self._closed:
            return
        stage = "account/read"
        self.status = "starting_login"
        self.last_error = None
        try:
            account = await self._account()
            if account.get("type") in AUTH_MODES:
                self.status = "idle"
                if self.display_enabled:
                    self._schedule_refresh()
                return
            stage = "account/login/start"
            result = await self.client.request("account/login/start", {"type": "chatgptDeviceCode"})
            login_id = result.get("loginId") if isinstance(result, dict) else None
            verification_url = result.get("verificationUrl") if isinstance(result, dict) else None
            user_code = result.get("userCode") if isinstance(result, dict) else None
            if not all(isinstance(value, str) and value for value in (login_id, verification_url, user_code)):
                raise ValueError("invalid login response")
            self.login_id = login_id
            self.verification_url = verification_url
            self.user_code = user_code
            self.status = "awaiting_login"
            self.enqueue_image(render_login(verification_url, user_code))
        except Exception as error:
            rpc_code = error.code if isinstance(error, AppServerError) else None
            logger.warning("Codex device-code login failed (stage=%s, error=%s, rpc_code=%s, executable=%r)", stage, type(error).__name__, rpc_code, getattr(self.client, "executable", None))
            self.login_id = None
            self.status = "auth_required"
            self._record_error("Login could not start", login=True)

    async def _account(self) -> dict:
        await self.client.start()
        if self._notification_task is None or self._notification_task.done():
            self._notification_task = self._spawn(self._notifications())
        result = await self.client.request("account/read")
        return result if isinstance(result, dict) else {}

    def _schedule_refresh(self):
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._spawn(self._refresh())

    async def _refresh(self):
        if self._closed or not self.display_enabled:
            return
        self.status = "loading"
        try:
            await self.client.start()
            result = await self.client.request("account/rateLimits/read")
            self.quota = normalize_quota(result if isinstance(result, dict) else {}, timezone_name=self.timezone_name)
            self.status = "ready"
            self.last_error = None
            self.enqueue_image(render_quota(self.quota, self.timezone_name))
        except AppServerError:
            # A rejected authenticated request requires a fresh device-code login.
            self.status = "auth_required"
            self.login_id = None
            self._record_error("Login required", login=True)
        except Exception:
            self._record_error("Quota update failed")

    def _record_error(self, message: str, *, login: bool = False):
        self.last_error = message
        if self.quota is not None:
            self.quota = self.quota.mark_stale()
            self.status = "auth_required" if login else "stale"
            self.enqueue_image(render_quota(self.quota, self.timezone_name, error=message))
        else:
            self.status = "auth_required" if login else "error"
            self.enqueue_image(render_login(self.verification_url, self.user_code, error=message))

    async def _periodic(self):
        due = self.monotonic() + self.interval
        self._set_next_update(due)
        while not self._closed and self.display_enabled:
            delay = max(0, due - self.monotonic())
            await asyncio.sleep(delay)
            if self._closed or not self.display_enabled:
                break
            self._schedule_refresh()
            due += self.interval
            now = self.monotonic()
            while due <= now:
                due += self.interval
            self._set_next_update(due)

    def _set_next_update(self, due: float):
        seconds = max(0, due - self.monotonic())
        self.next_update_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).astimezone(
            ZoneInfo(self.timezone_name)
        )

    async def _notifications(self):
        while not self._closed:
            message = await self.client.notifications.get()
            method, params = message.get("method"), message.get("params") or {}
            if method == "account/login/completed":
                if params.get("loginId") != self.login_id:
                    continue
                self.login_id = None
                if params.get("success") is True:
                    self.verification_url = self.user_code = None
                    self.status = "idle"
                    if self.display_enabled:
                        self._schedule_refresh()
                else:
                    self.status = "auth_required"
                    self._record_error("Login failed", login=True)
            elif method == "account/updated" and params.get("authMode") is None:
                self.login_id = None
                self.status = "auth_required"
            elif method == "client/disconnected" and self.display_enabled:
                self._record_error("Codex disconnected")
                await asyncio.sleep(5)
                if not self._closed:
                    self._begin_display()

    async def close(self):
        self._closed = True
        tasks = [task for task in (self._start_task, self._periodic_task, self._refresh_task, self._notification_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()

    def _spawn(self, coroutine):
        return asyncio.create_task(coroutine)

    def _load_display_enabled(self) -> bool:
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
