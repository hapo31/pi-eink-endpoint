"""Stateful login, quota retrieval, and periodic refresh coordination."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from .client import AppServerClient, AppServerError
from .models import normalize_quota
from .render import render_login, render_quota
from pi_eink_endpoint.quota.service import QuotaDisplayService

logger = logging.getLogger(__name__)

AUTH_MODES = {"chatgpt", "chatgptAuthTokens"}

class CodexService(QuotaDisplayService):
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
        self._init_display(
            enqueue_image, state_path=state_path, timezone_name=timezone_name,
            interval=interval, monotonic=monotonic,
        )
        self._notification_task: asyncio.Task | None = None

    def start_login(self) -> dict:
        if self.login_id is None and self.status != "starting_login":
            self.status = "starting_login"
            self._spawn(self._ensure_login())
        return self.snapshot()

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
            self._show_login()
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
        result = await self.client.request("account/read", {"refreshToken": False})
        if not isinstance(result, dict):
            return {}
        account = result.get("account")
        return account if isinstance(account, dict) else {}

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
            self._show_quota()
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
            self._show_quota(error=message)
        else:
            self.status = "auth_required" if login else "error"
            self._show_login(error=message)

    def _show_login(self, *, error: str | None = None):
        self._quota_screen_visible = False
        self._quota_refresh_count = 0
        self._force_full_refresh = False
        self.enqueue_image(render_login(self.verification_url, self.user_code, error=error), partial=False)

    def _show_quota(self, *, error: str | None = None):
        self._panel_update(render_quota(self.quota, self.timezone_name, error=error))

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
