import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pi_eink_endpoint.codex.client import AppServerError
from pi_eink_endpoint.codex.render import render_login, render_quota
from pi_eink_endpoint.codex.service import CodexService
from pi_eink_endpoint.quota.controller import ActiveDisplayController
from pi_eink_endpoint.quota.service import QuotaDisplay


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
            self._enqueue_image,
            state_path=Path(self.temp.name) / "state.json",
            interval=3600,
        )
        self.addAsyncCleanup(self.service.close)

    def _enqueue_image(self, image, *, partial=False):
        self.images.append((image, partial))

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
        self.assertEqual(self.images[-1][0].size, (296, 128))
        self.assertFalse(self.images[-1][1])
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
        self.assertFalse(self.images[-1][1])
        await self.service._refresh()
        self.assertTrue(self.images[-1][1])
        status = self.service.snapshot()
        self.assertIsNotNone(status["next_update_at"])
        self.assertNotIn("next_update_monotonic", status)

    async def test_quota_is_fully_refreshed_every_fourth_update(self):
        self.service.display_enabled = True
        for _ in range(5):
            await self.service._refresh()
        self.assertEqual([partial for _, partial in self.images], [False, True, True, True, False])

    async def test_refresh_forces_a_full_update(self):
        self.service.display_enabled = True
        await self.service._refresh()
        await self.service._refresh()
        self.assertTrue(self.images[-1][1])

        self.assertTrue(self.service.refresh())
        await self.service._refresh_task
        self.assertFalse(self.images[-1][1])

    async def test_refresh_enables_and_keeps_periodic_updates_running(self):
        self.service.display.interval = 0.01

        self.assertTrue(self.service.refresh())
        await self.service._refresh_task
        initial_rate_limit_calls = self.client.calls.count(
            ("account/rateLimits/read", None)
        )
        await asyncio.sleep(0.03)

        self.assertTrue(self.service.display_enabled)
        self.assertTrue((Path(self.temp.name) / "state.json").exists())
        self.assertGreater(
            self.client.calls.count(("account/rateLimits/read", None)),
            initial_rate_limit_calls,
        )
        self.assertIsNotNone(self.service.next_update_at)

    async def test_refresh_recovers_after_authentication_state_changes(self):
        self.service.display_enabled = True
        self.service.display.status = "auth_required"
        self.client.auth_type = "chatgpt"

        self.assertTrue(self.service.refresh())
        await self.service._refresh_task

        self.assertEqual(self.service.status, "ready")
        self.assertEqual(self.service.quota.five_hour.remaining_percent, 75)

    def test_black_to_white_transition_uses_base_refresh(self):
        images = []

        async def no_op():
            pass

        display = QuotaDisplay(
            lambda image, *, partial=False: images.append(partial),
            state_path=Path(self.temp.name) / "transition-state.json",
            timezone_name="Asia/Tokyo",
            interval=3600,
            prepare=no_op,
            refresh_quota=no_op,
        )
        white = Image.new("1", (296, 128), 1)
        black = white.copy()
        black.putpixel((60, 30), 0)

        display.show_quota(white)  # Initial base frame.
        display.show_quota(black)  # White-to-black is safe for partial refresh.
        display.show_quota(white)  # Clean the black-to-white transition.

        self.assertEqual(images, [False, True, False])

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


class ActiveDisplayControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.images = []
        self.controller = ActiveDisplayController(
            Path(self.temp.name) / "active-display.json"
        )
        self.controller.set_enqueue_image(
            lambda image, *, partial=False: self.images.append((image, partial))
        )
        self.codex = self._service("codex")
        self.claude = self._service("claude")
        self.controller.register("codex", self.codex)
        self.controller.register("claude", self.claude)

    def _service(self, name):
        async def no_op():
            pass

        display = QuotaDisplay(
            lambda image, *, partial=False: self.controller.enqueue(
                name, image, partial=partial
            ),
            state_path=Path(self.temp.name) / f"{name}.json",
            timezone_name="Asia/Tokyo",
            interval=3600,
            prepare=no_op,
            refresh_quota=no_op,
        )

        class Service:
            def __init__(self, display):
                self.display = display

            @property
            def display_enabled(self):
                return self.display.display_enabled

            async def start(self, *, activate=True):
                await self.display.start(activate=activate)

            def start_display(self):
                return self.display.start_display()

            def start_login(self):
                return self.display.snapshot()

            def refresh(self):
                return self.display.refresh()

        return Service(display)

    async def test_switch_stops_the_other_provider_and_rejects_its_frames(self):
        self.controller.start_display("codex")
        first_codex_periodic = self.codex.display._periodic_task
        self.codex.display.show_quota(Image.new("1", (1, 1), 1))
        self.assertEqual(len(self.images), 1)

        self.controller.start_login("claude")
        self.assertTrue(self.claude.display_enabled)
        self.assertFalse(self.codex.display_enabled)
        self.assertIsNone(self.codex.display.next_update_at)

        self.assertTrue(self.controller.refresh("codex"))
        self.assertTrue(self.codex.display_enabled)
        self.assertFalse(self.claude.display_enabled)
        self.assertIsNot(self.codex.display._periodic_task, first_codex_periodic)
        self.assertFalse(self.codex.display._periodic_task.cancelling())
        self.claude.display.show_quota(Image.new("1", (1, 1), 1))
        self.codex.display.show_quota(Image.new("1", (1, 1), 1))
        self.assertEqual(len(self.images), 2)
        self.assertEqual(
            json.loads((Path(self.temp.name) / "active-display.json").read_text()),
            {"active_provider": "codex"},
        )
        self.codex.display.stop_display()
        await asyncio.sleep(0)


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
