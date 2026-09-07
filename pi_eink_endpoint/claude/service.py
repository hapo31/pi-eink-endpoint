"""Claude Code authentication, quota refresh, and display coordination."""

import asyncio
import logging

from pi_eink_endpoint.quota.render import render_login, render_quota
from pi_eink_endpoint.quota.service import QuotaDisplayService

from .client import AuthenticationError
from .models import normalize_quota


logger = logging.getLogger(__name__)


class ClaudeService(QuotaDisplayService):
    def __init__(self, client, enqueue_image, *, state_path, timezone_name="Asia/Tokyo",
                 interval=900, monotonic=None):
        self.client = client
        self._login_task = None
        self._init_display(enqueue_image, state_path=state_path,
                           timezone_name=timezone_name, interval=interval,
                           monotonic=monotonic)

    def start_login(self):
        if self.login_id is None and self.status != "starting_login":
            self.status = "starting_login"
            self._login_task = self._spawn(self._ensure_login())
        return self.snapshot()

    async def _prepare_display(self):
        try:
            if await self.client.authenticated():
                self.status = "idle"
                self._schedule_refresh()
            else:
                await self._ensure_login()
        except Exception as error:
            logger.warning("Claude Code status failed (error=%s)", type(error).__name__)
            self._record_error("Claude unavailable")

    async def _ensure_login(self):
        if self.login_id is not None or self._closed:
            return
        self.status = "starting_login"
        try:
            if await self.client.authenticated():
                self.status = "idle"
                if self.display_enabled:
                    self._schedule_refresh()
                return
            self.login_id = "claude-cli"
            success = await self.client.login(self._login_url)
            self.login_id = None
            self.verification_url = None
            if not success:
                raise AuthenticationError("login failed")
            self.status = "idle"
            if self.display_enabled:
                self._schedule_refresh()
        except Exception as error:
            logger.warning("Claude Code login failed (error=%s, executable=%r)",
                           type(error).__name__, self.client.executable)
            self.login_id = None
            self._record_error("Login could not start", login=True)

    def _login_url(self, url):
        self.verification_url = url
        self.status = "awaiting_login"
        self._show_login()

    async def _refresh(self):
        if self._closed or not self.display_enabled:
            return
        self.status = "loading"
        try:
            self.quota = normalize_quota(await self.client.usage(), timezone_name=self.timezone_name)
            self.status = "ready"
            self.last_error = None
            self._show_quota()
        except AuthenticationError:
            self._record_error("Login required", login=True)
        except Exception as error:
            logger.warning("Claude quota update failed (error=%s)", type(error).__name__)
            self._record_error("Quota update failed")

    def _record_error(self, message, *, login=False):
        self.last_error = message
        if self.quota:
            self.quota = self.quota.mark_stale()
            self.status = "auth_required" if login else "stale"
            self._show_quota(error=message)
        else:
            self.status = "auth_required" if login else "error"
            self._show_login(error=message)

    def _show_login(self, *, error=None):
        self._quota_screen_visible = False
        self._quota_refresh_count = 0
        self._force_full_refresh = False
        self.enqueue_image(render_login(self.verification_url, title="CLAUDE", error=error), partial=False)

    def _show_quota(self, *, error=None):
        self._panel_update(render_quota(self.quota, self.timezone_name,
                                        title="CLAUDE", error=error))

    async def close(self):
        self._closed = True
        tasks = [task for task in (self._start_task, self._periodic_task,
                 self._refresh_task, self._login_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()
