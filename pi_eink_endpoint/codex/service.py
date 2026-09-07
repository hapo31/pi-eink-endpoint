"""Codex login, quota retrieval, and display coordination."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from pi_eink_endpoint.quota.service import QuotaDisplay

from .client import AppServerClient, AppServerError
from .models import normalize_quota
from .render import renderer


logger = logging.getLogger(__name__)

AUTH_MODES = {"chatgpt", "chatgptAuthTokens"}


class CodexService:
    """Own a Codex client and compose the provider-neutral display policy."""

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
        self.renderer = renderer
        self.display = QuotaDisplay(
            enqueue_image,
            state_path=state_path,
            timezone_name=timezone_name,
            interval=interval,
            prepare=self._prepare_display,
            refresh_quota=self._refresh,
            monotonic=monotonic,
        )
        self._notification_task: asyncio.Task | None = None

    def __getattr__(self, name):
        """Keep status inspection compatible while state is owned by ``display``."""
        return getattr(self.display, name)

    @property
    def display_enabled(self):
        return self.display.display_enabled

    @display_enabled.setter
    def display_enabled(self, value):
        self.display.display_enabled = value

    async def start(self, *, activate=True):
        await self.display.start(activate=activate)

    def snapshot(self):
        return self.display.snapshot()

    def start_display(self):
        return self.display.start_display()

    def refresh(self):
        return self.display.refresh()

    def start_login(self) -> dict:
        if self.display.login_id is None and self.display.status != "starting_login":
            self.display.status = "starting_login"
            self.display.spawn(self._ensure_login())
        return self.snapshot()

    async def _prepare_display(self):
        try:
            account = await self._account()
        except Exception:
            self._record_error("Codex unavailable")
            return
        if account.get("type") in AUTH_MODES:
            self.display.status = "idle"
            self.display.schedule_refresh()
        else:
            await self._ensure_login()

    async def _ensure_login(self):
        display = self.display
        if display.login_id is not None or display.closed:
            return
        stage = "account/read"
        display.status = "starting_login"
        display.last_error = None
        try:
            account = await self._account()
            if account.get("type") in AUTH_MODES:
                display.status = "idle"
                if display.display_enabled:
                    display.schedule_refresh()
                return
            stage = "account/login/start"
            result = await self.client.request("account/login/start", {"type": "chatgptDeviceCode"})
            login_id = result.get("loginId") if isinstance(result, dict) else None
            verification_url = result.get("verificationUrl") if isinstance(result, dict) else None
            user_code = result.get("userCode") if isinstance(result, dict) else None
            if not all(isinstance(value, str) and value for value in (login_id, verification_url, user_code)):
                raise ValueError("invalid login response")
            display.login_id = login_id
            display.verification_url = verification_url
            display.user_code = user_code
            display.status = "awaiting_login"
            self._show_login()
        except Exception as error:
            rpc_code = error.code if isinstance(error, AppServerError) else None
            logger.warning("Codex device-code login failed (stage=%s, error=%s, rpc_code=%s, executable=%r)", stage, type(error).__name__, rpc_code, getattr(self.client, "executable", None))
            display.login_id = None
            display.status = "auth_required"
            self._record_error("Login could not start", login=True)

    async def _account(self) -> dict:
        await self.client.start()
        if self._notification_task is None or self._notification_task.done():
            self._notification_task = self.display.spawn(self._notifications())
        result = await self.client.request("account/read", {"refreshToken": False})
        if not isinstance(result, dict):
            return {}
        account = result.get("account")
        return account if isinstance(account, dict) else {}

    async def _refresh(self):
        display = self.display
        if display.closed or not display.display_enabled:
            return
        display.status = "loading"
        try:
            await self.client.start()
            result = await self.client.request("account/rateLimits/read")
            display.quota = normalize_quota(
                result if isinstance(result, dict) else {}, timezone_name=display.timezone_name
            )
            display.status = "ready"
            display.last_error = None
            self._show_quota()
        except AppServerError:
            # A rejected authenticated request requires a fresh device-code login.
            display.status = "auth_required"
            display.login_id = None
            self._record_error("Login required", login=True)
        except Exception:
            self._record_error("Quota update failed")

    def _record_error(self, message: str, *, login: bool = False):
        display = self.display
        display.last_error = message
        if display.quota is not None:
            display.quota = display.quota.mark_stale()
            display.status = "auth_required" if login else "stale"
            self._show_quota(error=message)
        else:
            display.status = "auth_required" if login else "error"
            self._show_login(error=message)

    def _show_login(self, *, error: str | None = None):
        self.display.show_login(
            self.renderer.render_login(
                self.display.verification_url, self.display.user_code, error=error
            )
        )

    def _show_quota(self, *, error: str | None = None):
        self.display.show_quota(
            self.renderer.render_quota(
                self.display.quota, self.display.timezone_name, error=error
            )
        )

    async def _notifications(self):
        display = self.display
        while not display.closed:
            message = await self.client.notifications.get()
            method, params = message.get("method"), message.get("params") or {}
            if method == "account/login/completed":
                if params.get("loginId") != display.login_id:
                    continue
                display.login_id = None
                if params.get("success") is True:
                    display.verification_url = display.user_code = None
                    display.status = "idle"
                    if display.display_enabled:
                        display.schedule_refresh()
                else:
                    display.status = "auth_required"
                    self._record_error("Login failed", login=True)
            elif method == "account/updated" and params.get("authMode") is None:
                display.login_id = None
                display.status = "auth_required"
            elif method == "client/disconnected" and display.display_enabled:
                self._record_error("Codex disconnected")
                await asyncio.sleep(5)
                if not display.closed:
                    display._begin_display()

    async def close(self):
        self.display.closed = True
        tasks = [task for task in (*self.display.tasks(), self._notification_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()
