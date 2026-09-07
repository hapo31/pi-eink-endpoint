"""Claude Code authentication, quota refresh, and display coordination."""

import asyncio
import logging

from pi_eink_endpoint.quota.render import QuotaScreenRenderer
from pi_eink_endpoint.quota.service import QuotaDisplay

from .client import AuthenticationError
from .models import normalize_quota


logger = logging.getLogger(__name__)


class ClaudeService:
    """Own the Claude client and compose display state and frame rendering."""

    def __init__(self, client, enqueue_image, *, state_path, timezone_name="Asia/Tokyo",
                 interval=900, monotonic=None):
        self.client = client
        self.renderer = QuotaScreenRenderer(title="CLAUDE")
        self.display = QuotaDisplay(
            enqueue_image,
            state_path=state_path,
            timezone_name=timezone_name,
            interval=interval,
            prepare=self._prepare_display,
            refresh_quota=self._refresh,
            monotonic=monotonic,
        )
        self._login_task = None

    def __getattr__(self, name):
        return getattr(self.display, name)

    @property
    def display_enabled(self):
        return self.display.display_enabled

    @display_enabled.setter
    def display_enabled(self, value):
        self.display.display_enabled = value

    async def start(self):
        await self.display.start()

    def snapshot(self):
        return self.display.snapshot()

    def start_display(self):
        return self.display.start_display()

    def refresh(self):
        return self.display.refresh()

    def start_login(self):
        if self.display.login_id is None and self.display.status != "starting_login":
            self.display.status = "starting_login"
            self._login_task = self.display.spawn(self._ensure_login())
        return self.snapshot()

    async def _prepare_display(self):
        try:
            if await self.client.authenticated():
                self.display.status = "idle"
                self.display.schedule_refresh()
            else:
                await self._ensure_login()
        except Exception as error:
            logger.warning("Claude Code status failed (error=%s)", type(error).__name__)
            self._record_error("Claude unavailable")

    async def _ensure_login(self):
        display = self.display
        if display.login_id is not None or display.closed:
            return
        display.status = "starting_login"
        try:
            if await self.client.authenticated():
                display.status = "idle"
                if display.display_enabled:
                    display.schedule_refresh()
                return
            display.login_id = "claude-cli"
            success = await self.client.login(self._login_url)
            display.login_id = None
            display.verification_url = None
            if not success:
                raise AuthenticationError("login failed")
            display.status = "idle"
            if display.display_enabled:
                display.schedule_refresh()
        except Exception as error:
            logger.warning("Claude Code login failed (error=%s, executable=%r)",
                           type(error).__name__, self.client.executable)
            display.login_id = None
            self._record_error("Login could not start", login=True)

    def _login_url(self, url):
        self.display.verification_url = url
        self.display.status = "awaiting_login"
        self._show_login()

    async def _refresh(self):
        display = self.display
        if display.closed or not display.display_enabled:
            return
        display.status = "loading"
        try:
            display.quota = normalize_quota(
                await self.client.usage(), timezone_name=display.timezone_name
            )
            display.status = "ready"
            display.last_error = None
            self._show_quota()
        except AuthenticationError:
            self._record_error("Login required", login=True)
        except Exception as error:
            logger.warning("Claude quota update failed (error=%s)", type(error).__name__)
            self._record_error("Quota update failed")

    def _record_error(self, message, *, login=False):
        display = self.display
        display.last_error = message
        if display.quota is not None:
            display.quota = display.quota.mark_stale()
            display.status = "auth_required" if login else "stale"
            self._show_quota(error=message)
        else:
            display.status = "auth_required" if login else "error"
            self._show_login(error=message)

    def _show_login(self, *, error=None):
        self.display.show_login(self.renderer.render_login(self.display.verification_url, error=error))

    def _show_quota(self, *, error=None):
        self.display.show_quota(self.renderer.render_quota(
            self.display.quota, self.display.timezone_name, error=error
        ))

    async def close(self):
        self.display.closed = True
        tasks = [task for task in (*self.display.tasks(), self._login_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()
