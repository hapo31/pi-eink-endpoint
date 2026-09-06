import asyncio
from pathlib import Path
import tempfile
import unittest

from pi_eink_endpoint.codex.client import AppServerError
from pi_eink_endpoint.codex.render import render_login, render_quota
from pi_eink_endpoint.codex.service import CodexService


class FakeClient:
    def __init__(self):
        self.notifications = asyncio.Queue()
        self.auth_type = None
        self.login_error = None
        self.calls = []
        self.closed = False

    async def start(self):
        self.calls.append(("start", None))

    async def request(self, method, params=None):
        self.calls.append((method, params))
        if method == "account/read":
            account = None if self.auth_type is None else {"type": self.auth_type}
            return {"account": account, "requiresOpenaiAuth": True}
        if method == "account/login/start":
            if self.login_error:
                raise self.login_error
            return {
                "loginId": "current-login",
                "verificationUrl": "https://auth.openai.com/codex/device",
                "userCode": "ABCD-1234",
            }
        if method == "account/rateLimits/read":
            return {
                "rateLimits": {
                    "primary": {"windowDurationMins": 300, "usedPercent": 25, "resetsAt": 1},
                    "secondary": {"windowDurationMins": 10080, "usedPercent": 50, "resetsAt": 2},
                },
                "rateLimitResetCredits": {"availableCount": 0},
            }
        raise AssertionError(method)

    async def close(self):
        self.closed = True


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = FakeClient()
        self.images = []
        self.service = CodexService(
            self.client,
            self.images.append,
            state_path=Path(self.temp.name) / "state.json",
            interval=3600,
        )
        self.addAsyncCleanup(self.service.close)

    async def test_display_login_completion_and_quota_refresh(self):
        self.assertFalse(self.service.display_enabled)
        self.assertFalse(self.client.calls)
        state = self.service.start_display()
        self.assertTrue(state["display_enabled"])
        self.assertTrue((Path(self.temp.name) / "state.json").exists())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(self.service.status, "awaiting_login")
        self.assertEqual(self.service.login_id, "current-login")
        self.assertEqual(self.images[-1].size, (296, 128))
        self.assertFalse(self.service.refresh())

        await self.client.notifications.put({
            "method": "account/login/completed", "params": {"loginId": "old", "success": True}
        })
        await asyncio.sleep(0)
        self.assertEqual(self.service.login_id, "current-login")

        self.client.auth_type = "chatgpt"
        await self.client.notifications.put({
            "method": "account/login/completed", "params": {"loginId": "current-login", "success": True}
        })
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(self.service.status, "ready")
        self.assertEqual(self.service.quota.five_hour.remaining_percent, 75)
        self.assertEqual(self.service.quota.available_resets, 0)
        status = self.service.snapshot()
        self.assertIsNotNone(status["next_update_at"])
        self.assertNotIn("next_update_monotonic", status)

    async def test_login_failure_logs_safe_diagnostic_metadata(self):
        self.client.login_error = AppServerError(-32001)
        with self.assertLogs("pi_eink_endpoint.codex.service", level="WARNING") as logs:
            state = self.service.start_login()
            self.assertEqual(state["status"], "starting_login")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        self.assertEqual(self.service.status, "auth_required")
        output = "\n".join(logs.output)
        self.assertIn("stage=account/login/start", output)
        self.assertIn("rpc_code=-32001", output)
        self.assertNotIn("ABCD-1234", output)

    async def test_login_only_never_starts_periodic_refresh(self):
        state = self.service.start_login()
        self.assertEqual(state["status"], "starting_login")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(self.service.display_enabled)
        self.assertIsNone(self.service._periodic_task)
        self.assertEqual(self.service.status, "awaiting_login")
        self.assertIn(("account/read", {"refreshToken": False}), self.client.calls)


class RenderTests(unittest.TestCase):
    def test_login_and_quota_screens_are_monochrome_panel_sized(self):
        login = render_login("https://auth.openai.com/codex/device", "ABCD-1234")
        quota = render_quota(None, "Asia/Tokyo", error="Quota unavailable")
        for image in (login, quota):
            self.assertEqual(image.mode, "1")
            self.assertEqual(image.size, (296, 128))
            self.assertIn(0, set(image.get_flattened_data()))
            self.assertTrue(set(image.get_flattened_data()) <= {0, 1, 255})

    def test_login_qr_has_distinct_black_and_white_modules(self):
        login = render_login("https://auth.openai.com/codex/device", "ABCD-1234")
        qr_area = login.crop((7, 25, 106, 124))
        self.assertEqual(set(qr_area.get_flattened_data()), {0, 255})
